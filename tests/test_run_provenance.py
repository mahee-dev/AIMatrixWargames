"""T-053 immutable run provenance and post-game accounting."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from agents.model_client import OpenRouterClient
from agents.umpire import AIUmpire
from app import export as export_module, game_service
from app import provenance, scenarios
from app.api import create_app
from app.config import Settings
from app.database import session_scope
from app.models import Game, GameEvent, GameStatus
from app.summary import build_game_summary, format_game_summary
from tests.api_helpers import FIXTURE_DIR




def _game(engine: Engine):
    loaded = scenarios.load_scenario(FIXTURE_DIR)
    return scenarios.create_game_from_scenario(engine, loaded)


def _configuration(*, commit: str, assignment_id: str = "assignment-1") -> dict:
    return {
        "player": {
            "prompt_version": "player-v1.2",
            "prompt_hash": "a" * 64,
            "agent_framework": "pydantic-ai",
            "agent_framework_version": "1.2.3",
            "tool_schema_version": "player-tools-v1",
            "assignments": {"arkania": "model/a", "belvar": "model/b", "cestia": "model/c"},
        },
        "umpire": {
            "model": "model/u",
            "provider": "openrouter",
            "prompt_version": "umpire-v1.2",
            "prompt_hash": "b" * 64,
            "settings": {"temperature": 0.0, "reasoning_effort": "low"},
        },
        "code_version": commit,
        "benchmark": {
            "mode": "model",
            "assignment_id": assignment_id,
            "repetition_index": 2,
        },
    }


def test_run_manifest_is_complete_immutable_and_distinguishes_games(db_engine: Engine) -> None:
    first = _game(db_engine)
    second = _game(db_engine)

    one = provenance.create_run_manifest(
        db_engine, first.game_id, configuration=_configuration(commit="commit-one")
    )
    replay = provenance.create_run_manifest(
        db_engine, first.game_id, configuration=_configuration(commit="commit-one")
    )
    two = provenance.create_run_manifest(
        db_engine,
        second.game_id,
        configuration=_configuration(commit="commit-two", assignment_id="assignment-2"),
    )

    assert replay.manifest_sha256 == one.manifest_sha256
    assert one.manifest_sha256 != two.manifest_sha256
    assert one.manifest["game_id"] == str(first.game_id)
    assert one.manifest["scenario"] == {
        "id": "deterministic-game",
        "version": "1",
        "content_sha256": one.manifest["scenario"]["content_sha256"],
        "resource_manifest_sha256": one.manifest["scenario"]["resource_manifest_sha256"],
        "resources": one.manifest["scenario"]["resources"],
    }
    assert len(one.manifest["scenario"]["content_sha256"]) == 64
    assert all(len(r["content_sha256"]) == 64 for r in one.manifest["scenario"]["resources"])
    assert one.manifest["seed"] == 424242

    with pytest.raises(provenance.ManifestConflict):
        provenance.create_run_manifest(
            db_engine, first.game_id, configuration=_configuration(commit="changed")
        )
    unsafe = _configuration(commit="unsafe")
    unsafe["umpire"]["api_key"] = "must-not-persist"
    with pytest.raises(provenance.ProvenanceError, match="credential"):
        provenance.create_run_manifest(
            db_engine, second.game_id, configuration=unsafe
        )


def test_agent_calls_export_and_summary_cover_role_phase_tokens_cost_and_time(
    db_engine: Engine,
) -> None:
    creation = _game(db_engine)
    provenance.create_run_manifest(
        db_engine, creation.game_id, configuration=_configuration(commit="commit-one")
    )
    start = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
    with session_scope(db_engine) as session:
        session.add_all(
            [
                GameEvent(game_id=creation.game_id, turn=1, type="game_started", created_at=start),
                GameEvent(
                    game_id=creation.game_id,
                    turn=1,
                    type="phase_started",
                    payload={"phase": "diplomacy"},
                    created_at=start,
                ),
                GameEvent(
                    game_id=creation.game_id,
                    turn=1,
                    type="phase_started",
                    payload={"phase": "argument"},
                    created_at=start + timedelta(minutes=14),
                ),
                GameEvent(
                    game_id=creation.game_id,
                    turn=1,
                    type="phase_started",
                    payload={"phase": "adjudication"},
                    created_at=start + timedelta(minutes=25),
                ),
                GameEvent(
                    game_id=creation.game_id,
                    turn=1,
                    type="game_completed",
                    created_at=start + timedelta(minutes=32),
                ),
            ]
        )

    common = {
        "model_returned": "model/snapshot",
        "provider": "openrouter",
        "prompt_version": "player-v1.2",
        "prompt_hash": "a" * 64,
        "model_parameters": {"temperature": 0},
        "reasoning_configuration": {"effort": "low"},
        "agent_framework": "pydantic-ai",
        "agent_framework_version": "1.2.3",
        "tool_schema_version": "player-tools-v1",
        "completed": True,
    }
    provenance.record_agent_run(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        role="player",
        turn=1,
        phase="diplomacy",
        model_requested="model/a",
        input_tokens=1000,
        output_tokens=100,
        model_calls=3,
        tool_calls=5,
        invalid_tool_calls=1,
        stalls=0,
        cost_usd=0.12,
        latency_ms=120_000,
        activation_wall_ms=180_000,
        **common,
    )
    provenance.record_agent_run(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        role="player",
        turn=1,
        phase="argument",
        model_requested="model/a",
        input_tokens=2000,
        output_tokens=200,
        model_calls=2,
        tool_calls=2,
        invalid_tool_calls=0,
        stalls=0,
        cost_usd=None,
        latency_ms=60_000,
        activation_wall_ms=75_000,
        **common,
    )
    provenance.record_agent_run(
        db_engine,
        creation.game_id,
        actor_id=None,
        role="umpire",
        turn=1,
        phase="adjudication",
        model_requested="model/u",
        input_tokens=3000,
        output_tokens=300,
        model_calls=1,
        tool_calls=0,
        invalid_tool_calls=0,
        stalls=0,
        cost_usd=0.34,
        latency_ms=90_000,
        activation_wall_ms=95_000,
        **{**common, "prompt_version": "umpire-v1.2", "prompt_hash": "b" * 64,
           "agent_framework": "httpx"},
    )

    summary = build_game_summary(db_engine, creation.game_id)
    assert summary["total_runtime_ms"] == 32 * 60_000
    assert summary["provider_wait_ms"] == 270_000
    assert summary["activation_wall_ms"] == 350_000
    assert summary["player_model_calls"] == 5
    assert summary["umpire_calls"] == 1
    assert summary["by_phase"]["diplomacy"] == {
        "runtime_ms": 14 * 60_000,
        "activation_wall_ms": 180_000,
        "provider_wait_ms": 120_000,
        "activations": 1,
        "model_calls": 3,
        "input_tokens": 1000,
        "output_tokens": 100,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "tool_calls": 5,
    }
    assert summary["by_phase"]["argument"]["model_calls"] == 2
    assert summary["by_phase"]["argument"]["runtime_ms"] == 11 * 60_000
    assert summary["by_phase"]["adjudication"]["model_calls"] == 1
    assert summary["by_phase"]["adjudication"]["runtime_ms"] == 7 * 60_000
    assert summary["activations"][0] == {
        "agent_run_id": 1,
        "activation_id": None,
        "actor_id": "arkania",
        "role": "player",
        "turn": 1,
        "phase": "diplomacy",
        "activation_wall_ms": 180_000,
        "provider_wait_ms": 120_000,
        "model_calls": 3,
        "input_tokens": 1000,
        "output_tokens": 100,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "tool_calls": 5,
        "invalid_tool_calls": 1,
        "invalid_tool_call_breakdown": {},
        "request_topology_available": False,
        "measured_request_bytes": 0,
        "completed": True,
    }
    assert summary["invalid_tool_calls"] == {
        "total": 1,
        "classified": 0,
        "unclassified": 1,
        "counts_by_category": {},
        "counts_by_reason": {},
    }
    assert summary["input_tokens"] == 6000
    assert summary["output_tokens"] == 600
    assert summary["reasoning_tokens"] == 0
    assert summary["cache_read_tokens"] == 0
    assert summary["cache_write_tokens"] == 0
    assert summary["estimated_cost_usd"] == pytest.approx(0.46)
    assert summary["cost_coverage"] == {"calls_with_cost": 4, "total_calls": 6, "complete": False}

    rendered = format_game_summary(summary)
    assert "Total runtime 32m 00s" in rendered
    assert "Provider wait 4m 30s" in rendered
    assert "Activation wall time 5m 50s" in rendered
    assert "Player model calls 5" in rendered
    assert "Umpire calls 1" in rendered
    assert "Diplomacy 1 activations / 3 model calls / 5 tool calls" in rendered
    assert "Estimated cost $0.460000 (partial: 4/6 calls)" in rendered

    exported = export_module.build_game_export(db_engine, creation.game_id)
    assert exported["run_manifest"]["manifest_sha256"]
    assert len(exported["agent_runs"]) == 3
    assert exported["run_summary"] == summary
    assert "token" not in json.dumps(exported["run_manifest"]).lower()


def test_cost_is_unavailable_instead_of_fabricated_when_provider_reports_none(
    db_engine: Engine,
) -> None:
    creation = _game(db_engine)
    provenance.record_agent_run(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        role="player",
        turn=1,
        phase="diplomacy",
        model_requested="model/a",
        model_returned=None,
        provider=None,
        prompt_version="player-v1.2",
        prompt_hash="a" * 64,
        model_parameters={},
        reasoning_configuration=None,
        agent_framework="pydantic-ai",
        agent_framework_version=None,
        tool_schema_version="player-tools-v1",
        input_tokens=12,
        output_tokens=3,
        model_calls=1,
        tool_calls=0,
        invalid_tool_calls=0,
        stalls=0,
        cost_usd=None,
        latency_ms=None,
        completed=False,
        failure_reason="provider timeout",
    )
    summary = build_game_summary(db_engine, creation.game_id)
    assert summary["estimated_cost_usd"] is None
    assert summary["cost_coverage"]["complete"] is False
    assert "Estimated cost unavailable" in format_game_summary(summary)


def test_admin_advance_persists_umpire_provider_call(db_engine: Engine) -> None:
    creation = _game(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    for actor_id in ("arkania", "belvar", "cestia"):
        game_service.ready(db_engine, creation.game_id, actor_id=actor_id)
    argument = game_service.make_argument(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        action="Act",
        desired_effect="Effect",
        pros=["Pro"],
    )
    for actor_id in ("belvar", "cestia"):
        game_service.pass_con(
            db_engine, creation.game_id, argument_id=argument.id, actor_id=actor_id
        )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test/umpire-snapshot",
                "provider": "TestProvider",
                "choices": [{
                    "message": {"content": json.dumps({
                        "mode": "automatic_success",
                        "probability": None,
                        "rationale": "Clear.",
                        "success_effects": ["Done."],
                        "failure_effects": [],
                        "success_supersedes_effect_ids": [],
                        "failure_supersedes_effect_ids": [],
                    })},
                    "finish_reason": "stop",
                }],
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {
                        "cached_tokens": 50,
                        "cache_write_tokens": 10,
                    },
                    "completion_tokens_details": {"reasoning_tokens": 7},
                    "cost": 0.001,
                },
            },
        )

    umpire = AIUmpire(
        OpenRouterClient(api_key="test", transport=httpx.MockTransport(handler)),
        model="test/umpire",
    )
    app = create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN="admin", _env_file=None),
        umpire=umpire,
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/admin/games/{creation.game_id}/advance",
        headers={"Authorization": "Bearer admin"},
        json={},
    )
    assert response.status_code == 200, response.text

    exported = export_module.build_game_export(db_engine, creation.game_id)
    [run] = exported["agent_runs"]
    assert run["role"] == "umpire"
    assert run["phase"] == "adjudication"
    assert run["model_requested"] == "test/umpire"
    assert run["model_returned"] == "test/umpire-snapshot"
    assert run["provider"] == "TestProvider"
    assert run["input_tokens"] == 80
    assert run["output_tokens"] == 20
    assert run["reasoning_tokens"] == 7
    assert run["cache_read_tokens"] == 50
    assert run["cache_write_tokens"] == 10
    assert run["cost_usd"] == pytest.approx(0.001)
    assert exported["run_summary"]["umpire_calls"] == 1


def test_admin_advance_normalizes_inconsistent_umpire_usage_subsets(
    db_engine: Engine,
) -> None:
    creation = _game(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    for actor_id in ("arkania", "belvar", "cestia"):
        game_service.ready(db_engine, creation.game_id, actor_id=actor_id)
    argument = game_service.make_argument(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        action="Act",
        desired_effect="Effect",
        pros=["Pro"],
    )
    for actor_id in ("belvar", "cestia"):
        game_service.pass_con(
            db_engine, creation.game_id, argument_id=argument.id, actor_id=actor_id
        )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "test/umpire-snapshot",
                "provider": "TestProvider",
                "choices": [{
                    "message": {"content": json.dumps({
                        "mode": "automatic_success",
                        "probability": None,
                        "rationale": "Clear.",
                        "success_effects": ["Done."],
                        "failure_effects": [],
                        "success_supersedes_effect_ids": [],
                        "failure_supersedes_effect_ids": [],
                    })},
                    "finish_reason": "stop",
                }],
                # Seen in the overnight run: a routed provider reported a reasoning subset larger
                # than its completion-token parent. The ledger's parent bucket must remain
                # inclusive without throwing away the reported reasoning usage.
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 3,
                    "completion_tokens_details": {"reasoning_tokens": 7},
                },
            },
        )

    umpire = AIUmpire(
        OpenRouterClient(api_key="test", transport=httpx.MockTransport(handler)),
        model="test/umpire",
    )
    app = create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN="admin", _env_file=None),
        umpire=umpire,
    )
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/admin/games/{creation.game_id}/advance",
        headers={"Authorization": "Bearer admin"},
        json={},
    )
    assert response.status_code == 200, response.text

    [run] = export_module.build_game_export(db_engine, creation.game_id)["agent_runs"]
    assert run["output_tokens"] == 7
    assert run["reasoning_tokens"] == 7
    assert run["provider_metadata"]["usage_normalization"] == {
        "output_tokens_reported": 3,
        "output_tokens_recorded": 7,
        "reasoning_tokens_reported": 7,
    }


def test_admin_advance_records_unusable_umpire_attempts_and_returns_503(
    db_engine: Engine,
) -> None:
    creation = _game(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    for actor_id in ("arkania", "belvar", "cestia"):
        game_service.ready(db_engine, creation.game_id, actor_id=actor_id)
    argument = game_service.make_argument(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        action="Act",
        desired_effect="Effect",
        pros=["Pro"],
    )
    for actor_id in ("belvar", "cestia"):
        game_service.pass_con(
            db_engine, creation.game_id, argument_id=argument.id, actor_id=actor_id
        )

    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "model": "test/umpire-snapshot",
                "provider": "TestProvider",
                "choices": [{
                    "message": {"content": "not a JSON object"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 80, "completion_tokens": 4},
            },
        )

    umpire = AIUmpire(
        OpenRouterClient(api_key="test", transport=httpx.MockTransport(handler)),
        model="test/umpire",
    )
    app = create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN="admin", _env_file=None),
        umpire=umpire,
    )
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/admin/games/{creation.game_id}/advance",
        headers={"Authorization": "Bearer admin"},
        json={},
    )

    assert calls == 4  # the D-063 bound: every unusable attempt is made and recorded
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "adjudicator_unavailable"
    exported = export_module.build_game_export(db_engine, creation.game_id)
    assert exported["game"]["status"] == "paused"
    assert exported["adjudications"] == []
    assert len(exported["agent_runs"]) == 4
    assert all(run["role"] == "umpire" for run in exported["agent_runs"])
    assert all(run["completed"] is False for run in exported["agent_runs"])
    assert all(
        "Umpire returned no usable JSON object" in run["failure_reason"]
        for run in exported["agent_runs"]
    )
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None
        assert game.status is GameStatus.paused
        assert game.rng_draw_index == 0


def test_i074_006_admin_advance_records_semantic_retry_then_success(
    db_engine: Engine,
) -> None:
    creation = _game(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    for actor_id in ("arkania", "belvar", "cestia"):
        game_service.ready(db_engine, creation.game_id, actor_id=actor_id)
    argument = game_service.make_argument(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        action="Act",
        desired_effect="Effect",
        pros=["Pro"],
    )
    for actor_id in ("belvar", "cestia"):
        game_service.pass_con(
            db_engine, creation.game_id, argument_id=argument.id, actor_id=actor_id
        )

    requests: list[dict] = []
    invalid_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            output = {
                "mode": "automatic_success",
                "probability": None,
                "rationale": "Replace an earlier effect.",
                "success_effects": ["Done."],
                "failure_effects": [],
                "success_supersedes_effect_ids": [str(invalid_id)],
                "failure_supersedes_effect_ids": [],
            }
            provider = "Alibaba"
            generation_id = "gen-i074-invalid"
        else:
            output = {
                "mode": "automatic_success",
                "probability": None,
                "rationale": "Clear.",
                "success_effects": ["Done."],
                "failure_effects": [],
                "success_supersedes_effect_ids": [],
                "failure_supersedes_effect_ids": [],
            }
            provider = "DeepInfra"
            generation_id = "gen-i074-valid"
        return httpx.Response(
            200,
            json={
                "id": generation_id,
                "model": "test/umpire-snapshot",
                "provider": provider,
                "choices": [{
                    "message": {"content": json.dumps(output)},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 80, "completion_tokens": 20},
            },
        )

    umpire = AIUmpire(
        OpenRouterClient(api_key="test", transport=httpx.MockTransport(handler)),
        model="test/umpire",
    )
    app = create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN="admin", _env_file=None),
        umpire=umpire,
    )
    response = TestClient(app, raise_server_exceptions=False).post(
        f"/admin/games/{creation.game_id}/advance",
        headers={"Authorization": "Bearer admin"},
        json={},
    )

    assert response.status_code == 200, response.text
    assert len(requests) == 2
    assert requests[1]["provider"] == {
        "require_parameters": True,
        "ignore": ["Alibaba"],
    }
    exported = export_module.build_game_export(db_engine, creation.game_id)
    assert len(exported["adjudications"]) == 1
    failed, recovered = exported["agent_runs"]
    assert failed["completed"] is False
    assert failed["failure_reason"] == (
        "output:context-invalid supersession reference:finish_reason=stop"
    )
    assert failed["provider"] == "Alibaba"
    assert failed["provider_metadata"]["semantic_failure"] == {
        "code": "context_invalid_supersession",
        "offending_effect_ids": [str(invalid_id)],
    }
    assert failed["provider_metadata"]["generation_id"] == "gen-i074-invalid"
    assert recovered["completed"] is True
    assert recovered["failure_reason"] is None
    assert recovered["provider"] == "DeepInfra"
    assert recovered["provider_metadata"]["generation_id"] == "gen-i074-valid"
