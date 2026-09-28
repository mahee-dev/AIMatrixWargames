"""T-080 acceptance: round-robin Argument rounds (I-109, D-094).

``game.primary_arguments_per_actor`` N = 2 or 3 runs N Argument rounds per turn (state-machine
INV-ARG-003, §17, §20, §24): each round walks ``turn_order`` once, an Argument's ``budget_slot``
is its round, and one Actor never argues twice in a row. N = 1 is the released loop, unchanged
in state, events, contexts and provenance. The round is derived from persisted Arguments, so a
restart needs no new state.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine
from sqlmodel import select

from agents import lifecycle
from agents.player import PlayerConfig, ReferencePlayer
from agents.scripted import (
    build_scripted_players,
    deterministic_consequence_resolver,
    deterministic_umpire,
    run_deterministic_game,
)
from agents.tools import PlayerTools
from agents.umpire import FakeUmpire
from app import context as actor_context_module
from app import database, export, game_service, provenance, scenarios
from app.consequences import ConsequenceContext
from app.database import session_scope
from app.models import Argument, GameEvent, GamePhase, GameStatus
from app.umpire import AdjudicationContext
from tests.api_helpers import FIXTURE_DIR, bearer, client
from tests.test_scenarios import _VALID, _mutate, _write

ACTORS = ("arkania", "belvar", "cestia")
BOUNDED = "bounded_effects_v1"
SEED = 8080


def _package(tmp_path: Path, *, budget: int, max_turns: int = 2, mode: str | None = None) -> Path:
    """A copy of the golden fixture with the Argument budget (and turn count) changed."""
    package = tmp_path / f"budget-{budget}-{mode or 'none'}"
    shutil.copytree(FIXTURE_DIR, package)
    manifest = package / "scenario.yaml"
    text = manifest.read_text(encoding="utf-8")
    text = text.replace("  primary_arguments_per_actor: 1\n", f"  primary_arguments_per_actor: {budget}\n")
    text = text.replace("  max_turns: 6\n", f"  max_turns: {max_turns}\n")
    if mode is not None:
        text = text.replace("  max_cons_per_actor: 3\n", f"  max_cons_per_actor: 3\n  consequence_mode: {mode}\n")
    manifest.write_text(text, encoding="utf-8")
    return package


def _create(
    engine: Engine, package: Path, *, seed: int = SEED, start: bool = True
) -> scenarios.GameCreation:
    loaded = scenarios.load_scenario(package)
    creation = scenarios.create_game_from_scenario(engine, loaded, seed=seed)
    if start:
        game_service.start_game(engine, creation.game_id)
    return creation


def _drive(engine: Engine, game_id: uuid.UUID, *, max_iterations: int | None = None, seen=None):
    umpire, resolver = deterministic_umpire(), deterministic_consequence_resolver()
    seen = seen if seen is not None else SimpleNamespace(adjudications=[], consequences=[])

    def adjudicate(ctx: AdjudicationContext):
        seen.adjudications.append(ctx)
        return umpire.adjudicate(ctx)

    class Recording:
        def propose(self, ctx: ConsequenceContext):
            seen.consequences.append(ctx)
            return resolver.propose(ctx)

    run_deterministic_game(
        engine,
        game_id,
        build_scripted_players(ACTORS),
        FakeUmpire(responder=adjudicate),
        max_iterations=max_iterations,
        consequence_resolver=Recording(),
    )
    return seen


def _events(engine: Engine, game_id: uuid.UUID) -> list[SimpleNamespace]:
    with session_scope(engine) as session:
        return [
            SimpleNamespace(type=e.type, payload=dict(e.payload or {}))
            for e in session.exec(
                select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
            ).all()
        ]


def _arguments(engine: Engine, game_id: uuid.UUID) -> dict[str, SimpleNamespace]:
    with session_scope(engine) as session:
        return {
            str(a.id): SimpleNamespace(turn=a.turn, actor_id=a.actor_id, budget_slot=a.budget_slot)
            for a in session.exec(select(Argument).where(Argument.game_id == game_id)).all()
        }


def _argument_sequence(engine: Engine, game_id: uuid.UUID) -> list[tuple[int, str, int]]:
    """(turn, owner, budget_slot) in ``argument_submitted`` event order."""
    arguments = _arguments(engine, game_id)
    sequence = []
    for event in _events(engine, game_id):
        if event.type == "argument_submitted":
            argument = arguments[event.payload["argument_id"]]
            sequence.append((argument.turn, argument.actor_id, argument.budget_slot))
    return sequence


# --- TEST-T080-001: the loader accepts 1–3 and nothing else --------------------------------


@pytest.mark.parametrize("budget", [1, 2, 3])
def test_t080_001_budgets_one_to_three_load(tmp_path: Path, budget: int) -> None:
    text = _mutate(_VALID, "  primary_arguments_per_actor: 1\n", f"  primary_arguments_per_actor: {budget}\n")
    assert scenarios.load_scenario(_write(tmp_path, text)).game.primary_arguments_per_actor == budget


@pytest.mark.parametrize("budget", [0, 4, 10])
def test_t080_001_budgets_outside_one_to_three_are_rejected(tmp_path: Path, budget: int) -> None:
    """A budget outside 1…3 is refused at load (also carries TEST-I079-001's rejection half)."""
    text = _mutate(_VALID, "  primary_arguments_per_actor: 1\n", f"  primary_arguments_per_actor: {budget}\n")
    with pytest.raises(scenarios.ScenarioValidationError) as raised:
        scenarios.load_scenario(_write(tmp_path, text))
    assert any("primary_arguments_per_actor" in error for error in raised.value.errors)


# --- TEST-T080-002: a budget-2 game plays round-robin rounds to completion -----------------


def test_t080_002_budget_two_runs_round_robin_rounds(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=2))
    _drive(db_engine, creation.game_id)

    with session_scope(db_engine) as session:
        from app.models import Game

        game = session.get(Game, creation.game_id)
        assert game.status is GameStatus.complete

    expected = [
        (turn, actor, slot) for turn in (1, 2) for slot in (1, 2) for actor in ACTORS
    ]
    assert _argument_sequence(db_engine, creation.game_id) == expected

    events = _events(db_engine, creation.game_id)
    rounds = [e for e in events if e.type == "argument_round_started"]
    assert [(e.payload["turn"], e.payload["round"]) for e in rounds] == [(1, 2), (2, 2)]
    for boundary in rounds:
        position = events.index(boundary)
        # The round starts after the last Actor's round-1 adjudication and right before the
        # round-2 phase_started(argument) (§24).
        assert events[position + 1].type == "phase_started"
        assert events[position + 1].payload == {"phase": "argument"}
        previous = [e.type for e in events[:position]]
        assert previous[-1] in {"argument_adjudicated", "effect_created", "effect_deactivated"}
    # Each turn completes once, after the last round.
    assert [e.payload["turn"] for e in events if e.type == "turn_completed"] == [1, 2]


