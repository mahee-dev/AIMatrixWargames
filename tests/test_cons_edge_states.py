"""I-079 — Argument/Cons edge states: a participation opportunity either executes the Actor's
intended legal decision or stays unresolved for the lifecycle; sequencing never silently
reinterprets the configured game (state-machine §18/§20, INV-ARG-003, agents.md §18).
"""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest
from sqlalchemy import Engine
from sqlmodel import select

from app import game_service, persistence, scenarios
from app.database import session_scope
from app.models import Actor, ConParticipation, Game, GameEvent, GamePhase, GameStatus, Seat
from tests.test_scenarios import _VALID, _mutate, _write
from tests.api_helpers import FIXTURE_DIR


# --- TEST-I079-001: an unsupported Argument budget is refused, never silently played as 1 ------
# D-094 widened the supported budget from exactly 1 to 1–3 (round-robin rounds, TEST-T080-001).


# The rejection half of TEST-I079-001 (budgets 0 and 4) is carried by
# ``test_t080_001_budgets_outside_one_to_three_are_rejected``.


def test_i079_001_a_budget_of_one_and_the_fixtures_still_load(tmp_path: Path) -> None:
    assert scenarios.load_scenario(_write(tmp_path, _VALID)).game.primary_arguments_per_actor == 1
    assert scenarios.load_scenario(FIXTURE_DIR).game.primary_arguments_per_actor == 1


# --- TEST-I079-002: every Argument has an opponent; an empty participation set closes ---------


def test_i079_002_a_scenario_with_one_actor_is_rejected(tmp_path: Path) -> None:
    text = _VALID
    for actor in ("actor_b", "actor_c"):
        text = _mutate(text, f"    - {actor}\n", "")
    text = text[: text.index("  actor_b:\n")]
    with pytest.raises(scenarios.ScenarioValidationError) as raised:
        scenarios.load_scenario(_write(tmp_path, text))
    assert any("Actor" in error and "two" in error for error in raised.value.errors)


def _one_actor_running_game(engine: Engine) -> uuid.UUID:
    """A hand-seeded Game that the scenario loader would now refuse: the engine must still not
    strand it in ``cons`` (INV-CON-003 with nobody eligible; §18 closes vacuously)."""
    with session_scope(engine) as session:
        game = persistence.create(session, Game(
            scenario_id="solo", scenario_version="1", status=GameStatus.running,
            turn=1, phase=GamePhase.argument, active_actor_id="solo_a", max_turns=2,
            turn_order=["solo_a"], primary_arguments_per_actor=1, max_cons_per_actor=3,
            seed=1,
        ))
        persistence.create(session, Actor(
            game_id=game.id, id="solo_a", name="Solo", public_description="p",
            private_brief="b", objectives=["o"],
        ))
        persistence.create(session, Seat(game_id=game.id, actor_id="solo_a", token_hash="h" * 64))
        return game.id


def test_i079_002_an_argument_with_no_eligible_opponent_closes_cons_at_once(db_engine: Engine) -> None:
    game_id = _one_actor_running_game(db_engine)

    accepted = game_service.make_argument(
        db_engine, game_id, actor_id="solo_a", action="Act", desired_effect="Effect", pros=["Pro"],
    )

    with session_scope(db_engine) as session:
        persisted = session.get(Game, game_id)
        assert persisted.phase is GamePhase.adjudication
        events = [
            (e.type, e.payload) for e in session.exec(
                select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
            ).all()
        ]
    assert accepted.status == "adjudicating"
    assert _participation(db_engine, accepted.id) == []
    assert events[-3:] == [
        ("argument_submitted", {"argument_id": str(accepted.id)}),
        ("phase_started", {"phase": "cons"}),
        ("phase_started", {"phase": "adjudication"}),
    ]


def _participation(engine: Engine, argument_id: uuid.UUID) -> list[str]:
    with session_scope(engine) as session:
        return [
            p.actor_id for p in session.exec(
                select(ConParticipation).where(ConParticipation.argument_id == argument_id)
            ).all()
        ]


# --- TEST-I079-003: rejected Cons never become an authoritative pass -------------------------


def _key(operation: str, payload: dict) -> str:
    import json

    return f"k-{operation}-{json.dumps(payload, sort_keys=True)}"


