"""I-065 — the Umpire provenance capability is an explicit seam, not a duck-typed one.

Structural guards for structural-cleanup C5 / review F4 (AGENTS.md §6 rule 2: "OpenRouter
integration lives outside the core game domain"). The row *values* are pinned by the existing
``test_run_provenance.py`` characterisation tests; these tests pin where the knowledge lives.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Protocol, get_type_hints

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from agents.model_client import OpenRouterClient
from agents.umpire import AIUmpire, FakeUmpire
from app import export as export_module
from app import game_service, scenarios
from app.api import create_app
from app.config import Settings
from app.models import AgentRun
from app.umpire import (
    AdjudicationMode,
    AdjudicationProposal,
    RecordedUmpire,
    Umpire,
    UmpireAttemptRecord,
    UmpireProvenanceSource,
)
from tests.api_helpers import FIXTURE_DIR

# The HTTP layer: the application module and its player and admin route modules.
API_SOURCES = sorted((Path(__file__).parent.parent / "app").glob("api*.py"))

# The provider fixture from ``test_run_provenance.py::test_admin_advance_persists_umpire_provider_call``:
# raw OpenRouter/OpenAI-shaped usage with dotted detail paths. The numbers asserted below are
# exactly what the pre-I-065 route persisted for it.
_PROVIDER_USAGE = {
    "prompt_tokens": 80,
    "completion_tokens": 20,
    "prompt_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 10},
    "completion_tokens_details": {"reasoning_tokens": 7},
    "cost": 0.001,
}


def _proposal_body(output: dict) -> dict:
    return {
        "id": "gen-i065",
        "model": "test/umpire-snapshot",
        "provider": "TestProvider",
        "choices": [{"message": {"content": json.dumps(output)}, "finish_reason": "stop"}],
        "usage": dict(_PROVIDER_USAGE),
    }


def _ai_umpire(output: dict) -> AIUmpire:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_proposal_body(output))

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIUmpire(client, model="test/umpire")


def _adjudicating_game(engine: Engine):
    creation = scenarios.create_game_from_scenario(engine, scenarios.load_scenario(FIXTURE_DIR))
    game_service.start_game(engine, creation.game_id)
    for actor_id in ("arkania", "belvar", "cestia"):
        game_service.ready(engine, creation.game_id, actor_id=actor_id)
    argument = game_service.make_argument(
        engine, creation.game_id, actor_id="arkania",
        action="Act", desired_effect="Effect", pros=["Pro"],
    )
    for actor_id in ("belvar", "cestia"):
        game_service.pass_con(engine, creation.game_id, argument_id=argument.id, actor_id=actor_id)
    return creation.game_id


_VALID_OUTPUT = {
    "mode": "automatic_success", "probability": None, "rationale": "Clear.",
    "success_effects": ["Done."], "failure_effects": [],
    "success_supersedes_effect_ids": [], "failure_supersedes_effect_ids": [],
}


# --- 1. the capability is its own Protocol; ``Umpire`` stays the adjudication contract ------


def test_i065_001_provenance_protocol_is_separate_from_umpire() -> None:
    members = {"provenance_configuration", "adjudicate_recorded"}  # I-081: no drain, no "last"
    assert issubclass(UmpireProvenanceSource, Protocol)
    assert getattr(UmpireProvenanceSource, "_is_runtime_protocol", False)
    assert set(UmpireProvenanceSource.__protocol_attrs__) == members
    assert set(Umpire.__protocol_attrs__) == {"adjudicate"}

    class AdjudicateOnly:
        def adjudicate(self, context):  # noqa: ANN001
            return AdjudicationProposal(mode=AdjudicationMode.automatic_success, rationale="r")

    fake = FakeUmpire(
        proposal=AdjudicationProposal(mode=AdjudicationMode.automatic_success, rationale="r")
    )
    ai = _ai_umpire(_VALID_OUTPUT)
    for implementation in (fake, ai):
        assert isinstance(implementation, Umpire)
        assert isinstance(implementation, UmpireProvenanceSource)
    assert isinstance(AdjudicateOnly(), Umpire)
    assert not isinstance(AdjudicateOnly(), UmpireProvenanceSource)


# --- 2. The HTTP layer (``app/api*.py``) knows no provider usage shape or Umpire identity ----


def test_i065_002_app_api_has_no_provider_usage_knowledge() -> None:
    assert len(API_SOURCES) == 3
    advances = []
    for path in API_SOURCES:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert "_usage_integer" not in defined, path.name
        assert "_usage_cost" not in defined, path.name
        # Provider-shaped usage keys and the Umpire's framework/schema identity are nobody's
        # business in ``app/`` — anywhere in the HTTP layer.
        for forbidden in (
            "prompt_tokens", "completion_tokens", "cached_tokens",
            "prompt_tokens_details", "completion_tokens_details", '"httpx"', "umpire-output-v1",
        ):
            assert forbidden not in source, (path.name, forbidden)
        assert not re.search(r"getattr\(\s*umpire", source), path.name
        assert not re.search(r"getattr\(\s*request\.app\.state\.umpire", source), path.name
        advances += [
            ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_admin_advance_game"
        ]
    # The advance route must not inspect or compute any token bucket itself; the canonical
    # names are legitimate elsewhere (they are persisted ``AgentRun`` columns and therefore
    # fields of the admin ``agent-runs`` request body).
    [advance_source] = advances
    for bucket in (
        "input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens",
        "cache_write_tokens", "cost_usd", "costed_model_calls", "usage",
    ):
        assert bucket not in advance_source, bucket


# --- 3. the Umpire boundary emits normalised attempt records, not raw usage -----------------


def test_i065_003_ai_umpire_records_are_attempt_shaped(db_engine: Engine) -> None:
    game_id = _adjudicating_game(db_engine)
    umpire = _ai_umpire(_VALID_OUTPUT)
    recorded = RecordedUmpire(umpire)  # I-081: the adjudication owns its attempt records
    game_service.resolve_adjudication(db_engine, game_id, recorded)

    assert recorded.result is not None
    [record] = recorded.result.attempts
    attempt_keys = set(get_type_hints(UmpireAttemptRecord))
    assert set(record) <= attempt_keys
    assert set(record) <= set(AgentRun.model_fields)
    assert "usage" not in record
    # Server-owned context is not the Umpire's to claim.
    assert not {"actor_id", "role", "turn", "phase"} & set(record)

    assert record["input_tokens"] == 80
    assert record["output_tokens"] == 20
    assert record["reasoning_tokens"] == 7
    assert record["cache_read_tokens"] == 50
    assert record["cache_write_tokens"] == 10
    assert record["cost_usd"] == pytest.approx(0.001)
    assert record["costed_model_calls"] == 1
    assert record["model_calls"] == 1
    assert record["tool_calls"] == 0
    assert record["invalid_tool_calls"] == 0
    assert record["stalls"] == 0
    assert record["activation_wall_ms"] == record["latency_ms"]
    assert record["agent_framework"] == "httpx"
    assert isinstance(record["agent_framework_version"], str)
    assert record["tool_schema_version"] == "umpire-output-v1"
    assert record["completed"] is True
    assert record["failure_reason"] is None
    assert record["provider_metadata"]["generation_id"] == "gen-i065"


# --- 4. a server-side refusal after a successful provider call still overlays the row ------


def test_i065_004_server_side_resolution_error_still_overlays_failure_reason(
    db_engine: Engine,
) -> None:
    game_id = _adjudicating_game(db_engine)
    # Parseable, provider-accepted, but refused by the server's ``_validate_proposal``.
    umpire = _ai_umpire({**_VALID_OUTPUT, "mode": "probabilistic", "probability": 1.1})
    app = create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN="admin", _env_file=None),
        umpire=umpire,
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/admin/games/{game_id}/advance", headers={"Authorization": "Bearer admin"}, json={}
    )
    assert response.status_code == 422, response.text

    exported = export_module.build_game_export(db_engine, game_id)
    [run] = exported["agent_runs"]
    assert run["role"] == "umpire"
    assert run["actor_id"] is None
    assert run["phase"] == "adjudication"
    assert run["input_tokens"] == 80  # the provider call itself succeeded and is kept
    assert run["completed"] is False
    # The Umpire reported no failure of its own, so the server's exception class is the reason.
    assert umpire.last_failure is None
    assert run["failure_reason"] == "InvalidAdjudication"
