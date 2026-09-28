"""Runtime configuration and safe logging (T-002, WBS 1.1.2).

Configuration is environment-driven (TRD §36: "environment variables for provider
secrets"). Secrets are held as ``SecretStr`` so they never render in a repr, a model dump
or a log record, and a logging redaction filter scrubs any that reach the log stream anyway
(TRD §36 "safe logging"; testing.md §24 "Do not persist API keys or bearer tokens").

This module lives in ``app/`` and imports no agent framework (TRD §4).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from amaw_contracts import SEARCH_CONTRACT_DEFAULT
from app.game_loop import GameLoopCondition


class Settings(BaseSettings):
    """Central settings object, loaded from the environment (and an optional ``.env``).

    The field → environment-variable mapping is fixed by ``.env.example``:
    ``OPENROUTER_API_KEY`` has no prefix; the operational settings carry the ``AMAW_``
    prefix. Explicit ``validation_alias`` values pin those names. Defaults are documented
    here and match ``.env.example``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Provider secret. Optional: layers 1-3 of the test suite run without it (AGENTS.md §7).
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )

    # Separate admin/facilitator token for administrative endpoints (api.md §2.4, TRD §16).
    # A Seat token must never authorize an admin endpoint; when this is unset the admin
    # surface is closed. Held as SecretStr so it never renders in a repr or log.
    admin_token: SecretStr | None = Field(
        default=None, validation_alias="AMAW_ADMIN_TOKEN"
    )

    # Operational settings (AMAW_-prefixed in the environment).
    database_path: Path = Field(
        default=Path("data/amaw.db"), validation_alias="AMAW_DATABASE_PATH"
    )
    log_level: str = Field(default="INFO", validation_alias="AMAW_LOG_LEVEL")
    log_file: Path | None = Field(default=None, validation_alias="AMAW_LOG_FILE")
    request_timeout_seconds: float = Field(
        default=60.0, validation_alias="AMAW_REQUEST_TIMEOUT_SECONDS"
    )
    # Gate-2 autonomous smoke defaults. Model selection remains explicit per PlayerConfig and
    # AIUmpire construction; these settings provide one documented fixed condition for T-044.
    player_model: str = Field(
        default="deepseek/deepseek-v4-flash-0731",
        validation_alias="AMAW_PLAYER_MODEL",
    )
    umpire_model: str = Field(
        default="deepseek/deepseek-v4-flash-0731",
        validation_alias="AMAW_UMPIRE_MODEL",
    )
    # I-028/I-029: bounded autonomous orchestration. Values of 1 retain pool-free serial paths.
    diplomacy_concurrency: int = Field(
        default=4, ge=1, validation_alias="AMAW_DIPLOMACY_CONCURRENCY"
    )
    # I-053: explicit game-loop treatment. Open Diplomacy is the released default; a bound is
    # meaningful (and required) only for the experimental bounded condition.
    diplomacy_mode: Literal["open_diplomacy", "bounded_rounds"] = Field(
        default="open_diplomacy", validation_alias="AMAW_DIPLOMACY_MODE"
    )
    diplomacy_rounds_per_turn: int | None = Field(
        default=None, validation_alias="AMAW_DIPLOMACY_ROUNDS_PER_TURN"
    )
    # I-055 / D-053: explicit Con-opposition treatment. Unrestricted opposition is the released
    # default; a challenger count is meaningful (and required) only for ``sampled_sealed``.
    con_mode: Literal["open_cons", "sampled_sealed"] = Field(
        default="open_cons", validation_alias="AMAW_CON_MODE"
    )
    con_challengers_per_argument: int | None = Field(
        default=None, validation_alias="AMAW_CON_CHALLENGERS_PER_ARGUMENT"
    )
    # I-046 / D-060: explicit Con-reply treatment. Sealed Cons (Umpire-only, no reply) is the
    # released default; the reply bound (1–3) is meaningful and required only for
    # ``visible_reply``. The Umpire prompt version is its own explicit condition: unset means
    # ``umpire-v1.7`` for every game (D-114). ``visible_reply`` cannot run under v1.3 and a
    # scenario that declares ``rules`` needs a rules-aware prompt; older versions stay selectable
    # to reproduce earlier runs.
    con_reply_mode: Literal["sealed_cons", "visible_reply"] = Field(
        default="sealed_cons", validation_alias="AMAW_CON_REPLY_MODE"
    )
    reply_max_points: int | None = Field(default=None, validation_alias="AMAW_REPLY_MAX_POINTS")
    umpire_prompt_version: str | None = Field(
        default=None, validation_alias="AMAW_UMPIRE_PROMPT_VERSION"
    )
    # T-074 / D-091: the Consequence condition. Unset defers to the scenario's
    # ``game.consequence_mode`` (default ``none``); ``bounded_effects_v1`` needs a resolver model.
    consequence_mode: Literal["none", "bounded_effects_v1"] | None = Field(
        default=None, validation_alias="AMAW_CONSEQUENCE_MODE"
    )
    consequence_model: str | None = Field(default=None, validation_alias="AMAW_CONSEQUENCE_MODEL")
    # I-029: how many opponents of one Argument the coordinator activates at once in Cons.
    # ``1`` is the serial sweep. Wall time only — Cons are Umpire-only, so behaviour is unchanged.
    cons_concurrency: int = Field(default=4, ge=1, validation_alias="AMAW_CONS_CONCURRENCY")
    # I-031: how the reference player drives one activation — ``single_shot`` (one structured
    # decision, research tools callable) or ``tool_loop`` (one mutating tool per model call).
    # A benchmark condition; recorded in the run manifest. Values are owned by ``agents.player``;
    # ``app/`` only carries the string (it imports no agent framework — AGENTS.md §6).
    player_activation_mode: Literal["single_shot", "tool_loop"] = Field(
        default="single_shot", validation_alias="AMAW_PLAYER_ACTIVATION_MODE"
    )
    # I-057 post-release harness treatments. Defaults preserve the released player condition.
    player_research_mode: Literal["sequential-v1", "batched-v1"] = Field(
        default="sequential-v1", validation_alias="AMAW_PLAYER_RESEARCH_MODE"
    )
    # D-084: Resource ranking is held separately from research request topology. I-112/D-088
    # adopted the evaluated candidate as the released default; `search-v1` stays selectable.
    player_search_contract: Literal["search-v1", "search-v2"] = Field(
        default=SEARCH_CONTRACT_DEFAULT, validation_alias="AMAW_PLAYER_SEARCH_CONTRACT"
    )
    player_research_history_mode: Literal["full-v1", "compact-success-v1"] = Field(
        default="full-v1", validation_alias="AMAW_PLAYER_RESEARCH_HISTORY_MODE"
    )
    # I-092 / D-079: the mutating tools a ``tool_loop`` model is offered — ``broad`` (the whole
    # vocabulary) or ``available_only`` (what ``available_actions`` currently lists). Sealed
    # beside the activation mode; inert under ``single_shot``. Values owned by ``agents.player``.
    player_tool_surface: Literal["broad", "available_only"] = Field(
        default="broad", validation_alias="AMAW_PLAYER_TOOL_SURFACE"
    )
    # I-032: path to a declared ``inference-profile-v1`` document (per-phase reasoning effort /
    # completion budget). ``None`` is the released ``uniform`` behaviour. ``app/`` only carries
    # the path; ``agents.player`` validates the document.
    inference_profile: Path | None = Field(
        default=None, validation_alias="AMAW_INFERENCE_PROFILE"
    )
    # I-033: normal outgoing-Message budget for one Actor activation. Zero disables outgoing
    # Messages without disabling research, memory updates or required completion actions.
    max_messages_per_activation: int = Field(
        default=3, ge=0, validation_alias="AMAW_MAX_MESSAGES_PER_ACTIVATION"
    )
    # Agent guardrails. The recommended defaults remain conservative; unattended runs can raise
    # them explicitly through the environment or the CLI while retaining finite recovery bounds.
    max_llm_iterations: int = Field(
        default=12, ge=1, validation_alias="AMAW_MAX_LLM_ITERATIONS"
    )
    max_tool_calls: int = Field(default=20, ge=1, validation_alias="AMAW_MAX_TOOL_CALLS")
    max_research_tool_calls: int = Field(
        default=4, ge=0, validation_alias="AMAW_MAX_RESEARCH_TOOL_CALLS"
    )
    # I-098 / D-078: Resource operations per activation, independent of how many calls carry them.
    max_research_operations: int = Field(
        default=4, ge=0, validation_alias="AMAW_MAX_RESEARCH_OPERATIONS"
    )
    max_messages_per_phase: int = Field(
        default=10, ge=0, validation_alias="AMAW_MAX_MESSAGES_PER_PHASE"
    )
    max_activations_per_opportunity: int = Field(
        default=6, ge=1, validation_alias="AMAW_MAX_ACTIVATIONS_PER_OPPORTUNITY"
    )

    @model_validator(mode="after")
    def _validate_game_loop_condition(self) -> "Settings":
        # api.md §14 pairing rules — the one validator (I-060); a ValueError subclass so pydantic
        # reports it as a settings validation failure like before.
        self.game_loop_condition().validate()
        return self

    def game_loop_condition(self) -> GameLoopCondition:
        """The environment-declared Diplomacy / Con / Con-reply condition (I-053/I-055/I-046)."""
        return GameLoopCondition(
            diplomacy_mode=self.diplomacy_mode,
            diplomacy_rounds_per_turn=self.diplomacy_rounds_per_turn,
            con_mode=self.con_mode,
            con_challengers_per_argument=self.con_challengers_per_argument,
            con_reply_mode=self.con_reply_mode,
            reply_max_points=self.reply_max_points,
        )


