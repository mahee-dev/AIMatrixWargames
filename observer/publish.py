"""The publisher: pushes a game's observer feed to an operator-run relay over HTTPS.

TRD §41.1 (D-122). The relay (its own repository, ``amaw-relay``) serves published games to an
external frontend. This side only reads the game database, through :mod:`observer.feed`, and
sends batches to the relay's ingest contract:

* ``GET  {url}/ingest/games/{id}/cursor`` returns the last ``seq`` the relay holds;
* ``POST {url}/ingest/games/{id}`` with ``{"contract", "snapshot", "items"}`` stores a batch
  and returns the new cursor. Ingest is idempotent on ``(game, seq)``.

The relay holds the cursor, so a restarted publisher resumes where the relay stands. Connections
are outbound only. A transient failure (network, 5xx, 429) is retried with backoff; a refusal
(401, 413, 422) means the configuration or contract is wrong and raises :class:`PublishError`.
Published games carry every Actor's private material, as the admin export does.

Publishing never affects a run: under ``amaw run --publish`` the publisher runs on a daemon
thread, and every error ends the publisher only.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine

from observer import feed as observer

CONTRACT = "amaw-observer-ingest-v1"
log = logging.getLogger("observer.publish")

# Plain HTTP is accepted only to this machine (a relay under development); anything else would
# send the ingest token and every Actor's private material in the clear.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class PublishError(RuntimeError):
    """Publishing cannot go on: the configuration is missing or the relay refused a request."""


@dataclass(frozen=True)
class RelaySettings:
    url: str
    token: str


class _RelayEnv(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AMAW_RELAY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    url: str | None = None
    token: SecretStr | None = None


def relay_settings() -> RelaySettings:
    """``AMAW_RELAY_URL`` and ``AMAW_RELAY_TOKEN`` from the environment or ``.env``."""
    env = _RelayEnv()
    if not env.url or env.token is None or not env.token.get_secret_value():
        raise PublishError(
            "publishing needs AMAW_RELAY_URL and AMAW_RELAY_TOKEN (in .env or the environment)"
        )
    url = env.url.rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
        raise PublishError(f"AMAW_RELAY_URL must use https (got {url!r})")
    return RelaySettings(url=url, token=env.token.get_secret_value())


def _client(settings: RelaySettings) -> httpx.Client:
    return httpx.Client(base_url=settings.url, timeout=httpx.Timeout(30.0, connect=10.0))


class Publisher:
    """Sends one game's feed to the relay, in order, resuming from the relay's cursor."""

    def __init__(
        self,
        engine: Engine,
        game_id: uuid.UUID,
        *,
        client: httpx.Client,
        token: str,
        batch_size: int = 500,
        poll_seconds: float = 2.0,
        retry_seconds: float = 1.0,
        max_retry_seconds: float = 30.0,
    ) -> None:
        self._engine = engine
        self._game_id = game_id
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self._batch_size = batch_size
        self._poll_seconds = poll_seconds
        self._retry_seconds = retry_seconds
        self._max_retry_seconds = max_retry_seconds
        self._cursor: int | None = None
        self._last_snapshot: dict[str, Any] | None = None
        self.sent = 0

    @property
    def cursor(self) -> int | None:
        return self._cursor

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """One request, raising :class:`PublishError` on a refusal; transient errors propagate."""
        response = self._client.request(method, path, headers=self._headers, **kwargs)
        if response.status_code in (401, 403, 404, 413, 422):
            raise PublishError(
                f"relay refused {method} {path}: {response.status_code} {response.text[:200]}"
            )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("cursor"), int):
            raise PublishError(f"relay answered {method} {path} without a cursor")
        return body

    def _resume(self) -> int:
        if self._cursor is None:
            path = f"/ingest/games/{self._game_id}/cursor"
            self._cursor = int(self._request("GET", path)["cursor"])
        return self._cursor

    def push_once(self) -> int:
        """Send the next batch (or a changed snapshot) and return how many items it carried."""
        cursor = self._resume()
        page = observer.feed(self._engine, self._game_id, after=cursor, limit=self._batch_size)
        snapshot = observer.snapshot(self._engine, self._game_id)
        if not page["items"] and snapshot == self._last_snapshot:
            return 0
        body = {"contract": CONTRACT, "snapshot": snapshot, "items": page["items"]}
        answer = self._request("POST", f"/ingest/games/{self._game_id}", json=body)
        self._cursor = int(answer["cursor"])
        self._last_snapshot = snapshot
        self.sent += len(page["items"])
        return len(page["items"])

    def _push_with_retry(self, stop: threading.Event | None, deadline: float | None) -> int | None:
        """``push_once`` through transient failures.

        Returns ``None`` instead of retrying further when ``deadline`` would pass, or, outside a
        drain (no deadline), when ``stop`` is set, so the caller can move on to draining.
        """
        delay = self._retry_seconds
        while True:
            try:
                return self.push_once()
            except PublishError:
                raise
            except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as exc:
                log.warning("relay unavailable (%s); retrying in %.1fs", exc, delay)
            if deadline is not None:
                if time.monotonic() + delay > deadline:
                    return None
                time.sleep(delay)
            elif stop is not None:
                if stop.wait(delay):
                    return None
            else:
                time.sleep(delay)
            delay = min(delay * 2, self._max_retry_seconds)

    def _caught_up(self) -> bool:
        return not observer.feed(self._engine, self._game_id, after=self._cursor or 0, limit=1)[
            "items"
        ]

    def run(
        self,
        *,
        follow: bool = True,
        stop: threading.Event | None = None,
        drain_seconds: float = 30.0,
    ) -> int:
        """Publish until caught up (``follow=False``), or until the game completes and is sent.

        When ``stop`` is set, what the feed already holds is still sent, for up to
        ``drain_seconds``, so the end of a run reaches the relay. Returns the items sent.
        """
        while True:
            if stop is not None and stop.is_set():
                deadline = time.monotonic() + drain_seconds
                while time.monotonic() < deadline:
                    sent = self._push_with_retry(stop, deadline)
                    if sent is None or (sent == 0 and self._caught_up()):
                        break
                return self.sent
            sent = self._push_with_retry(stop, None)
            if sent is None:
                continue
            if sent == self._batch_size:
                continue
            if self._caught_up():
                status = (self._last_snapshot or {}).get("game", {}).get("status")
                if not follow or status == "complete":
                    return self.sent
            if stop is not None:
                stop.wait(self._poll_seconds)
            else:
                time.sleep(self._poll_seconds)


def publish_in_background(
    engine: Engine,
    game_id: Callable[[], uuid.UUID | None],
    *,
    client: httpx.Client,
    token: str,
    poll_seconds: float = 2.0,
    retry_seconds: float = 1.0,
    drain_seconds: float = 30.0,
) -> tuple[threading.Thread, threading.Event]:
    """Start a following publisher on a daemon thread; set the returned event to finish it.

    ``game_id`` returns ``None`` until the game exists. Any error ends the publisher only: it is
    logged and never raised into the caller, so a run is never failed by its publisher.
    """
    stop = threading.Event()

    def target() -> None:
        try:
            target_id = game_id()
            while target_id is None:
                if stop.wait(poll_seconds):
                    return
                target_id = game_id()
            Publisher(
                engine, target_id, client=client, token=token,
                poll_seconds=poll_seconds, retry_seconds=retry_seconds,
            ).run(follow=True, stop=stop, drain_seconds=drain_seconds)
        except Exception:  # noqa: BLE001 - the publisher must never fail the run
            log.exception("publisher stopped")

    thread = threading.Thread(target=target, name="amaw-publish", daemon=True)
    thread.start()
    return thread, stop
