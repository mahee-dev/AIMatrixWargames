"""I-081 — execution provenance is invocation-owned and durable across sibling failure.

Player side (agents.md §18 "Every failed activation is recorded", §19; TRD §28/§35; testing.md
§20 "agent failures are recorded"): a completed sibling's outcome is persisted the moment it
reaches the coordinator, so an *unexpected* exception elsewhere in the same sweep — a worker
defect, ``ActivationLimitExceeded``, a failed settlement — never leaves committed Con/Message
state without its ``AgentRun``. Umpire side (AGENTS.md §6.2, D-065 → D-072): each adjudication
call returns its own immutable result carrying exactly the attempts that produced it; nothing
is queued on the shared app-level Umpire for "whoever drains next".
"""

from __future__ import annotations

import dataclasses
import json
import threading
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.model_client import OpenRouterClient
from agents.player import PlayerConfig, ReferencePlayer
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from agents.umpire import AIUmpire
from app import export as export_module
from app.api import create_app
from app.config import Settings
from app.umpire import (
    AdjudicationContext,
    AdjudicationMode,
    UmpireAdjudication,
    UmpireUnavailable,
)
from tests.agent_fakes import default_policy, model_from_policy
from tests.api_helpers import FIXTURE_DIR

ACTORS = ("arkania", "belvar", "cestia")
ADMIN_TOKEN = "admin-secret-token"
SEED = 20260919


def _client(db_engine: Engine) -> TestClient:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    return TestClient(app, raise_server_exceptions=False)


def _factory(runner_cls: type[LifecycleRunner]):
    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model_from_policy(default_policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
        )
        return runner_cls(player)

    return player_factory


def _player_runs(db_engine: Engine, game_id: str) -> list[dict[str, Any]]:
    exported = export_module.build_game_export(db_engine, uuid.UUID(game_id))
    return [run for run in exported["agent_runs"] if run["role"] == "player"]


# --- TEST-I081-001: a concurrent Cons sibling's success outlives another sibling's defect ----


def test_i081_001_completed_concurrent_sibling_is_persisted_before_unexpected_failure(
    db_engine: Engine,
) -> None:
    """``cons_concurrency=3``: cestia's worker raises only after belvar's Con has committed.

    The coordinator must still surface the defect as ``AutonomousGameError`` — but belvar's
    activation reached the coordinator first, and its ``AgentRun`` must already be durable when
    the error propagates. Before I-081 the whole sweep's outcomes were dropped with the error.
    """
    belvar_done = threading.Event()

    class SiblingDefect(LifecycleRunner):
        def step(self, context=None):
            context = context or self.observe()
            if context.get("phase") != "cons" or not context.get("requires_input"):
                return super().step(context)
            actor = self.player.actor_id
            if actor == "belvar":
                try:
                    return super().step(context)
                finally:
                    belvar_done.set()
            assert belvar_done.wait(timeout=10), "belvar's Con never committed"
            raise RuntimeError("cestia's worker blew up after its sibling committed")

    runner = AutonomousGameRunner(
        _client(db_engine),
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=_factory(SiblingDefect),
        seed=SEED,
        cons_concurrency=3,
    )
    with pytest.raises(AutonomousGameError, match="Actor 'cestia'.*blew up after its sibling"):
        runner.run()
    assert runner.last_game_id is not None

    exported = export_module.build_game_export(db_engine, uuid.UUID(runner.last_game_id))
    cons = [con for con in exported["cons"] if con["actor_id"] == "belvar"]
    assert cons, "belvar's Con is committed game state"
    belvar_cons_runs = [
        run for run in exported["agent_runs"]
        if run["role"] == "player" and run["actor_id"] == "belvar" and run["phase"] == "cons"
    ]
    assert len(belvar_cons_runs) == 1, exported["agent_runs"]
    [run] = belvar_cons_runs
    assert run["completed"] is True
    assert run["activation_id"]
    assert run["turn"] == 1
    # Nothing is fabricated for the Actor whose worker never produced an outcome.
    assert not [
        run for run in exported["agent_runs"]
        if run["actor_id"] == "cestia" and run["phase"] == "cons"
    ]


# --- TEST-I081-002: a diplomacy round's activations outlive a failed settlement --------------


class _FailingAdminCall:
    """An HTTP client that fails one admin path; everything else reaches the real app."""

    def __init__(self, client: TestClient, *, failing_suffix: str) -> None:
        self._client = client
        self._failing_suffix = failing_suffix
        self.failed_calls = 0

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        if url.endswith(self._failing_suffix):
            self.failed_calls += 1
            return _Failure()
        return self._client.request(method, url, **kwargs)


class _Failure:
    status_code = 500
    is_success = False

    @staticmethod
    def json() -> dict[str, Any]:
        return {"error": {"code": "injected_failure", "message": "settlement refused"}}


