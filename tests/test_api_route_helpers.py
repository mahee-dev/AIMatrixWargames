"""I-126: the request-size middleware and the advance route delegate to two named helpers.

Behaviour is unchanged:
- the 413 message is the same text;
- Starlette's private ``request._body`` is touched in exactly one place;
- a resolution's attempts are recorded with the same overlay whether the Umpire or the
  consequence resolver made them.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.api_admin as api_admin
from app.api import MAX_REQUEST_BODY_BYTES, create_app

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_i126_001_body_limit_and_message_are_unchanged(db_engine) -> None:  # noqa: ANN001
    assert MAX_REQUEST_BODY_BYTES == 65536
    client = TestClient(create_app(engine=db_engine))
    response = client.post("/admin/games", content=b"x" * (MAX_REQUEST_BODY_BYTES + 1))
    assert response.status_code == 413
    assert response.json()["error"]["message"] == "Request body exceeds 65536 bytes."


def test_i126_002_private_body_is_restored_in_one_place_only() -> None:
    uses = [
        f"{path.name}:{number}"
        for path in APP_DIR.glob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\._body\s*=", line)
    ]
    assert len(uses) == 1, uses
    assert uses[0].startswith("api.py:")
    source = (APP_DIR / "api.py").read_text(encoding="utf-8")
    helper = source[source.index("def _replay_consumed_body"):]
    assert "request._body = body" in helper.split("\ndef ", 1)[0]


def test_i126_003_attempts_are_overlaid_only_when_the_server_failed_a_usable_proposal(
    monkeypatch,  # noqa: ANN001
) -> None:
    recorded: list[dict] = []
    monkeypatch.setattr(
        api_admin.provenance, "record_agent_run",
        lambda engine, game_id, **values: recorded.append(values),
    )
    attempts = ({"completed": True, "failure_reason": None, "model_requested": "m"},)
    game_id = uuid.uuid4()

    usable = SimpleNamespace(attempts=attempts, proposal=object(), failure=None)
    api_admin._record_resolution_attempts(  # noqa: SLF001
        None, game_id, usable, ValueError("rejected"), role="consequence", turn=2,
        phase="consequences",
    )
    assert recorded[-1] == {
        "actor_id": None, "role": "consequence", "turn": 2, "phase": "consequences",
        "completed": False, "failure_reason": "ValueError", "model_requested": "m",
    }

    diagnosed = SimpleNamespace(attempts=attempts, proposal=object(), failure="output:bad")
    api_admin._record_resolution_attempts(  # noqa: SLF001
        None, game_id, diagnosed, RuntimeError(), role="umpire", turn=1, phase="adjudication",
    )
    assert recorded[-1]["failure_reason"] == "output:bad"

    no_proposal = SimpleNamespace(attempts=attempts, proposal=None, failure="provider:x")
    api_admin._record_resolution_attempts(  # noqa: SLF001
        None, game_id, no_proposal, RuntimeError(), role="umpire", turn=1, phase="adjudication",
    )
    assert recorded[-1]["completed"] is True  # the provider attempt's own record, unchanged

    api_admin._record_resolution_attempts(  # noqa: SLF001
        None, game_id, None, None, role="umpire", turn=1, phase="adjudication",
    )
    assert len(recorded) == 3