def test_i079_003_all_rejected_cons_leave_the_opportunity_unresolved(db_engine: Engine) -> None:
    from agents.decisions import ConsDecision, execute_decision
    from agents.tools import PlayerTools
    from app.game_service import MAX_TEXT_CHARS
    from tests.api_helpers import client as api_client, seed, start_to_cons
    from tests.test_single_shot import _memory

    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    tools = PlayerTools(api_client(db_engine), game_id=str(creation.game_id), token=creation.seat_tokens["belvar"])
    too_long = "x" * (MAX_TEXT_CHARS + 1)  # the server refuses each; pass_con stays legal

    report = execute_decision(
        ConsDecision(cons=[too_long, too_long], strategic_memory=_memory()),
        tools, argument_id=str(argument.id), key_fn=_key,
    )

    assert report.actions == ["make_con", "make_con"]
    assert [r["action"] for r in report.rejected] == ["make_con", "make_con"]
    assert _participation(db_engine, argument.id) == []
    observed = tools.observe()
    assert observed.ok and "pass_con" in observed.data["available_actions"]
    assert observed.data["requires_input"] is True


def test_i079_003_a_partly_accepted_decision_still_completes_as_intended(db_engine: Engine) -> None:
    from agents.decisions import ConsDecision, execute_decision
    from agents.tools import PlayerTools
    from app.game_service import MAX_TEXT_CHARS
    from tests.api_helpers import client as api_client, seed, start_to_cons
    from tests.test_single_shot import _memory

    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    tools = PlayerTools(api_client(db_engine), game_id=str(creation.game_id), token=creation.seat_tokens["belvar"])

    report = execute_decision(
        ConsDecision(cons=["Rail is disrupted.", "x" * (MAX_TEXT_CHARS + 1)], strategic_memory=_memory()),
        tools, argument_id=str(argument.id), key_fn=_key,
    )

    assert report.actions == ["make_con", "make_con", "pass_con"]
    assert [r["action"] for r in report.rejected] == ["make_con"]
    assert _participation(db_engine, argument.id) == ["belvar"]


@pytest.mark.slow
def test_i079_003_lifecycle_reactivates_the_actor_for_the_same_cons_opportunity(
    db_engine: Engine, short_game: Path
) -> None:
    """The unresolved opportunity is handled by the existing bounded reactivation, not by a
    fabricated pass: the second activation decides again and completes it legally."""
    from fastapi.testclient import TestClient
    from pydantic_ai.models.function import AgentInfo

    from agents.autonomous import AutonomousGameRunner
    from agents.guardrails import GuardrailConfig
    from agents.lifecycle import LifecycleRunner
    from agents.player import PlayerConfig, ReferencePlayer
    from agents.scripted import deterministic_umpire
    from agents.tools import PlayerTools
    from app.api import create_app
    from app.config import Settings
    from app.game_service import MAX_TEXT_CHARS
    from tests.agent_fakes import decision_call, default_decision_policy, model_from_decision_policy
    from tests.benchmark_helpers import SHORT_TURNS
    from tests.test_single_shot import _memory

    seen: dict[tuple, int] = {}

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        if "make_con" in packet.get("available_actions", []):
            key = (packet["you"]["actor_id"], packet["current_argument"]["id"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 1:  # passes the client guardrail below, refused by the server
                return [decision_call(info, {
                    "cons": ["x" * (MAX_TEXT_CHARS + 1)], "strategic_memory": _memory()
                })]
            return [decision_call(info, {"cons": [], "strategic_memory": _memory()})]
        return default_decision_policy(packet, idx, info)

    def factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        return LifecycleRunner(ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_decision_policy(policy),
            config=PlayerConfig(
                model="fake/deterministic",
                guardrails=GuardrailConfig(max_message_chars=MAX_TEXT_CHARS + 100),
            ),
        ))

    client = TestClient(
        create_app(
            engine=db_engine, settings=Settings(AMAW_ADMIN_TOKEN="admin-secret-token", _env_file=None),
            umpire=deterministic_umpire(),
        ),
        raise_server_exceptions=False,
    )
    runner = AutonomousGameRunner(
        client, admin_token="admin-secret-token", scenario=short_game, player_factory=factory, seed=7,
    )
    result = runner.run()

    assert result.export["game"]["status"] == "complete"
    assert result.failures == []
    cons = [t for t in result.activations if t.phase == "cons"]
    # Per turn, 3 Arguments × 2 opponents, each activated twice: the refused challenge, then the pass.
    assert len(cons) == SHORT_TURNS * 3 * 2 * 2
    assert all(count == 2 for count in seen.values())