def test_i081_002_diplomacy_round_activations_are_persisted_before_settlement_failure(
    db_engine: Engine,
) -> None:
    """The round's ``…/diplomacy-rounds/complete`` call fails after all three Actors acted.

    The error propagates as the admin failure it is, and the three diplomacy activations — the
    public Messages they committed are game state — already have their ``AgentRun`` rows.
    """
    client = _FailingAdminCall(_client(db_engine), failing_suffix="/diplomacy-rounds/complete")
    runner = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=_factory(LifecycleRunner),
        seed=SEED,
        diplomacy_concurrency=3,
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=1,
    )
    with pytest.raises(AutonomousGameError, match="admin POST .*diplomacy-rounds/complete"):
        runner.run()
    assert client.failed_calls == 1
    assert runner.last_game_id is not None

    exported = export_module.build_game_export(db_engine, uuid.UUID(runner.last_game_id))
    assert len([m for m in exported["messages"] if m["public"]]) == len(ACTORS)
    diplomacy_runs = [
        run for run in _player_runs(db_engine, runner.last_game_id) if run["phase"] == "diplomacy"
    ]
    assert sorted(run["actor_id"] for run in diplomacy_runs) == sorted(ACTORS)
    assert all(run["completed"] for run in diplomacy_runs)


# --- TEST-I081-003: a serial sweep records each step before the next Actor can fail ---------


def test_i081_003_serial_sweep_persists_earlier_steps_before_a_later_backstop(
    db_engine: Engine,
) -> None:
    """``cons_concurrency=1``: cestia's absolute activation backstop trips in Cons, after belvar
    (earlier in the sweep) has already committed its Con. The backstop propagates unchanged; the
    committed Con has its provenance."""
    from agents.lifecycle import ActivationLimitExceeded

    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model_from_policy(default_policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
        )
        # cestia's one diplomacy activation exhausts its backstop; its Cons step trips it.
        return LifecycleRunner(player, max_activations=1 if actor_id == "cestia" else 500)

    runner = AutonomousGameRunner(
        _client(db_engine),
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=player_factory,
        seed=SEED,
        cons_concurrency=1,
    )
    with pytest.raises(ActivationLimitExceeded, match="'cestia'"):
        runner.run()
    assert runner.last_game_id is not None

    exported = export_module.build_game_export(db_engine, uuid.UUID(runner.last_game_id))
    assert [con["actor_id"] for con in exported["cons"]] == ["belvar"]
    belvar_cons = [
        run for run in _player_runs(db_engine, runner.last_game_id)
        if run["actor_id"] == "belvar" and run["phase"] == "cons"
    ]
    assert len(belvar_cons) == 1 and belvar_cons[0]["completed"] is True


# --- TEST-I081-004: an adjudication call owns its own immutable, correlated result ----------


_GOOD_OUTPUT = {"mode": "automatic_success", "rationale": "Clear.", "success_effects": ["Done."]}


def _context(turn: int, action: str = "Act") -> AdjudicationContext:
    return AdjudicationContext(
        scenario_summary="A regional standoff.",
        turn=turn,
        turn_length="2 weeks",
        argument={"actor_id": "arkania", "action": action, "desired_effect": "E", "pros": ["P"]},
        cons=[],
        relevant_effects=[],
    )


