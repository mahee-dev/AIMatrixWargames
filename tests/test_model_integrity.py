"""Regression tests for I-003's pre-T-007 persistence corrections."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app.models import (
    Actor,
    AdjudicationMode,
    AdjudicationRecord,
    Argument,
    ConParticipation,
    Effect,
    Game,
    Seat,
)


@pytest.fixture
def session(db_engine):
    SQLModel.metadata.create_all(db_engine)
    with Session(db_engine) as value:
        yield value


def _game(scenario_id: str) -> Game:
    return Game(scenario_id=scenario_id, max_turns=6, seed=42)


def _game_with_actor(session: Session, actor_id: str = "arkania") -> Game:
    """A committed Game with one Actor — I-082 enforces the Argument's parent relationship."""
    game = _game("scenario")
    session.add(game)
    session.commit()
    session.add(
        Actor(
            game_id=game.id,
            id=actor_id,
            name=actor_id.title(),
            public_description="public",
            private_brief="private",
        )
    )
    session.commit()
    return game


def test_actor_ids_are_scoped_to_game(session: Session) -> None:
    first = _game("scenario")
    second = _game("scenario")
    session.add(first)
    session.add(second)
    session.commit()

    session.add(
        Actor(
            game_id=first.id,
            id="arkania",
            name="Arkania",
            public_description="public",
            private_brief="private",
        )
    )
    session.add(
        Actor(
            game_id=second.id,
            id="arkania",
            name="Arkania",
            public_description="public",
            private_brief="private",
        )
    )
    session.commit()

    actors = session.exec(select(Actor).where(Actor.id == "arkania")).all()
    assert {actor.game_id for actor in actors} == {first.id, second.id}


def test_game_execution_configuration_and_runtime_state_roundtrip(session: Session) -> None:
    game = Game(
        scenario_id="scenario",
        max_turns=6,
        seed=42,
        rng_draw_index=3,
        primary_arguments_per_actor=2,
        max_cons_per_actor=4,
        in_world_turn_length="2 weeks",
        diplomacy_timeout_seconds=120,
        cons_timeout_seconds=60,
        diplomacy_ready_actor_ids=["arkania", "belvar"],
    )
    session.add(game)
    session.commit()
    game_id = game.id
    session.expunge_all()

    loaded = session.get(Game, game_id)
    assert loaded is not None
    assert loaded.rng_draw_index == 3
    assert loaded.primary_arguments_per_actor == 2
    assert loaded.max_cons_per_actor == 4
    assert loaded.in_world_turn_length == "2 weeks"
    assert loaded.diplomacy_timeout_seconds == 120
    assert loaded.cons_timeout_seconds == 60
    assert loaded.diplomacy_ready_actor_ids == ["arkania", "belvar"]


def test_one_seat_per_game_actor_is_database_enforced(session: Session) -> None:
    game = _game("scenario")
    session.add(game)
    session.commit()
    session.add(
        Actor(
            game_id=game.id,
            id="arkania",
            name="Arkania",
            public_description="public",
            private_brief="private",
        )
    )
    session.commit()
    session.add(Seat(game_id=game.id, actor_id="arkania", token_hash="hash:one"))
    session.commit()
    session.add(Seat(game_id=game.id, actor_id="arkania", token_hash="hash:two"))

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_con_completion_is_unique_per_argument_and_actor(session: Session) -> None:
    game = _game_with_actor(session)
    argument = Argument(
        game_id=game.id,
        turn=1,
        actor_id="arkania",
        action="Act",
        desired_effect="Effect",
        pros=["Reason"],
    )
    session.add(argument)
    session.commit()
    session.add(ConParticipation(argument_id=argument.id, actor_id="belvar"))
    session.commit()
    session.add(ConParticipation(argument_id=argument.id, actor_id="belvar"))

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_argument_budget_slots_are_unique_per_game_turn_actor(session: Session) -> None:
    game = _game_with_actor(session)
    first = Argument(
        game_id=game.id,
        turn=1,
        actor_id="arkania",
        budget_slot=1,
        action="First",
        desired_effect="First effect",
        pros=["Reason"],
    )
    session.add(first)
    session.commit()
    session.add(
        Argument(
            game_id=game.id,
            turn=1,
            actor_id="arkania",
            budget_slot=1,
            action="Duplicate slot",
            desired_effect="Duplicate effect",
            pros=["Reason"],
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    session.add(
        Argument(
            game_id=game.id,
            turn=1,
            actor_id="arkania",
            budget_slot=2,
            action="Second",
            desired_effect="Second effect",
            pros=["Reason"],
        )
    )
    session.commit()


def test_one_adjudication_record_per_argument_roundtrips(session: Session) -> None:
    game = _game_with_actor(session)
    argument = Argument(
        game_id=game.id,
        turn=1,
        actor_id="arkania",
        action="Act",
        desired_effect="Effect",
        pros=["Reason"],
    )
    session.add(argument)
    session.commit()
    superseded_id = uuid.uuid4()
    record = AdjudicationRecord(
        argument_id=argument.id,
        mode=AdjudicationMode.probabilistic,
        probability=0.7,
        rationale="Plausible but contested.",
        success_effects=["Success"],
        failure_effects=["Failure"],
        supersedes_effect_ids=[superseded_id],
        roll=0.2,
        success=True,
        raw_output={"mode": "probabilistic", "probability": 0.7},
    )
    session.add(record)
    session.commit()
    argument_id = argument.id
    session.expunge_all()

    loaded = session.get(AdjudicationRecord, argument_id)
    assert loaded is not None
    assert loaded.mode is AdjudicationMode.probabilistic
    assert loaded.supersedes_effect_ids == [superseded_id]
    assert isinstance(loaded.supersedes_effect_ids[0], uuid.UUID)
    assert loaded.roll == 0.2
    assert loaded.success is True

    session.expunge(loaded)
    session.add(
        AdjudicationRecord(
            argument_id=argument_id,
            mode=AdjudicationMode.automatic_failure,
            rationale="Duplicate",
            success=False,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_effect_supersedes_roundtrips_as_uuid(session: Session) -> None:
    game = _game("scenario")
    session.add(game)
    session.commit()
    old_id = uuid.uuid4()
    effect = Effect(
        game_id=game.id,
        description="Replacement",
        visibility="public",
        created_turn=1,
        supersedes=[old_id],
    )
    session.add(effect)
    session.commit()
    effect_id = effect.id
    session.expunge_all()

    loaded = session.get(Effect, effect_id)
    assert loaded is not None
    assert loaded.supersedes == [old_id]
    assert isinstance(loaded.supersedes[0], uuid.UUID)
