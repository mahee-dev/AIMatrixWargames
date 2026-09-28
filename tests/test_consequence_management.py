"""T-074 — bounded, evidence-linked Consequence Management (D-091, state-machine §21 Option B).

Keyed to T-072 §8.2 and the WBS done-when. The default suite is offline and key-free: every
engine test drives the canonical fixture with the deterministic ScriptedPlayers, the fixture
Umpire and a deterministic fake consequence resolver. The one provider smoke is ``llm``-marked.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import select
from typer.testing import CliRunner

import amaw_cli
from agents.consequence import (
    CONSEQUENCE_MAX_ATTEMPTS,
    CONSEQUENCE_PROMPT_VERSION,
    AIConsequenceResolver,
    FakeConsequenceResolver,
    load_consequence_prompt,
)
from agents.guardrails import GuardrailConfig
from agents.model_client import OpenRouterClient
from agents.scripted import (
    _run_argument_cycle,
    _run_diplomacy,
    build_scripted_players,
    deterministic_consequence_resolver,
    deterministic_umpire,
    run_deterministic_game,
)
from app import context as context_module
from app import export as export_module
from app import game_service, resumption, scenarios
from app.api import create_app
from app.config import Settings
from app.consequences import (
    ConsequenceContext,
    ConsequenceProposal,
    ConsequenceResolverUnavailable,
    ProposedConsequence,
)
from app.database import session_scope
from app.models import (
    Actor,
    AgentRun,
    ConsequenceRecord,
    Effect,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
    Message,
    RunManifest,
)
from tests.api_helpers import FIXTURE_DIR
from tests.cli_helpers import capture_run_cli, invoke_run

BOUNDED = "bounded_effects_v1"
ADMIN = "t074-admin"


# --- helpers ------------------------------------------------------------------------------


def _create(engine: Engine, *, consequence_mode: str | None = BOUNDED) -> uuid.UUID:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(
        engine, scenario, consequence_mode=consequence_mode
    )
    game_service.start_game(engine, creation.game_id)
    return creation.game_id


def _game(engine: Engine, game_id: uuid.UUID) -> Game:
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        session.expunge(game)
        return game


def _play(engine: Engine, game_id: uuid.UUID, resolver) -> None:  # noqa: ANN001
    run_deterministic_game(
        engine,
        game_id,
        build_scripted_players(_game(engine, game_id).turn_order),
        deterministic_umpire(),
        consequence_resolver=resolver,
    )


def _to_consequences(engine: Engine, *, turn: int = 1, game_id: uuid.UUID | None = None) -> uuid.UUID:
    """Drive a ``bounded_effects_v1`` game through the service to ``consequences`` of ``turn``;
    every earlier turn's pass commits zero Effects."""
    game_id = game_id or _create(engine)
    order = _game(engine, game_id).turn_order
    players = build_scripted_players(order)
    umpire = deterministic_umpire()
    empty = FakeConsequenceResolver(ConsequenceProposal(effects=[]))
    for _ in range(100):
        game = _game(engine, game_id)
        if game.phase is GamePhase.consequences:
            if game.turn == turn:
                return game_id
            game_service.resolve_consequences(engine, game_id, empty)
            game_service.advance_after_resolution(engine, game_id)
        elif game.phase is GamePhase.diplomacy:
            _run_diplomacy(engine, game_id, players, order)
        elif game.phase is GamePhase.argument:
            _run_argument_cycle(engine, game_id, players, order, game.active_actor_id, umpire)
        else:  # pragma: no cover - the driver never observes another top-level phase
            raise AssertionError(f"unexpected phase {game.phase}")
    raise AssertionError("did not reach the consequences phase")


def _state(engine: Engine, game_id: uuid.UUID) -> dict:
    """Everything a rejected pass must leave untouched."""
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        return {
            "rng_draw_index": game.rng_draw_index,
            "records": len(
                session.exec(
                    select(ConsequenceRecord).where(ConsequenceRecord.game_id == game_id)
                ).all()
            ),
            "effects": sorted(
                (str(e.id), e.active)
                for e in session.exec(select(Effect).where(Effect.game_id == game_id)).all()
            ),
            "events": [
                e.type
                for e in session.exec(
                    select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
                ).all()
            ],
        }


def _one(*evidence: dict) -> ConsequenceProposal:
    return ConsequenceProposal(
        effects=[
            ProposedConsequence(
                description="Regional insurers raise premiums.",
                rationale="Same-turn moves raise perceived risk.",
                evidence=list(evidence),
            )
        ]
    )


def _event_ids(engine: Engine, game_id: uuid.UUID, *, turn: int, type_: str) -> list[int]:
    with session_scope(engine) as session:
        return [
            e.id
            for e in session.exec(
                select(GameEvent)
                .where(GameEvent.game_id == game_id, GameEvent.turn == turn, GameEvent.type == type_)
                .order_by(GameEvent.id)
            ).all()
        ]


def _aggregate_on_turn_one(context: ConsequenceContext) -> ConsequenceProposal:
    """Turn 1: one secondary Effect citing the first two Arguments' outcomes; later turns: none."""
    if context.turn != 1:
        return ConsequenceProposal(effects=[])
    first, second = context.outcomes[0], context.outcomes[1]
    return _one(
        {"kind": "event", "id": first["event_id"]},
        {"kind": "event", "id": second["event_id"]},
    )


# --- TEST-T074-001: the aggregate case ----------------------------------------------------