def _completion(generation_id: str, output: dict | None = _GOOD_OUTPUT, *, status: int = 200):
    if output is None:
        return httpx.Response(status, json={"error": {"message": "unavailable"}})
    return httpx.Response(200, json={
        "id": generation_id,
        "model": "test/umpire",
        "provider": "TestProvider",
        "choices": [
            {"message": {"role": "assistant", "content": json.dumps(output)}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    })


def _requested_action(request: httpx.Request) -> str:
    """The Argument action the rendered Umpire user message names — the call's identity."""
    body = json.loads(request.content)
    user = next(m["content"] for m in body["messages"] if m["role"] == "user")
    for token in ("alpha", "beta"):
        if f"action-{token}" in user:
            return token
    raise AssertionError("request carries neither call's Argument")


def test_i081_004_adjudicate_recorded_returns_the_calls_own_immutable_result() -> None:
    both_in_flight = threading.Barrier(2, timeout=10)

    def handler(request: httpx.Request) -> httpx.Response:
        token = _requested_action(request)
        both_in_flight.wait()  # neither call completes before the other has started
        return _completion(f"gen-{token}")

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    results: dict[str, UmpireAdjudication] = {}

    def call(token: str) -> None:
        results[token] = umpire.adjudicate_recorded(_context(1, action=f"action-{token}"))

    threads = [threading.Thread(target=call, args=(token,)) for token in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    for token in ("alpha", "beta"):
        result = results[token]
        assert dataclasses.is_dataclass(result) and result.__dataclass_params__.frozen
        assert result.proposal is not None
        assert result.proposal.mode is AdjudicationMode.automatic_success
        assert result.failure is None
        assert isinstance(result.attempts, tuple)
        assert [a["provider_metadata"]["generation_id"] for a in result.attempts] == [
            f"gen-{token}"
        ]
        assert result.attempts[0]["completed"] is True


def test_i081_004b_recorded_result_carries_every_attempt_and_the_outage_failure() -> None:
    calls = {"n": 0}

    def recovering(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:  # truncated: reasoning ate the budget, no JSON came
            return httpx.Response(200, json={
                "id": "gen-truncated", "model": "test/umpire", "provider": "First",
                "choices": [{"message": {"role": "assistant", "content": ""},
                             "finish_reason": "length"}],
            })
        return _completion("gen-recovered")

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(recovering)),
        model="test/umpire",
    )
    result = umpire.adjudicate_recorded(_context(1))
    assert result.proposal is not None and result.failure is None
    assert [a["provider_metadata"]["generation_id"] for a in result.attempts] == [
        "gen-truncated", "gen-recovered",
    ]
    assert [a["completed"] for a in result.attempts] == [False, True]
    assert result.attempts[0]["failure_reason"] == (
        "output:Umpire returned no usable JSON object:finish_reason=length"
    )

    def outage(_request: httpx.Request) -> httpx.Response:
        return _completion("", None, status=503)

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(outage)),
        model="test/umpire",
    )
    result = umpire.adjudicate_recorded(_context(1))
    assert result.proposal is None
    assert result.failure == (
        "provider:transient:status=503:provider returned 503 for model test/umpire"
    )
    assert len(result.attempts) == 4  # the D-063 bound
    assert all(a["completed"] is False for a in result.attempts)
    assert all(a["failure_reason"] == result.failure for a in result.attempts)
    # The plain ``Umpire`` contract is unchanged: the same call raises for the engine.
    with pytest.raises(UmpireUnavailable, match="transient"):
        umpire.adjudicate(_context(1))


# --- TEST-I081-005: concurrent advances on two games never interleave Umpire records --------


def _adjudicating_game(engine: Engine, *, action: str) -> uuid.UUID:
    from app import game_service, scenarios

    creation = scenarios.create_game_from_scenario(engine, scenarios.load_scenario(FIXTURE_DIR))
    game_service.start_game(engine, creation.game_id)
    for actor_id in ACTORS:
        game_service.ready(engine, creation.game_id, actor_id=actor_id)
    argument = game_service.make_argument(
        engine, creation.game_id, actor_id="arkania",
        action=action, desired_effect="Effect", pros=["Pro"],
    )
    for actor_id in ("belvar", "cestia"):
        game_service.pass_con(engine, creation.game_id, argument_id=argument.id, actor_id=actor_id)
    return creation.game_id


def _umpire_runs(db_engine: Engine, game_id: uuid.UUID) -> list[dict[str, Any]]:
    exported = export_module.build_game_export(db_engine, game_id)
    return [run for run in exported["agent_runs"] if run["role"] == "umpire"]


def test_i081_005_concurrent_advances_persist_each_games_own_umpire_attempt(
    db_engine: Engine,
) -> None:
    """Two games share the one app-level ``AIUmpire``; both ``/advance`` calls are in flight at
    the provider at the same time. Each game must persist exactly the attempt made for *its*
    Argument — a shared drain would hand both records to whichever request finished first."""
    games = {
        token: _adjudicating_game(db_engine, action=f"action-{token}")
        for token in ("alpha", "beta")
    }
    both_in_flight = threading.Barrier(2, timeout=10)

    def handler(request: httpx.Request) -> httpx.Response:
        token = _requested_action(request)
        both_in_flight.wait()
        return _completion(f"gen-{token}")

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler)),
        model="test/umpire",
    )
    app = create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
        umpire=umpire,
    )
    client = TestClient(app, raise_server_exceptions=False)
    statuses: dict[str, int] = {}

    def advance(token: str) -> None:
        response = client.post(
            f"/admin/games/{games[token]}/advance",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={},
        )
        statuses[token] = response.status_code

    threads = [threading.Thread(target=advance, args=(token,)) for token in games]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert statuses == {"alpha": 200, "beta": 200}

    for token, game_id in games.items():
        runs = _umpire_runs(db_engine, game_id)
        assert [run["provider_metadata"]["generation_id"] for run in runs] == [f"gen-{token}"], (
            token, runs,
        )
        assert runs[0]["completed"] is True and runs[0]["phase"] == "adjudication"


# --- TEST-I081-006: the seam has no drain and no "last" state; the overlays still land -------


# The HTTP layer: the application module and its player and admin route modules.
API_SOURCES = sorted((Path(__file__).parent.parent / "app").glob("api*.py"))


