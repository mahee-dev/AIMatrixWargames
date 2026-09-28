"""Golden outcomes of the canonical six-turn fixture game (testing.md §18, re-checked by §19).

§18 plays the fixture through the service; §19 plays the Actor half over HTTP and must reach the
same outcomes. Both call these assertions, so the two layers cannot drift apart. Expectations are
derived from the scenario and the seed, never hard-coded floats or ids.
"""

from __future__ import annotations

import random
import uuid
from collections import Counter
from collections.abc import Sequence

from sqlalchemy import Engine
from sqlmodel import select

from agents.scripted import effect_description, superseded_effect_description
from app import scenarios
from app.database import session_scope
from app.models import AdjudicationMode, AdjudicationRecord, Argument, Effect, Game, GameEvent

TURNS = 6


def assert_arguments_and_budget(engine: Engine, game_id: uuid.UUID, turn_order: Sequence[str]) -> None:
    """18 primary Arguments, exactly one per (turn, Actor): no budget violation."""
    with session_scope(engine) as session:
        rows = [
            (a.turn, a.actor_id)
            for a in session.exec(select(Argument).where(Argument.game_id == game_id)).all()
        ]
    assert len(rows) == len(turn_order) * TURNS
    assert set(Counter(rows).values()) == {1}
    assert {actor_id for _, actor_id in rows} == set(turn_order)
    assert {turn for turn, _ in rows} == set(range(1, TURNS + 1))


def assert_adjudications(engine: Engine, game_id: uuid.UUID) -> None:
    """Exactly one adjudication record per Argument, and every Argument resolved."""
    with session_scope(engine) as session:
        arguments = session.exec(select(Argument).where(Argument.game_id == game_id)).all()
        argument_ids = {a.id for a in arguments}
        statuses = {a.status for a in arguments}
        record_ids = {r.argument_id for r in session.exec(select(AdjudicationRecord)).all()}
    assert len(record_ids) == 18
    assert argument_ids == record_ids
    assert statuses == {"resolved"}


def assert_rng_sequence(engine: Engine, game_id: uuid.UUID, turn_order: Sequence[str]) -> None:
    """Every adjudication is probabilistic and the rolls are the seed's stream in draw order."""
    order = list(turn_order)
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        assert game is not None
        seed, final_index = game.seed, game.rng_draw_index
        args = {
            a.id: (a.turn, a.actor_id)
            for a in session.exec(select(Argument).where(Argument.game_id == game_id)).all()
        }
        # (mode, roll) keyed to the (turn, turn-order position) draw order the server used.
        draws = sorted(
            (args[r.argument_id][0], order.index(args[r.argument_id][1]), r.mode, r.roll)
            for r in session.exec(select(AdjudicationRecord)).all()
        )

    assert all(mode is AdjudicationMode.probabilistic for *_, mode, _ in draws)
    assert final_index == 18
    generator = random.Random(seed)
    assert [roll for *_, roll in draws] == [generator.random() for _ in range(18)]


def assert_final_effects(
    engine: Engine, game_id: uuid.UUID, scenario: scenarios.LoadedScenario
) -> None:
    """The final active Effects, with superseded and expired rows kept inactive (INV-EFFECT-004).

    Every (turn, Actor) adjudication succeeds, so each creates one Effect, minus the one
    superseded at the last turn. The two public starting Effects stay active; the private
    arkania one expires at turn 3.
    """
    with session_scope(engine) as session:
        effects = [
            (e.description, e.active, e.visibility, tuple(e.visible_to))
            for e in session.exec(select(Effect).where(Effect.game_id == game_id)).all()
        ]

    created = {
        effect_description(actor, turn)
        for turn in range(1, TURNS + 1)
        for actor in scenario.game.turn_order
    }
    superseded = superseded_effect_description()
    public_starting = {e.description for e in scenario.starting_effects if e.visibility == "public"}
    active = {description for description, is_active, *_ in effects if is_active}
    assert active == (created - {superseded}) | public_starting

    assert len(effects) == 3 + 18  # 3 starting + 18 created; nothing deleted
    superseded_rows = [e for e in effects if e[0] == superseded]
    assert len(superseded_rows) == 1 and superseded_rows[0][1] is False
    private_starting = [e for e in effects if e[2] == "private" and "arkania" in e[3]]
    assert len(private_starting) == 1 and private_starting[0][1] is False


def assert_event_structure(engine: Engine, game_id: uuid.UUID) -> list[int]:
    """The event stream is monotonic, bracketed and causally ordered (§24); returns its ids."""
    with session_scope(engine) as session:
        events = [
            (e.id, e.type, dict(e.payload))
            for e in session.exec(
                select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
            ).all()
        ]

    assert events, "no events emitted"
    ids = [event_id for event_id, *_ in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert events[0][1] == "game_started"
    assert events[-1][1] == "game_completed"

    counts = Counter(event_type for _, event_type, _ in events)
    assert counts["game_started"] == 1
    assert counts["game_completed"] == 1
    assert counts["turn_completed"] == TURNS
    assert counts["argument_submitted"] == 18
    assert counts["argument_adjudicated"] == 18
    assert counts["effect_created"] == 18  # one success Effect per adjudication
    assert counts["phase_started"] >= TURNS  # at least one Diplomacy opening per turn

    # Every adjudication is preceded by its Argument's submission.
    submitted_at = {
        payload["argument_id"]: position
        for position, (_, event_type, payload) in enumerate(events)
        if event_type == "argument_submitted"
    }
    for position, (_, event_type, payload) in enumerate(events):
        if event_type == "argument_adjudicated":
            assert submitted_at.get(payload["argument_id"], len(events)) < position
    return ids