def test_t080_002_budget_three_runs_three_rounds(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=3, max_turns=1))
    _drive(db_engine, creation.game_id)
    assert _argument_sequence(db_engine, creation.game_id) == [
        (1, actor, slot) for slot in (1, 2, 3) for actor in ACTORS
    ]


# --- TEST-T080-003: round order is enforced ------------------------------------------------


def _to_argument(engine: Engine, game_id: uuid.UUID) -> None:
    for actor in ACTORS:
        game_service.ready(engine, game_id, actor)


def _cycle(engine: Engine, game_id: uuid.UUID, owner: str) -> None:
    result = game_service.make_argument(
        engine, game_id, actor_id=owner, action="Act", desired_effect="Effect", pros=["Pro"]
    )
    _finish(engine, game_id, owner, result)


def _finish(engine: Engine, game_id: uuid.UUID, owner: str, result) -> None:
    """Pass every Con, resolve with the deterministic Umpire and advance (server-owned)."""
    for opponent in ACTORS:
        if opponent != owner:
            game_service.pass_con(engine, game_id, argument_id=result.id, actor_id=opponent)
    game_service.resolve_adjudication(engine, game_id, deterministic_umpire())
    game_service.advance_after_resolution(engine, game_id)


def test_t080_003_no_actor_argues_out_of_its_round(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=2))
    game_id = creation.game_id
    _to_argument(db_engine, game_id)

    _cycle(db_engine, game_id, "arkania")
    # Round 1 is not finished: arkania may not take its round-2 slot now.
    with pytest.raises(game_service.NotActiveActor):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="A", desired_effect="E", pros=["P"]
        )
    _cycle(db_engine, game_id, "belvar")
    _cycle(db_engine, game_id, "cestia")

    # Round 2: the ring restarts at the first Actor.
    view = actor_context_module.actor_context(db_engine, game_id, "arkania")
    assert view["phase"] == "argument" and view["active_actor_id"] == "arkania"
    assert "make_argument" in view["available_actions"]
    _cycle(db_engine, game_id, "arkania")
    _cycle(db_engine, game_id, "belvar")

    # cestia is active for its slot 2; once used, a third Argument is refused by the budget.
    result = game_service.make_argument(
        db_engine, game_id, actor_id="cestia", action="A", desired_effect="E", pros=["P"]
    )
    assert result.budget_slot == 2