def test_i081_006_provenance_seam_is_per_call_and_the_server_overlay_still_lands(
    db_engine: Engine,
) -> None:
    from typing import Protocol

    from agents.umpire import FakeUmpire
    from app.umpire import AdjudicationProposal, Umpire, UmpireProvenanceSource

    assert issubclass(UmpireProvenanceSource, Protocol)
    assert set(UmpireProvenanceSource.__protocol_attrs__) == {
        "provenance_configuration", "adjudicate_recorded",
    }
    assert set(Umpire.__protocol_attrs__) == {"adjudicate"}  # the adjudication contract alone

    class AdjudicateOnly:
        def adjudicate(self, context):  # noqa: ANN001
            return AdjudicationProposal(mode=AdjudicationMode.automatic_success, rationale="r")

    fake = FakeUmpire(
        proposal=AdjudicationProposal(mode=AdjudicationMode.automatic_success, rationale="r")
    )
    ai = AIUmpire(
        OpenRouterClient(
            api_key="sk-test",
            transport=httpx.MockTransport(lambda _r: _completion("gen-seam")),
        ),
        model="test/umpire",
    )
    for implementation in (fake, ai):
        assert isinstance(implementation, Umpire)
        assert isinstance(implementation, UmpireProvenanceSource)
    assert not isinstance(AdjudicateOnly(), UmpireProvenanceSource)
    fake_result = fake.adjudicate_recorded(_context(1))
    assert fake_result.attempts == () and fake_result.failure is None
    assert fake_result.proposal is not None

    assert len(API_SOURCES) == 3
    for path in API_SOURCES:
        source = path.read_text(encoding="utf-8")
        assert "drain_provenance" not in source, path.name
        assert "last_failure" not in source, path.name

    # The server-side overlay: a provider-accepted proposal the server refuses is persisted as
    # the provider call it was, failed with the server's exception class (D-065 precedence).
    refused = AIUmpire(
        OpenRouterClient(
            api_key="sk-test",
            transport=httpx.MockTransport(lambda _r: _completion("gen-refused", {
                "mode": "probabilistic", "probability": 1.1, "rationale": "r",
                "success_effects": ["s"],
            })),
        ),
        model="test/umpire",
    )
    game_id = _adjudicating_game(db_engine, action="Act")
    client = TestClient(create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
        umpire=refused,
    ), raise_server_exceptions=False)
    response = client.post(
        f"/admin/games/{game_id}/advance", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={},
    )
    assert response.status_code == 422, response.text
    [run] = _umpire_runs(db_engine, game_id)
    assert run["completed"] is False
    assert run["failure_reason"] == "InvalidAdjudication"
    assert run["provider_metadata"]["generation_id"] == "gen-refused"

    # The Umpire-stated failure takes precedence: an outage pauses the game and every bounded
    # attempt is persisted with the Umpire's own diagnosis.
    outage = AIUmpire(
        OpenRouterClient(
            api_key="sk-test",
            transport=httpx.MockTransport(lambda _r: _completion("", None, status=503)),
        ),
        model="test/umpire",
    )
    game_id = _adjudicating_game(db_engine, action="Act")
    client = TestClient(create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
        umpire=outage,
    ), raise_server_exceptions=False)
    response = client.post(
        f"/admin/games/{game_id}/advance", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={},
    )
    assert response.status_code == 503, response.text
    runs = _umpire_runs(db_engine, game_id)
    assert len(runs) == 4
    assert {run["failure_reason"] for run in runs} == {
        "provider:transient:status=503:provider returned 503 for model test/umpire"
    }


# --- TEST-I110-001: terminal mixed failures retain each attempt's own diagnosis ------------


def test_i110_001_mixed_terminal_umpire_failures_keep_per_attempt_reasons(
    db_engine: Engine,
) -> None:
    calls = {"n": 0}

    def mixed_outage(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] in {1, 3}:
            return _completion("", None, status=503)
        return httpx.Response(200, json={
            "id": f"gen-unusable-{calls['n']}",
            "model": "test/umpire",
            "provider": "TestProvider",
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(mixed_outage)),
        model="test/umpire",
    )
    game_id = _adjudicating_game(db_engine, action="Act")
    client = TestClient(create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
        umpire=umpire,
    ), raise_server_exceptions=False)

    response = client.post(
        f"/admin/games/{game_id}/advance",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={},
    )

    assert response.status_code == 503, response.text
    exported = export_module.build_game_export(db_engine, game_id)
    assert exported["game"]["status"] == "paused"
    assert [run["failure_reason"] for run in _umpire_runs(db_engine, game_id)] == [
        "provider:transient:status=503:provider returned 503 for model test/umpire",
        "output:Umpire returned no usable JSON object:finish_reason=stop",
        "provider:transient:status=503:provider returned 503 for model test/umpire",
        "output:Umpire returned no usable JSON object:finish_reason=stop",
    ]