def test_t074_001_aggregate_consequence_cites_two_same_turn_arguments(db_engine: Engine) -> None:
    game_id = _create(db_engine)
    _play(db_engine, game_id, FakeConsequenceResolver(responder=_aggregate_on_turn_one))

    with session_scope(db_engine) as session:
        game = session.get(Game, game_id)
        assert game.status is GameStatus.complete and game.turn == 6
        records = session.exec(
            select(ConsequenceRecord)
            .where(ConsequenceRecord.game_id == game_id)
            .order_by(ConsequenceRecord.turn)
        ).all()
        assert [r.turn for r in records] == [1, 2, 3, 4, 5, 6]  # one pass per turn
        [entry] = records[0].effects
        assert all(not r.effects for r in records[1:])

        effect = session.get(Effect, uuid.UUID(entry["effect_id"]))
        assert effect.source_consequence_turn == 1
        assert effect.source_argument_id is None
        assert effect.visibility == "public" and effect.active and effect.expires_turn is None
        assert effect.supersedes == []

        cited = [session.get(GameEvent, ref["id"]) for ref in entry["evidence"]]
        assert all(e.type == "argument_adjudicated" and e.turn == 1 for e in cited)
        argument_ids = {e.payload["argument_id"] for e in cited}
        assert len(argument_ids) == 2  # two *different* same-turn Arguments

    exported = export_module.build_game_export(db_engine, game_id)
    assert exported["export_format_version"] == export_module.EXPORT_FORMAT_VERSION  # 15 at T-074
    assert exported["game"]["consequence_mode"] == BOUNDED
    assert exported["game"]["consequence_policy_version"] == "consequence-policy-v1"
    [record] = [r for r in exported["consequence_records"] if r["turn"] == 1]
    assert record["effects"][0]["evidence"] == entry["evidence"]
    assert record["effects"][0]["rationale"] == entry["rationale"]
    [origin] = [e for e in exported["effects"] if e["id"] == entry["effect_id"]]
    assert origin["source_consequence_turn"] == 1 and origin["source_argument_id"] is None
    # §24: the turn boundary is phase_started(consequences) → effect_created → consequences_resolved
    # → turn_completed.
    turn_one = [e["type"] for e in exported["events"] if e["turn"] == 1]
    tail = turn_one[turn_one.index("consequences_resolved") - 2 :]
    assert tail[:4] == [
        "phase_started", "effect_created", "consequences_resolved", "turn_completed"
    ]


# --- TEST-T074-002: evidence is mandatory and must resolve -------------------------------


def _assert_rejected_and_paused(engine: Engine, game_id: uuid.UUID, proposal) -> None:  # noqa: ANN001
    before = _state(engine, game_id)
    with pytest.raises(game_service.ConsequencesUnavailable):
        game_service.resolve_consequences(engine, game_id, FakeConsequenceResolver(proposal))
    after = _state(engine, game_id)
    # Nothing but the pause itself: no Effect, no record, no RNG draw.
    assert after["rng_draw_index"] == before["rng_draw_index"]
    assert after["records"] == before["records"]
    assert after["effects"] == before["effects"]
    assert after["events"] == [*before["events"], "game_paused"]
    game = _game(engine, game_id)
    assert game.status is GameStatus.paused and game.phase is GamePhase.consequences
    with session_scope(engine) as session:
        pause = session.exec(
            select(GameEvent)
            .where(GameEvent.game_id == game_id, GameEvent.type == "game_paused")
            .order_by(GameEvent.id.desc())
        ).first()
        assert pause.payload == {
            "reason": "consequence_resolver_unavailable",
            "turn": game.turn,
            "failure": "invalid_proposal",
        }


def test_t074_002_no_evidence_reference_rejects_the_whole_proposal(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    event = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]
    # A valid first Effect does not rescue an evidence-free second one: all or nothing.
    proposal = ConsequenceProposal(
        effects=[
            _one({"kind": "event", "id": event}).effects[0],
            ProposedConsequence(description="Something happens.", rationale="Because.", evidence=[]),
        ]
    )
    _assert_rejected_and_paused(db_engine, game_id, proposal)


def test_t074_002_event_from_another_turn_is_rejected(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine, turn=2)
    stale = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]
    _assert_rejected_and_paused(db_engine, game_id, _one({"kind": "event", "id": stale}))


def test_t074_002_event_from_another_game_is_rejected(db_engine: Engine) -> None:
    other = _to_consequences(db_engine)
    foreign = _event_ids(db_engine, other, turn=1, type_="argument_adjudicated")[0]
    game_id = _to_consequences(db_engine)
    _assert_rejected_and_paused(db_engine, game_id, _one({"kind": "event", "id": foreign}))