_REDACTED = "***REDACTED***"
# Bearer-token-shaped substrings: "Bearer <token>" and our opaque Seat tokens.
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{8,})")
_SEAT_TOKEN_RE = re.compile(r"\bseat_[A-Za-z0-9._\-]{8,}\b")


def redact_secrets(text: str, secret_values: list[str] | tuple[str, ...] = ()) -> str:
    """Scrub known secrets and bearer/Seat-token-shaped substrings from ``text``.

    The one redaction path: the log filter below and the benchmark runner's on-disk failure
    records (testing.md §24) both go through it.
    """
    for secret in secret_values:
        if secret and secret in text:
            text = text.replace(secret, _REDACTED)
    text = _BEARER_RE.sub(lambda m: m.group(1) + _REDACTED, text)
    text = _SEAT_TOKEN_RE.sub(_REDACTED, text)
    return text


class SecretRedactingFilter(logging.Filter):
    """Scrub known secrets and bearer-token-shaped substrings from log records.

    Defense in depth: ``SecretStr`` already prevents the configured key from rendering,
    but code may still build a message from a raw string. This filter rewrites the record's
    message so the secret never reaches a handler's output.
    """

    def __init__(self, secret_values: list[str] | None = None) -> None:
        super().__init__()
        self._secrets = [s for s in (secret_values or []) if s]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self._redact(message)
        if redacted != message:
            # Replace the fully-rendered message; drop args so they are not re-interpolated.
            record.msg = redacted
            record.args = None
        return True

    def _redact(self, text: str) -> str:
        return redact_secrets(text, self._secrets)


def configure_logging(settings: Settings) -> None:
    """Set logging and install secret redaction on every handler.

    ``AMAW_LOG_FILE`` adds a timestamped UTF-8 file handler while preserving the existing stream
    handler. Handlers created here are marked so a later application/test configuration can replace
    the prior AMAW file destination without accumulating duplicate writes.
    """
    secret_values: list[str] = []
    if settings.openrouter_api_key is not None:
        secret_values.append(settings.openrouter_api_key.get_secret_value())
    if settings.admin_token is not None:
        secret_values.append(settings.admin_token.get_secret_value())

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    if not root.handlers:
        root.addHandler(logging.StreamHandler())

    for handler in list(root.handlers):
        if getattr(handler, "_amaw_log_file_handler", False):
            root.removeHandler(handler)
            handler.close()

    if settings.log_file is not None:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(settings.log_file, encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        file_handler._amaw_log_file_handler = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    redactor = SecretRedactingFilter(secret_values)
    for handler in root.handlers:
        # Drop a stale redactor from a prior call before adding the fresh one.
        handler.filters = [
            f for f in handler.filters if not isinstance(f, SecretRedactingFilter)
        ]
        handler.addFilter(redactor)