# --- TEST-T080-004: budget 1 is the released loop, unchanged -------------------------------


def test_t080_004_budget_one_adds_no_round_fields(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=1))
    game_id = creation.game_id
    _to_argument(db_engine, game_id)
    view = actor_context_module.actor_context(db_engine, game_id, "arkania")
    assert "argument_budget" not in view
    result = game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="A", desired_effect="E", pros=["P"]
    )
    view = actor_context_module.actor_context(db_engine, game_id, "belvar")
    assert "budget_slot" not in view["current_argument"]
    _finish(db_engine, game_id, "arkania", result)

    seen = _drive(db_engine, game_id)
    assert len(seen.adjudications) == 5  # arkania's turn-1 Argument was resolved above
    assert all(set(ctx.argument) == {"actor_id", "action", "desired_effect", "pros"} for ctx in seen.adjudications)
    assert not [e for e in _events(db_engine, game_id) if e.type == "argument_round_started"]
    assert _argument_sequence(db_engine, game_id) == [(t, a, 1) for t in (1, 2) for a in ACTORS]


def test_t080_004_budget_one_provenance_has_no_sequencing_condition(
    db_engine: Engine, tmp_path: Path
) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=1), start=False)
    manifest = provenance.create_run_manifest(
        db_engine, creation.game_id, configuration=_configuration()
    )
    assert "argument_sequencing" not in manifest.manifest["scenario"]
    assert "argument_sequencing" not in manifest.manifest.get("game_loop", {})


# --- TEST-T080-005: budget-2 contexts carry the round --------------------------------------


def test_t080_005_actor_and_umpire_contexts_carry_the_round(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=2, max_turns=1))
    game_id = creation.game_id

    view = actor_context_module.actor_context(db_engine, game_id, "belvar")
    assert view["argument_budget"] == {"primary_arguments_per_actor": 2, "round": None, "used": 0}

    _to_argument(db_engine, game_id)
    view = actor_context_module.actor_context(db_engine, game_id, "arkania")
    assert view["argument_budget"] == {"primary_arguments_per_actor": 2, "round": 1, "used": 0}
    for owner in ACTORS:
        _cycle(db_engine, game_id, owner)

    view = actor_context_module.actor_context(db_engine, game_id, "arkania")
    assert view["argument_budget"] == {"primary_arguments_per_actor": 2, "round": 2, "used": 1}
    result = game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="A", desired_effect="E", pros=["P"]
    )
    view = actor_context_module.actor_context(db_engine, game_id, "belvar")
    assert view["argument_budget"] == {"primary_arguments_per_actor": 2, "round": 2, "used": 1}
    assert view["current_argument"]["budget_slot"] == 2
    _finish(db_engine, game_id, "arkania", result)

    seen = _drive(db_engine, game_id)
    assert [ctx.argument["budget_slot"] for ctx in seen.adjudications] == [2, 2]
    assert {ctx.argument["primary_arguments_per_actor"] for ctx in seen.adjudications} == {2}

    view = actor_context_module.actor_context(db_engine, game_id, "belvar")
    assert view["argument_budget"]["round"] is None  # the game is complete