def test_t074_002_non_citable_event_type_is_rejected(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    phase_event = _event_ids(db_engine, game_id, turn=1, type_="phase_started")[0]
    _assert_rejected_and_paused(db_engine, game_id, _one({"kind": "event", "id": phase_event}))


def test_t074_002_inactive_effect_is_rejected(db_engine: Engine) -> None:
    # Turn 6's last adjudication supersedes cestia's turn-1 Effect (the fixture schedule).
    game_id = _to_consequences(db_engine, turn=6)
    with session_scope(db_engine) as session:
        inactive = session.exec(
            select(Effect).where(Effect.game_id == game_id, Effect.active == False)  # noqa: E712
        ).all()
        target = next(e for e in inactive if e.visibility == "public")
        target_id = str(target.id)
    _assert_rejected_and_paused(db_engine, game_id, _one({"kind": "effect", "id": target_id}))


def test_t074_002_unknown_effect_is_rejected(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    _assert_rejected_and_paused(
        db_engine, game_id, _one({"kind": "effect", "id": str(uuid.uuid4())})
    )


def test_t074_002_private_effect_is_a_leak_and_rejected(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    with session_scope(db_engine) as session:
        private = session.exec(
            select(Effect).where(
                Effect.game_id == game_id, Effect.visibility == "private", Effect.active == True  # noqa: E712
            )
        ).one()
        private_id = str(private.id)
    _assert_rejected_and_paused(db_engine, game_id, _one({"kind": "effect", "id": private_id}))


@pytest.mark.parametrize(
    "reference",
    [
        {"kind": "event"},
        {"kind": "event", "id": "12"},
        {"kind": "event", "id": True},
        {"kind": "effect", "id": "not-a-uuid"},
        {"kind": "message", "id": 1},
        {"kind": "event", "id": 1, "extra": 1},
    ],
)
def test_t074_002_malformed_references_are_rejected(db_engine: Engine, reference: dict) -> None:
    game_id = _to_consequences(db_engine)
    _assert_rejected_and_paused(db_engine, game_id, _one(reference))


def test_t074_002_duplicate_reference_is_rejected(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    event = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]
    _assert_rejected_and_paused(
        db_engine, game_id, _one({"kind": "event", "id": event}, {"kind": "event", "id": event})
    )


def test_t074_002_active_public_effect_is_citable(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    with session_scope(db_engine) as session:
        public = session.exec(
            select(Effect).where(
                Effect.game_id == game_id,
                Effect.visibility == "public",
                Effect.source_argument_id != None,  # noqa: E711 — a same-turn adjudicated Effect
            )
        ).first()
        public_id = str(public.id)
    outcome = game_service.resolve_consequences(
        db_engine, game_id, FakeConsequenceResolver(_one({"kind": "effect", "id": public_id}))
    )
    assert len(outcome.created_effect_ids) == 1


# --- TEST-T074-003: the bound -------------------------------------------------------------


def _n_effects(n: int, event: int) -> ConsequenceProposal:
    return ConsequenceProposal(
        effects=[
            ProposedConsequence(
                description=f"Consequence {i}.",
                rationale="Aggregate pressure.",
                evidence=[{"kind": "event", "id": event}],
            )
            for i in range(n)
        ]
    )


def test_t074_003_four_effects_are_rejected_as_a_whole(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    event = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]
    _assert_rejected_and_paused(db_engine, game_id, _n_effects(4, event))


def test_t074_003_three_effects_commit_exactly_three(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    event = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]
    outcome = game_service.resolve_consequences(
        db_engine, game_id, FakeConsequenceResolver(_n_effects(3, event))
    )
    assert len(outcome.created_effect_ids) == 3 and outcome.phase is GamePhase.consequences
    with session_scope(db_engine) as session:
        created = session.exec(
            select(Effect).where(Effect.game_id == game_id, Effect.source_consequence_turn == 1)
        ).all()
        assert len(created) == 3
        assert len(session.get(ConsequenceRecord, (game_id, 1)).effects) == 3


def test_t074_003_zero_effects_record_the_pass_and_complete_the_turn(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    outcome = game_service.resolve_consequences(
        db_engine, game_id, FakeConsequenceResolver(ConsequenceProposal(effects=[]))
    )
    assert outcome.created_effect_ids == []
    with session_scope(db_engine) as session:
        assert session.get(ConsequenceRecord, (game_id, 1)).effects == []
    advanced = game_service.advance_after_resolution(db_engine, game_id)
    assert advanced.turn_completed and advanced.turn == 2
    assert advanced.phase is GamePhase.diplomacy


@pytest.mark.parametrize(
    ("description", "rationale"),
    [("", "r"), ("   ", "r"), ("d", ""), ("x" * 4001, "r"), ("d", "y" * 4001)],
)
def test_t074_003_text_bounds_are_enforced(
    db_engine: Engine, description: str, rationale: str
) -> None:
    game_id = _to_consequences(db_engine)
    event = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]
    proposal = ConsequenceProposal(
        effects=[
            ProposedConsequence(
                description=description, rationale=rationale,
                evidence=[{"kind": "event", "id": event}],
            )
        ]
    )
    _assert_rejected_and_paused(db_engine, game_id, proposal)


def test_t074_003_nine_references_exceed_the_evidence_bound(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine, turn=2)
    refs = [
        {"kind": "event", "id": e}
        for e in _event_ids(db_engine, game_id, turn=2, type_="argument_adjudicated")
    ]
    with session_scope(db_engine) as session:
        refs += [
            {"kind": "effect", "id": str(e.id)}
            for e in session.exec(
                select(Effect).where(
                    Effect.game_id == game_id, Effect.visibility == "public", Effect.active == True  # noqa: E712
                )
            ).all()
        ]
    assert len(refs) >= 9  # nine distinct references, each individually citable
    _assert_rejected_and_paused(db_engine, game_id, _one(*refs[:9]))


# --- TEST-T074-004: `none` is the released loop -------------------------------------------


def _normalized_events(engine: Engine, game_id: uuid.UUID) -> list[tuple]:
    with session_scope(engine) as session:
        return [
            (e.turn, e.type, e.actor_id)
            for e in session.exec(
                select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
            ).all()
        ]


def test_t074_004_none_is_the_released_loop(db_engine: Engine) -> None:
    def never(_context: ConsequenceContext) -> ConsequenceProposal:
        raise AssertionError("no resolver is called under consequence_mode none")

    default_id = _create(db_engine, consequence_mode=None)  # the scenario declares nothing
    explicit_id = _create(db_engine, consequence_mode="none")
    _play(db_engine, default_id, FakeConsequenceResolver(responder=never))
    _play(db_engine, explicit_id, None)

    assert _normalized_events(db_engine, default_id) == _normalized_events(db_engine, explicit_id)
    for game_id in (default_id, explicit_id):
        game = _game(db_engine, game_id)
        assert game.status is GameStatus.complete and game.turn == 6
        assert game.consequence_mode == "none"
        types = [t for _, t, _ in _normalized_events(db_engine, game_id)]
        assert "consequences_resolved" not in types
        with session_scope(db_engine) as session:
            phases = [
                e.payload.get("phase")
                for e in session.exec(
                    select(GameEvent).where(
                        GameEvent.game_id == game_id, GameEvent.type == "phase_started"
                    )
                ).all()
            ]
            assert "consequences" not in phases
            assert session.exec(
                select(ConsequenceRecord).where(ConsequenceRecord.game_id == game_id)
            ).all() == []
            assert all(
                e.source_consequence_turn is None
                for e in session.exec(select(Effect).where(Effect.game_id == game_id)).all()
            )
        # The released turn boundary: the last adjudication's events, then turn_completed.
        for turn in range(1, 7):
            turn_types = [t for tn, t, _ in _normalized_events(db_engine, game_id) if tn == turn]
            last = len(turn_types) - 1 - turn_types[::-1].index("argument_adjudicated")
            assert set(turn_types[last + 1 : turn_types.index("turn_completed")]) <= {
                "effect_created", "effect_deactivated"
            }

    # The golden fixture is untouched and declares no condition.
    assert "consequence_mode" not in (FIXTURE_DIR / "scenario.yaml").read_text(encoding="utf-8")
    # The provenance of a `none` game keeps the released shape: no consequence keys.
    from app.game_loop import GameLoopCondition

    condition = GameLoopCondition()
    assert "consequence_mode" not in condition.as_dict()
    assert not any(key.startswith("consequence") for key in condition.provenance())


# --- TEST-T074-005: no extra Actor opportunity -------------------------------------------


def _refused(call) -> bool:  # noqa: ANN001
    try:
        call()
    except game_service.GameServiceError:
        return True
    return False


def test_t074_005_consequences_phase_refuses_every_actor_mutation(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    game = _game(db_engine, game_id)
    assert game.active_actor_id is None
    with session_scope(db_engine) as session:
        argument_id = session.exec(
            select(GameEvent).where(
                GameEvent.game_id == game_id, GameEvent.type == "argument_submitted"
            )
        ).first().payload["argument_id"]
    for actor in game.turn_order:
        assert _refused(lambda: game_service.send_message(
            db_engine, game_id, sender_actor_id=actor, text="hi", public=True, recipients=[]
        ))
        assert _refused(lambda: game_service.ready(db_engine, game_id, actor))
        assert _refused(lambda: game_service.make_argument(
            db_engine, game_id, actor_id=actor, action="a", desired_effect="d", pros=["p"]
        ))
        assert _refused(lambda: game_service.make_con(
            db_engine, game_id, argument_id=uuid.UUID(argument_id), actor_id=actor, reason="r"
        ))
        assert _refused(lambda: game_service.pass_con(
            db_engine, game_id, argument_id=uuid.UUID(argument_id), actor_id=actor
        ))
        view = context_module.actor_context(db_engine, game_id, actor)
        assert view["requires_input"] is False
        assert not {"send_message", "ready", "make_argument", "make_con", "pass_con",
                    "reply_to_cons"} & set(view["available_actions"])


def test_t074_005_no_actor_event_between_last_adjudication_and_turn_completion(
    db_engine: Engine,
) -> None:
    game_id = _create(db_engine)
    _play(db_engine, game_id, deterministic_consequence_resolver())
    events = _normalized_events(db_engine, game_id)
    for turn in range(1, 7):
        types = [t for tn, t, _ in events if tn == turn]
        last = len(types) - 1 - types[::-1].index("argument_adjudicated")
        between = types[last + 1 : types.index("turn_completed")]
        assert set(between) <= {
            "effect_created", "effect_deactivated", "phase_started", "consequences_resolved"
        }
        assert between.count("consequences_resolved") == 1


# --- TEST-T074-006: resume ---------------------------------------------------------------


def test_t074_006_interrupted_after_the_commit_resumes_by_advancing_only(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    event = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]
    game_service.resolve_consequences(
        db_engine, game_id, FakeConsequenceResolver(_one({"kind": "event", "id": event}))
    )
    # "Crash" here: the pass is committed, the turn is not advanced.
    assert _game(db_engine, game_id).phase is GamePhase.consequences
    assert game_service.consequence_pending(db_engine, game_id) is False
    with pytest.raises(game_service.ConsequencesAlreadyResolved):
        game_service.resolve_consequences(
            db_engine, game_id, FakeConsequenceResolver(ConsequenceProposal(effects=[]))
        )

    fresh = deterministic_consequence_resolver()
    _play(db_engine, game_id, fresh)  # a new process resumes from durable state
    with session_scope(db_engine) as session:
        record = session.get(ConsequenceRecord, (game_id, 1))
        assert len(record.effects) == 1  # the committed pass, never re-run
    assert [c.turn for c in fresh.contexts] == [2, 3, 4, 5, 6]
    assert _game(db_engine, game_id).status is GameStatus.complete


def _recipe(consequence_mode: str) -> dict:
    recipe = {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "model/p", "belvar": "model/p", "cestia": "model/p"},
            "temperature": 0,
            "max_tokens": 1000,
            "timeout": 30,
            "reasoning_effort": None,
            "activation_mode": "single_shot",
            "prompt_version": "player-v1.6",
            "guardrails": dataclasses.asdict(GuardrailConfig()),
        },
        "umpire": {
            "model": "model/u", "temperature": 0, "max_tokens": 1000, "timeout": 30,
            "reasoning_effort": None,
        },
        "coordinator": {"max_cycles": 1000, "cons_concurrency": 2, "diplomacy_concurrency": 2},
    }
    if consequence_mode != "none":
        recipe["coordinator"]["consequence_mode"] = consequence_mode
        recipe["consequence"] = {
            "model": "model/c", "temperature": None, "max_tokens": None, "timeout": 30,
            "reasoning_effort": None, "prompt_version": CONSEQUENCE_PROMPT_VERSION,
        }
    return recipe


def _seal(engine: Engine, game_id: uuid.UUID, recipe: dict) -> None:
    manifest = {
        "game_id": str(game_id), "scenario": {"id": "deterministic-game"}, "seed": 424242,
        "player": {}, "umpire": {}, "code_version": "test-code", "benchmark": {},
        "resume": recipe,
    }
    with session_scope(engine) as session:
        session.add(RunManifest(game_id=game_id, manifest=manifest, manifest_sha256="a" * 64))


def test_t074_006_unavailable_pause_resumes_and_reruns_the_pass_once(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    _seal(db_engine, game_id, _recipe(BOUNDED))
    before = _state(db_engine, game_id)

    def outage(_context: ConsequenceContext) -> ConsequenceProposal:
        raise ConsequenceResolverUnavailable("provider down")

    with pytest.raises(game_service.ConsequencesUnavailable):
        game_service.resolve_consequences(
            db_engine, game_id, FakeConsequenceResolver(responder=outage)
        )
    paused = _game(db_engine, game_id)
    assert paused.status is GameStatus.paused and paused.phase is GamePhase.consequences
    after = _state(db_engine, game_id)
    assert after["records"] == before["records"] and after["effects"] == before["effects"]
    assert after["rng_draw_index"] == before["rng_draw_index"]
    assert game_service.consequence_pending(db_engine, game_id) is False  # paused, not running

    resumption.rotate_seat_tokens(db_engine, game_id)  # the admin resume path (I-048)
    assert _game(db_engine, game_id).status is GameStatus.running
    assert game_service.consequence_pending(db_engine, game_id) is True
    rerun = deterministic_consequence_resolver()
    _play(db_engine, game_id, rerun)
    assert [c.turn for c in rerun.contexts] == [1, 2, 3, 4, 5, 6]  # turn 1 exactly once more
    with session_scope(db_engine) as session:
        assert len(session.exec(
            select(ConsequenceRecord).where(ConsequenceRecord.game_id == game_id)
        ).all()) == 6


def test_t074_006_resume_recipe_must_match_the_consequence_condition(db_engine: Engine) -> None:
    bounded = _to_consequences(db_engine)
    _seal(db_engine, bounded, _recipe("none"))
    with pytest.raises(resumption.ResumeError, match="Consequence condition"):
        resumption.describe(db_engine, bounded)

    incomplete = _to_consequences(db_engine)
    recipe = _recipe(BOUNDED)
    del recipe["consequence"]
    _seal(db_engine, incomplete, recipe)
    with pytest.raises(resumption.ResumeError, match="consequence-resolver"):
        resumption.describe(db_engine, incomplete)

    released = _create(db_engine, consequence_mode="none")
    _seal(db_engine, released, _recipe("none"))  # a legacy-shaped recipe carries no key
    assert resumption.describe(db_engine, released).game_id == released


# --- TEST-T074-007: no RNG and no hidden input --------------------------------------------


def test_t074_007_the_pass_draws_no_rng_and_sees_nothing_hidden(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    draws = _game(db_engine, game_id).rng_draw_index
    resolver = deterministic_consequence_resolver()
    game_service.resolve_consequences(db_engine, game_id, resolver)
    assert _game(db_engine, game_id).rng_draw_index == draws  # INV-CONSEQ-004

    [ctx] = resolver.contexts
    rendered = json.dumps(ctx.as_payload())
    with session_scope(db_engine) as session:
        hidden: list[str] = []
        for actor in session.exec(select(Actor).where(Actor.game_id == game_id)).all():
            hidden += list(actor.private_information) + [actor.private_brief.strip()]
            hidden += list(actor.objectives)
        hidden += [
            e.description
            for e in session.exec(select(Effect).where(Effect.game_id == game_id)).all()
            if e.visibility == "private"
        ]
        hidden += [
            m.text
            for m in session.exec(select(Message).where(Message.game_id == game_id)).all()
            if not m.public
        ]
    hidden += ["questions the wisdom", "presses its case", "Arkanian Intelligence Estimate"]
    assert hidden
    for secret in hidden:
        assert secret not in rendered, secret
    assert set(ctx.as_payload()) == {
        "scenario_summary", "turn", "turn_length", "outcomes", "active_effects"
    }
    assert {key for o in ctx.outcomes for key in o} == {
        "event_id", "actor_id", "action", "desired_effect", "mode", "success",
        "created_effect_ids",
    }
    assert all(set(e) == {"id", "description", "created_turn"} for e in ctx.active_effects)


def test_t074_007_scenario_rules_reach_the_resolver_when_declared(
    db_engine: Engine, tmp_path: Path
) -> None:
    package = tmp_path / "ruled"
    shutil.copytree(FIXTURE_DIR, package)
    manifest = package / "scenario.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + '\nrules:\n  - id: "no-overflight"\n    text: "No overflight without a vote."\n',
        encoding="utf-8",
    )
    creation = scenarios.create_game_from_scenario(
        db_engine, scenarios.load_scenario(package), consequence_mode=BOUNDED
    )
    game_service.start_game(db_engine, creation.game_id)
    game_id = _to_consequences(db_engine, game_id=creation.game_id)
    resolver = deterministic_consequence_resolver()
    game_service.resolve_consequences(db_engine, game_id, resolver)
    assert resolver.contexts[0].as_payload()["scenario_rules"] == [
        {"id": "no-overflight", "text": "No overflight without a vote."}
    ]


# --- TEST-T074-008: full offline games in both modes -------------------------------------


@pytest.mark.parametrize("mode", ["none", BOUNDED])
def test_t074_008_six_turn_games_complete_offline_in_both_modes(
    db_engine: Engine, mode: str
) -> None:
    game_id = _create(db_engine, consequence_mode=mode)
    _play(db_engine, game_id, deterministic_consequence_resolver())
    game = _game(db_engine, game_id)
    assert game.status is GameStatus.complete and game.turn == 6
    assert game.rng_draw_index == 18  # one adjudication draw per Argument, none for consequences
    exported = export_module.build_game_export(db_engine, game_id)
    consequence_effects = [e for e in exported["effects"] if e["source_consequence_turn"]]
    if mode == "none":
        assert exported["consequence_records"] == [] and consequence_effects == []
    else:
        assert [r["turn"] for r in exported["consequence_records"]] == [1, 2, 3, 4, 5, 6]
        assert len(consequence_effects) == 6
        assert all(e["visibility"] == "public" for e in consequence_effects)


def test_t074_008_scenario_declared_mode_and_explicit_override(
    db_engine: Engine, tmp_path: Path
) -> None:
    package = tmp_path / "declared"
    shutil.copytree(FIXTURE_DIR, package)
    manifest = package / "scenario.yaml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(
        text.replace("  max_cons_per_actor: 3\n", "  max_cons_per_actor: 3\n  consequence_mode: bounded_effects_v1\n"),
        encoding="utf-8",
    )
    loaded = scenarios.load_scenario(package)
    assert loaded.game.consequence_mode == BOUNDED
    assert loaded.scenario_content_sha256 != scenarios.load_scenario(FIXTURE_DIR).scenario_content_sha256
    declared = scenarios.create_game_from_scenario(db_engine, loaded)
    overridden = scenarios.create_game_from_scenario(db_engine, loaded, consequence_mode="none")
    assert _game(db_engine, declared.game_id).consequence_mode == BOUNDED
    assert _game(db_engine, overridden.game_id).consequence_mode == "none"

    bad = tmp_path / "bad"
    shutil.copytree(package, bad)
    (bad / "scenario.yaml").write_text(
        (bad / "scenario.yaml").read_text(encoding="utf-8").replace(BOUNDED, "unbounded"),
        encoding="utf-8",
    )
    with pytest.raises(scenarios.ScenarioError):
        scenarios.load_scenario(bad)
    with pytest.raises(scenarios.GameCreationError, match="consequence_mode"):
        scenarios.create_game_from_scenario(db_engine, loaded, consequence_mode="sometimes")


def test_t074_008_autonomous_coordinator_runs_one_pass_per_turn_over_http(
    db_engine: Engine, short_game: Path,
) -> None:
    from agents.autonomous import AutonomousGameRunner
    from agents.lifecycle import LifecycleRunner
    from agents.player import PlayerConfig, ReferencePlayer
    from tests.agent_fakes import default_decision_policy, model_from_decision_policy
    from tests.benchmark_helpers import SHORT_TURNS

    def player_factory(tools, actor_id: str, actor_name: str) -> LifecycleRunner:  # noqa: ANN001
        return LifecycleRunner(ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_decision_policy(default_decision_policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="single_shot"),
        ))

    resolver = deterministic_consequence_resolver()
    runner = AutonomousGameRunner(
        _admin_client(db_engine, resolver),
        admin_token=ADMIN,
        scenario=short_game,
        player_factory=player_factory,
        seed=20260923,
        consequence_mode=BOUNDED,
    )
    result = runner.run()
    exported = result.export
    assert exported["game"]["status"] == "complete"
    turns = list(range(1, SHORT_TURNS + 1))
    assert [r["turn"] for r in exported["consequence_records"]] == turns
    assert [c.turn for c in resolver.contexts] == turns
    manifest = exported["run_manifest"]
    assert manifest["game_loop"]["consequence_mode"] == BOUNDED
    assert manifest["game_loop"]["consequence_policy_version"] == "consequence-policy-v1"
    assert manifest["consequence"] == resolver.provenance_configuration()
    # No Actor is activated during a consequence pass (INV-CONSEQ-004).
    assert not [
        run for run in exported["agent_runs"]
        if run["role"] == "player" and run["phase"] == "consequences"
    ]


# --- TEST-T074-009: the LLM resolver, offline, and its provenance ------------------------


def _completion(content: str, *, provider: str = "TestProvider") -> dict:
    return {
        "id": "gen-consequence-1",
        "model": "test/consequence",
        "provider": provider,
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
    }


def _ai_resolver(handler) -> AIConsequenceResolver:  # noqa: ANN001
    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIConsequenceResolver(client, model="test/consequence")


def _context() -> ConsequenceContext:
    return ConsequenceContext(
        scenario_summary="A strait crisis.",
        turn=1,
        turn_length="1 week",
        outcomes=[
            {"event_id": 11, "actor_id": "arkania", "action": "a", "desired_effect": "d",
             "mode": "probabilistic", "success": True, "created_effect_ids": []},
            {"event_id": 14, "actor_id": "belvar", "action": "a", "desired_effect": "d",
             "mode": "automatic_success", "success": True, "created_effect_ids": []},
        ],
        active_effects=[{"id": "7b0c7f0e-0000-4000-8000-000000000001",
                         "description": "Tension.", "created_turn": 0}],
    )


def test_t074_009_llm_resolver_sends_the_versioned_prompt_under_a_strict_schema() -> None:
    requests: list[dict] = []
    output = {"effects": [{"description": "Premiums rise.", "rationale": "Both moves.",
                           "evidence": [{"kind": "event", "id": 11}, {"kind": "event", "id": 14}]}]}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(json.dumps(output)))

    resolver = _ai_resolver(handler)
    result = resolver.propose_recorded(_context())
    assert result.failure is None
    [effect] = result.proposal.effects
    assert effect.evidence == [{"kind": "event", "id": 11}, {"kind": "event", "id": 14}]

    [body] = requests
    assert body["messages"][0] == {"role": "system", "content": load_consequence_prompt()}
    assert json.loads(body["messages"][1]["content"]) == _context().as_payload()
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert body["provider"]["require_parameters"] is True
    items = schema["schema"]["properties"]["effects"]
    assert items["maxItems"] == 3
    branches = items["items"]["properties"]["evidence"]["items"]["anyOf"]
    assert branches[0]["properties"]["id"]["enum"] == [11, 14]
    assert branches[1]["properties"]["id"]["enum"] == [_context().active_effects[0]["id"]]

    [attempt] = result.attempts
    assert attempt["prompt_version"] == CONSEQUENCE_PROMPT_VERSION == "consequence-v1.1"
    assert attempt["completed"] is True and attempt["tool_schema_version"] == "consequence-output-v1"
    assert attempt["input_tokens"] == 20
    serialized = json.dumps(attempt)
    assert "Premiums rise" not in serialized and "strait crisis" not in serialized
    assert "sk-test" not in serialized
    assert resolver.provenance_configuration()["prompt_version"] == "consequence-v1.1"
    assert resolver.provenance_configuration()["settings"]["attempts"] == CONSEQUENCE_MAX_ATTEMPTS


def test_t074_009_context_invalid_output_retries_then_becomes_unavailable() -> None:
    calls: list[int] = []
    bad = {"effects": [{"description": "x", "rationale": "y",
                        "evidence": [{"kind": "event", "id": 999}]}]}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_completion(json.dumps(bad), provider=f"P{len(calls)}"))

    resolver = _ai_resolver(handler)
    result = resolver.propose_recorded(_context())
    assert result.proposal is None and len(calls) == CONSEQUENCE_MAX_ATTEMPTS
    assert len(result.attempts) == CONSEQUENCE_MAX_ATTEMPTS
    assert all(a["completed"] is False for a in result.attempts)
    assert result.attempts[0]["provider_metadata"]["semantic_failure"]["code"] == (
        "context_invalid_evidence"
    )
    with pytest.raises(ConsequenceResolverUnavailable):
        resolver.propose(_context())


