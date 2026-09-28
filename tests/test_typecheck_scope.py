"""I-127: the modules next to the I-120 hand-offs are type-checked, and two lax boundaries fail closed.

- The mypy scope in ``pyproject.toml`` names each I-127 module (TEST-I127-001); TEST-I120-001
  keeps mypy passing on whatever that scope is.
- A Player's top-level ``reasoning_effort`` must be one of OpenRouter's effort levels, exactly as
  an inference-profile entry must be (TEST-I127-002).
- The Umpire's ``reasoning_effort`` is held to the same levels where the Umpire is composed, and
  ``amaw run --umpire-reasoning-effort`` refuses an unknown level before anything runs
  (TEST-I129-001/002).
- A run manifest that asks the server to derive ``resume.umpire`` from an Umpire provenance
  configuration without a model is refused, instead of sealing ``model: null`` (TEST-I127-003).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from sqlalchemy import Engine
from sqlmodel import select

import amaw_cli
from agents.model_client import OpenRouterClient
from agents.player import PlayerConfig
from agents.umpire import AIUmpire
from amaw_contracts import REASONING_EFFORTS
from app.api import create_app
from app.database import session_scope
from app.models import RunManifest
from tests.test_provenance_precommitment import ADMIN_TOKEN, FIXTURE_DIR, _admin
from tests.test_run_provenance import _configuration

REPO = Path(__file__).resolve().parents[1]

I127_MODULES = (
    "agents/autonomous.py",
    "benchmark/controls.py",
    "app/provenance.py",
    "agents/player.py",
    "agents/consequence.py",
    "app/api.py",
)


def test_i127_001_mypy_scope_names_the_i120_neighbours() -> None:
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    files = config["tool"]["mypy"]["files"]
    missing = [module for module in I127_MODULES if module not in files]
    assert missing == []


def test_i127_002_player_reasoning_effort_is_an_openrouter_level() -> None:
    for effort in (None, *REASONING_EFFORTS):
        assert PlayerConfig(model="model/p", reasoning_effort=effort).reasoning_effort == effort
    with pytest.raises(ValueError, match="reasoning_effort must be one of"):
        PlayerConfig(model="model/p", reasoning_effort="maximum")


def test_i129_001_umpire_reasoning_effort_is_an_openrouter_level() -> None:
    """The Umpire requests only OpenRouter's effort levels; an unknown one is refused at
    composition, before any provider call."""
    client = OpenRouterClient(
        api_key="sk-test",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    for effort in (None, *REASONING_EFFORTS):
        umpire = AIUmpire(client, model="test/umpire", reasoning_effort=effort)
        requested = umpire._config.extra_body.get("reasoning")
        assert requested == (None if effort is None else {"effort": effort})
    with pytest.raises(ValueError, match="reasoning_effort must be one of"):
        AIUmpire(client, model="test/umpire", reasoning_effort="maximum")


def test_i129_002_cli_refuses_unknown_umpire_reasoning_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``amaw run`` refuses an unknown ``--umpire-reasoning-effort`` with exit 2 before a runner,
    and so a Game, is ever created."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "i129.db"))
    constructed: list[object] = []

    class FakeRunner:
        def __init__(self, *args: object, **kwargs: object) -> None:
            constructed.append(kwargs)

    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)

    result = CliRunner().invoke(
        amaw_cli.app,
        ["run", str(FIXTURE_DIR), "--umpire-reasoning-effort", "maximum"],
    )

    assert result.exit_code == 2, result.output
    assert "reasoning_effort must be one of" in result.output
    assert constructed == []


def test_i127_003_derived_umpire_recipe_requires_an_umpire_model(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AMAW_ADMIN_TOKEN", ADMIN_TOKEN)
    client = TestClient(create_app(engine=db_engine), raise_server_exceptions=False)
    created = client.post("/admin/games", json={"scenario": str(FIXTURE_DIR)}, headers=_admin())
    game_id = created.json()["game_id"]

    configuration = _configuration(commit="no-umpire-model")
    configuration["resume"] = {"schema": "autonomous-resume-v1"}
    del configuration["umpire"]["model"]
    refused = client.post(
        f"/admin/games/{game_id}/run-manifest",
        json={"configuration": configuration},
        headers=_admin(),
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "invalid_request"
    assert "model" in refused.json()["error"]["message"]
    with session_scope(db_engine) as session:
        assert session.exec(select(RunManifest)).all() == []

    control = _configuration(commit="no-umpire-model")
    control["resume"] = {"schema": "autonomous-resume-v1"}
    sealed = client.post(
        f"/admin/games/{game_id}/run-manifest",
        json={"configuration": control},
        headers=_admin(),
    )
    assert sealed.status_code == 201, sealed.text
    with session_scope(db_engine) as session:
        record = session.exec(select(RunManifest)).one()
        assert record.manifest["resume"]["umpire"]["model"] == "model/u"