def test_t080_005_player_packet_forwards_the_budget(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=2, max_turns=1))
    _to_argument(db_engine, creation.game_id)
    tools = PlayerTools(
        client(db_engine), game_id=str(creation.game_id), token=creation.seat_tokens["arkania"]
    )
    player = ReferencePlayer(
        tools, actor_id="arkania", actor_name="Arkania", model="test",
        config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
    )
    packet = player.build_packet()
    assert packet["argument_budget"] == {"primary_arguments_per_actor": 2, "round": 1, "used": 0}


# --- TEST-T080-006: the consequence pass follows the last round ----------------------------


def test_t080_006_consequence_pass_runs_once_after_the_last_round(
    db_engine: Engine, tmp_path: Path
) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=2, mode=BOUNDED))
    seen = _drive(db_engine, creation.game_id)

    assert len(seen.consequences) == 2  # one pass per turn
    events = _events(db_engine, creation.game_id)
    types = [e.type for e in events]
    for turn in (1, 2):
        round_two = next(
            i for i, e in enumerate(events)
            if e.type == "argument_round_started" and e.payload["turn"] == turn
        )
        consequences = next(
            i for i, e in enumerate(events)
            if e.type == "phase_started" and e.payload == {"phase": "consequences"}
            and i > round_two
        )
        # Every one of the turn's six adjudications precedes the pass.
        turn_start = max(
            (i for i, e in enumerate(events) if e.type == "phase_started"
             and e.payload == {"phase": "diplomacy"} and i < round_two),
        )
        assert types[turn_start:consequences].count("argument_adjudicated") == 6
    # The pass sees both rounds: all six current-turn adjudications are citable outcomes.
    for ctx in seen.consequences:
        assert len(ctx.outcomes) == 6
        assert [o["actor_id"] for o in ctx.outcomes] == list(ACTORS) * 2


# --- TEST-T080-007: a restart in round 2 resumes deterministically -------------------------


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _normalised(value):
    if isinstance(value, dict):
        return {
            k: _normalised(v)
            for k, v in value.items()
            if not (k.endswith("_at") or k.endswith("_ms") or k == "database")
        }
    if isinstance(value, list):
        return sorted((_normalised(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, str):
        return _UUID.sub("<uuid>", value)
    return value


def test_t080_007_restart_in_round_two_resumes(tmp_path: Path) -> None:
    package = _package(tmp_path, budget=2, mode=BOUNDED)

    reference_engine = database.engine_for_path(tmp_path / "reference.db")
    database.initialize_database(reference_engine)
    reference = _create(reference_engine, package)
    _drive(reference_engine, reference.game_id)
    expected = export.build_game_export(reference_engine, reference.game_id)
    reference_engine.dispose()

    path = tmp_path / "restarted.db"
    first = database.engine_for_path(path)
    database.initialize_database(first)
    creation = _create(first, package)
    # Diplomacy plus four Argument cycles: arkania's round-2 Argument is resolved.
    with pytest.raises(RuntimeError):
        _drive(first, creation.game_id, max_iterations=5)
    with session_scope(first) as session:
        from app.models import Game

        game = session.get(Game, creation.game_id)
        assert (game.turn, game.phase, game.active_actor_id) == (1, GamePhase.argument, "belvar")
    first.dispose()

    second = database.engine_for_path(path)
    _drive(second, creation.game_id)
    actual = export.build_game_export(second, creation.game_id)
    second.dispose()

    assert _normalised(actual["events"]) == _normalised(expected["events"])
    assert _normalised(actual["arguments"]) == _normalised(expected["arguments"])
    assert actual["game"]["rng_draw_index"] == expected["game"]["rng_draw_index"]


# --- TEST-T080-008: provenance and export record the policy and the slots ------------------


def _configuration() -> dict:
    return {
        "player": {"model": "fake"},
        "umpire": {"model": "fake"},
        "code_version": "test",
        "benchmark": None,
    }


def test_t080_008_provenance_seals_round_robin_and_export_carries_slots(
    db_engine: Engine, tmp_path: Path
) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=2), start=False)
    manifest = provenance.create_run_manifest(
        db_engine, creation.game_id, configuration=_configuration()
    )
    game_service.start_game(db_engine, creation.game_id)
    sealed = manifest.manifest["scenario"]
    assert sealed["argument_sequencing"] == "round_robin_v1"
    assert sealed["primary_arguments_per_actor"] == 2
    assert "argument_sequencing" not in manifest.manifest.get("game_loop", {})

    _drive(db_engine, creation.game_id)
    exported = export.build_game_export(db_engine, creation.game_id)
    assert exported["export_format_version"] == export.EXPORT_FORMAT_VERSION
    assert exported["game"]["primary_arguments_per_actor"] == 2
    slots = [(a["turn"], a["budget_slot"]) for a in exported["arguments"]]
    assert slots == [(t, s) for t in (1, 2) for s in (1, 2) for _ in ACTORS]