def test_t074_009_recovered_retry_and_transient_outage() -> None:
    outputs = iter(["not json", json.dumps({"effects": []})])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(next(outputs)))

    result = _ai_resolver(handler).propose_recorded(_context())
    assert result.proposal == ConsequenceProposal(effects=[])
    assert [a["completed"] for a in result.attempts] == [False, True]

    def down(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    result = _ai_resolver(down).propose_recorded(_context())
    assert result.proposal is None and result.failure.startswith("provider:")
    assert len(result.attempts) == CONSEQUENCE_MAX_ATTEMPTS


def _admin_client(engine: Engine, resolver) -> TestClient:  # noqa: ANN001
    return TestClient(
        create_app(
            engine=engine,
            settings=Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None),
            umpire=deterministic_umpire(),
            consequence_resolver=resolver,
        ),
        raise_server_exceptions=False,
    )


def test_t074_009_advance_route_runs_the_pass_and_records_attempts(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    events = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")
    good = {"effects": [{"description": "Premiums rise.", "rationale": "Both moves.",
                         "evidence": [{"kind": "event", "id": events[0]},
                                      {"kind": "event", "id": events[1]}]}]}
    resolver = _ai_resolver(lambda _r: httpx.Response(200, json=_completion(json.dumps(good))))
    client = _admin_client(db_engine, resolver)
    headers = {"Authorization": f"Bearer {ADMIN}"}

    response = client.post(f"/admin/games/{game_id}/advance", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["turn_completed"] is True and response.json()["turn"] == 2
    with session_scope(db_engine) as session:
        [run] = session.exec(
            select(AgentRun).where(AgentRun.game_id == game_id, AgentRun.role == "consequence")
        ).all()
        assert run.turn == 1 and run.phase == "consequences" and run.actor_id is None
        assert run.completed and run.prompt_version == "consequence-v1.1"
        assert len(session.get(ConsequenceRecord, (game_id, 1)).effects) == 1


def test_t074_009_advance_route_pauses_with_503_when_unavailable(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine)
    down = _ai_resolver(lambda _r: httpx.Response(503, json={"error": {"message": "down"}}))
    client = _admin_client(db_engine, down)
    headers = {"Authorization": f"Bearer {ADMIN}"}
    response = client.post(f"/admin/games/{game_id}/advance", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "consequence_resolver_unavailable"
    assert _game(db_engine, game_id).status is GameStatus.paused
    with session_scope(db_engine) as session:
        runs = session.exec(
            select(AgentRun).where(AgentRun.game_id == game_id, AgentRun.role == "consequence")
        ).all()
        assert len(runs) == CONSEQUENCE_MAX_ATTEMPTS and not any(r.completed for r in runs)
        assert session.get(ConsequenceRecord, (game_id, 1)) is None

    missing = _to_consequences(db_engine)
    response = _admin_client(db_engine, None).post(
        f"/admin/games/{missing}/advance", headers=headers
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "consequence_resolver_unavailable"
    assert _game(db_engine, missing).status is GameStatus.running  # nothing committed


def test_t074_009_a_rejected_proposal_marks_its_attempt_unusable(db_engine: Engine) -> None:
    game_id = _to_consequences(db_engine, turn=2)
    stale = _event_ids(db_engine, game_id, turn=1, type_="argument_adjudicated")[0]

    class StaleButContextual(FakeConsequenceResolver):
        def propose_recorded(self, context):  # noqa: ANN001
            result = super().propose_recorded(context)
            attempt = {
                "model_requested": "m", "model_returned": "m", "provider": "p",
                "prompt_version": "consequence-v1.1", "prompt_hash": "h" * 64,
                "model_parameters": {}, "reasoning_configuration": None,
                "agent_framework": "httpx", "agent_framework_version": None,
                "tool_schema_version": "consequence-output-v1", "input_tokens": 1,
                "output_tokens": 1, "reasoning_tokens": 0, "cache_read_tokens": 0,
                "cache_write_tokens": 0, "model_calls": 1, "costed_model_calls": 0,
                "tool_calls": 0, "invalid_tool_calls": 0, "stalls": 0, "cost_usd": None,
                "latency_ms": 1, "activation_wall_ms": 1, "completed": True,
                "failure_reason": None, "provider_metadata": {},
            }
            return dataclasses.replace(result, attempts=(attempt,))

    client = _admin_client(db_engine, StaleButContextual(_one({"kind": "event", "id": stale})))
    response = client.post(
        f"/admin/games/{game_id}/advance", headers={"Authorization": f"Bearer {ADMIN}"}
    )
    assert response.status_code == 503
    with session_scope(db_engine) as session:
        [run] = session.exec(
            select(AgentRun).where(AgentRun.game_id == game_id, AgentRun.role == "consequence")
        ).all()
        assert run.completed is False and run.failure_reason == "ConsequencesUnavailable"


def test_t074_009_cli_defaults_the_resolver_and_seals_the_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)

    # An unset resolver model is the Umpire model (D-115; D-091 refused it).
    result = invoke_run(FIXTURE_DIR, "--consequence-mode", BOUNDED)
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["run_configuration"]["resume"]["consequence"]["model"] == "u/m"

    result = invoke_run(FIXTURE_DIR, "--consequence-model", "c/m")
    assert result.exit_code == 2 and "requires consequence_mode" in result.output

    result = invoke_run(FIXTURE_DIR, "--consequence-mode", BOUNDED, "--consequence-model", "c/m")
    assert result.exit_code == 0, result.output
    runner = captured["runner_options"]
    assert runner["consequence_mode"] == BOUNDED
    recipe = runner["run_configuration"]["resume"]
    assert recipe["coordinator"]["consequence_mode"] == BOUNDED
    assert recipe["consequence"] == {
        "model": "c/m", "temperature": None, "max_tokens": None,
        "timeout": recipe["consequence"]["timeout"], "reasoning_effort": None,
        "prompt_version": "consequence-v1.1",
    }
    assert captured["resolver_options"]["model"] == "c/m"
    assert captured["resolver_options"]["prompt_version"] == "consequence-v1.1"

    # The released default: no condition key, no resolver section, no resolver built.
    captured.clear()
    result = invoke_run(FIXTURE_DIR)
    assert result.exit_code == 0, result.output
    recipe = captured["runner_options"]["run_configuration"]["resume"]
    assert "consequence" not in recipe and "consequence_mode" not in recipe["coordinator"]
    assert "consequence_mode" not in captured["runner_options"]
    assert "resolver_options" not in captured

    # A scenario that declares the mode takes the Umpire model, or the environment's.
    declared = tmp_path / "declared"
    shutil.copytree(FIXTURE_DIR, declared)
    manifest = declared / "scenario.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  max_cons_per_actor: 3\n", f"  max_cons_per_actor: 3\n  consequence_mode: {BOUNDED}\n"
        ),
        encoding="utf-8",
    )
    captured.clear()
    assert invoke_run(declared).exit_code == 0
    assert captured["runner_options"]["run_configuration"]["resume"]["consequence"]["model"] == "u/m"
    monkeypatch.setenv("AMAW_CONSEQUENCE_MODEL", "env/c")
    result = invoke_run(declared)
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["run_configuration"]["resume"]["consequence"]["model"] == "env/c"
    # --consequence-mode none overrides the scenario (with no resolver model left configured).
    monkeypatch.delenv("AMAW_CONSEQUENCE_MODEL")
    captured.clear()
    result = invoke_run(declared, "--consequence-mode", "none")
    assert result.exit_code == 0, result.output
    assert "consequence" not in captured["runner_options"]["run_configuration"]["resume"]

    # Batches resolve the condition as `run` does, defaulting the resolver model the same way
    # (TEST-I114-004, tests/test_benchmark_consequence_condition.py).


# --- provider smoke (owner-approved budget only) -----------------------------------------


@pytest.mark.llm
def test_t074_llm_smoke_one_end_of_turn_pass_per_turn(db_engine: Engine) -> None:
    """A two-turn `bounded_effects_v1` fixture game with the live resolver: one pass per turn."""
    settings = Settings()  # reads the environment and .env, like every provider smoke
    if settings.openrouter_api_key is None or not settings.openrouter_api_key.get_secret_value():
        pytest.skip("OPENROUTER_API_KEY is not configured")
    model = settings.consequence_model or settings.umpire_model
    if not model:
        pytest.skip("AMAW_CONSEQUENCE_MODEL (or AMAW_UMPIRE_MODEL) is not configured")
    resolver = AIConsequenceResolver.for_openrouter(
        settings, model=model, timeout=settings.request_timeout_seconds
    )
    game_id = _create(db_engine)
    for turn in (1, 2):
        _to_consequences(db_engine, turn=turn, game_id=game_id)
        game_service.resolve_consequences(db_engine, game_id, resolver)  # the live pass
        game_service.advance_after_resolution(db_engine, game_id)
    with session_scope(db_engine) as session:
        records = session.exec(
            select(ConsequenceRecord)
            .where(ConsequenceRecord.game_id == game_id)
            .order_by(ConsequenceRecord.turn)
        ).all()
        assert [r.turn for r in records] == [1, 2]  # exactly one end-of-turn pass per turn
        assert all(len(r.effects) <= 3 for r in records)
        assert all(ref for r in records for entry in r.effects for ref in entry["evidence"])
    assert _game(db_engine, game_id).rng_draw_index == 6  # adjudications only