# --- TEST-T080-009: the runner lifecycle treats each round as a new opportunity ------------


def test_t080_009_lifecycle_opportunity_key_includes_the_round() -> None:
    released = {"turn": 1, "phase": "argument", "active_actor_id": "arkania"}
    assert lifecycle.opportunity_key(released) == (1, "argument", "arkania")

    def budgeted(round_: int) -> dict:
        return {
            **released,
            "argument_budget": {"primary_arguments_per_actor": 2, "round": round_, "used": round_ - 1},
        }

    assert lifecycle.opportunity_key(budgeted(1)) != lifecycle.opportunity_key(budgeted(2))


# --- TEST-T080-010: the API exposes the budget and the round event ------------------------


def test_t080_010_api_context_and_round_event_are_actor_visible(
    db_engine: Engine, tmp_path: Path
) -> None:
    creation = _create(db_engine, _package(tmp_path, budget=2, max_turns=1))
    game_id = creation.game_id
    _to_argument(db_engine, game_id)
    for owner in ACTORS:
        _cycle(db_engine, game_id, owner)

    api = client(db_engine)
    headers = bearer(creation.seat_tokens["belvar"])
    context = api.get(f"/games/{game_id}/context", headers=headers).json()
    assert context["argument_budget"] == {"primary_arguments_per_actor": 2, "round": 2, "used": 1}
    events = api.get(f"/games/{game_id}/events", headers=headers).json()
    rows = events.get("events", events) if isinstance(events, dict) else events
    assert any(row["type"] == "argument_round_started" for row in rows)


# --- TEST-T080-011: the autonomous runner plays Argument rounds end to end -----------------


@pytest.mark.parametrize("activation_mode", ["single_shot", "tool_loop"])
@pytest.mark.slow
def test_t080_011_autonomous_runner_completes_a_budget_two_game(
    db_engine: Engine, tmp_path: Path, activation_mode: str
) -> None:
    from agents.autonomous import AutonomousGameRunner
    from agents.lifecycle import LifecycleRunner
    from tests.agent_fakes import (
        default_decision_policy,
        default_policy,
        model_from_decision_policy,
        model_from_policy,
    )
    from tests.test_cons_visible_reply import ADMIN_TOKEN, _admin_client

    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        model = (
            model_from_policy(default_policy)
            if activation_mode == "tool_loop"
            else model_from_decision_policy(default_decision_policy)
        )
        player = ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name, model=model,
            config=PlayerConfig(model="fake/deterministic", activation_mode=activation_mode),
        )
        return LifecycleRunner(player)

    runner = AutonomousGameRunner(
        _admin_client(db_engine, umpire=deterministic_umpire()),
        admin_token=ADMIN_TOKEN,
        scenario=_package(tmp_path, budget=2),
        player_factory=player_factory,
        seed=SEED,
        cons_concurrency=3,
    )
    exported = runner.run().export

    assert exported["game"]["status"] == "complete"
    by_id = {a["id"]: (a["turn"], a["actor_id"], a["budget_slot"]) for a in exported["arguments"]}
    submitted = [
        by_id[e["payload"]["argument_id"]]
        for e in exported["events"] if e["type"] == "argument_submitted"
    ]
    assert submitted == [(turn, actor, slot) for turn in (1, 2) for slot in (1, 2) for actor in ACTORS]
    assert exported["run_manifest"]["scenario"]["argument_sequencing"] == "round_robin_v1"
    argument_runs = [run for run in exported["agent_runs"] if run["phase"] == "argument"]
    # One Argument activation per slot: no stall, no retry, no bundled second slot.
    assert len(argument_runs) == len(exported["arguments"])
    assert not [run for run in exported["agent_runs"] if run.get("failure_category")]
