"""T-015/T-016 acceptance tests for the authoritative Argument → Cons lifecycle."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import select

from app import database, game_service, persistence
from app.database import session_scope
from app.models import (
    Argument,
    Con,
    ConParticipation,
    Game,
    GamePhase,
    GameStatus,
)
from tests.db_helpers import actor_row

ACTORS = ["arkania", "belvar", "cestia"]


def _seed_game(
    engine: Engine,
    *,
    phase: GamePhase = GamePhase.argument,
    active_actor_id: str | None = "arkania",
    turn: int = 1,
    primary_arguments_per_actor: int = 1,
    max_cons_per_actor: int = 3,
) -> uuid.UUID:
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="argument-cons",
                turn=turn,
                max_turns=6,
                phase=phase,
                status=GameStatus.running,
                turn_order=list(ACTORS),
                active_actor_id=active_actor_id,
                seed=42,
                primary_arguments_per_actor=primary_arguments_per_actor,
                max_cons_per_actor=max_cons_per_actor,
            ),
        )
        for actor_id in ACTORS:
            persistence.create(session, actor_row(game.id, actor_id))
        return game.id


def _make_current_argument(engine: Engine, game_id: uuid.UUID) -> uuid.UUID:
    return game_service.make_argument(
        engine,
        game_id,
        actor_id="arkania",
        action="Mobilise reserves",
        desired_effect="Reserves deploy to the frontier",
        pros=["Plans exist"],
    ).id


def _game(engine: Engine, game_id: uuid.UUID) -> tuple[GamePhase, str | None]:
    with session_scope(engine) as session:
        game = persistence.get(session, Game, game_id)
        assert game is not None
        return game.phase, game.active_actor_id


@dataclass(frozen=True)
class _ArgumentSnapshot:
    id: uuid.UUID
    actor_id: str
    turn: int
    budget_slot: int
    status: str


@dataclass(frozen=True)
class _ConSnapshot:
    id: uuid.UUID
    actor_id: str


def _arguments(engine: Engine, game_id: uuid.UUID) -> list[_ArgumentSnapshot]:
    with session_scope(engine) as session:
        rows = session.exec(select(Argument).where(Argument.game_id == game_id)).all()
        return [
            _ArgumentSnapshot(a.id, a.actor_id, a.turn, a.budget_slot, a.status)
            for a in rows
        ]


def _cons(engine: Engine, argument_id: uuid.UUID) -> list[_ConSnapshot]:
    with session_scope(engine) as session:
        rows = session.exec(select(Con).where(Con.argument_id == argument_id)).all()
        return [_ConSnapshot(c.id, c.actor_id) for c in rows]


def _participation(
    engine: Engine, argument_id: uuid.UUID, actor_id: str
) -> ConParticipation | None:
    with session_scope(engine) as session:
        return session.get(
            ConParticipation, {"argument_id": argument_id, "actor_id": actor_id}
        )


# T-015: TEST-ARG-001…008 and TEST-PHASE-004 -------------------------------------------


def test_arg_001_active_actor_can_submit(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)

    result = game_service.make_argument(
        db_engine,
        game_id,
        actor_id="arkania",
        action="Mobilise reserves",
        desired_effect="Reserves deploy",
        pros=["Plans exist", "Rail is available"],
    )

    assert result.actor_id == "arkania"
    assert result.turn == 1
    assert result.status == "open_for_cons"
    loaded = _arguments(db_engine, game_id)
    assert [(a.id, a.actor_id, a.turn, a.status) for a in loaded] == [
        (result.id, "arkania", 1, "open_for_cons")
    ]


def test_arg_002_wrong_actor_rejected_without_write(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)

    with pytest.raises(game_service.NotActiveActor):
        game_service.make_argument(
            db_engine, game_id, actor_id="belvar", action="Act",
            desired_effect="Effect", pros=["Pro"])

    assert _arguments(db_engine, game_id) == []
    assert _game(db_engine, game_id) == (GamePhase.argument, "arkania")


def test_argument_requires_an_authoritative_active_actor(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, active_actor_id=None)
    with pytest.raises(game_service.NotActiveActor):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Act",
            desired_effect="Effect", pros=["Pro"])
    assert _arguments(db_engine, game_id) == []


def test_arg_003_wrong_phase_rejected(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, phase=GamePhase.diplomacy, active_actor_id=None)

    with pytest.raises(game_service.InvalidPhase):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Act",
            desired_effect="Effect", pros=["Pro"])

    assert _arguments(db_engine, game_id) == []


@pytest.mark.parametrize("action", ["", "   ", None, 42])
def test_arg_004_empty_or_malformed_action_rejected(
    db_engine: Engine, action: object
) -> None:
    game_id = _seed_game(db_engine)
    with pytest.raises(game_service.InvalidArgument):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action=action,
            desired_effect="Effect", pros=["Pro"])
    assert _arguments(db_engine, game_id) == []


@pytest.mark.parametrize("desired_effect", ["", "   ", None, 42])
def test_arg_005_empty_or_malformed_desired_effect_rejected(
    db_engine: Engine, desired_effect: object
) -> None:
    game_id = _seed_game(db_engine)
    with pytest.raises(game_service.InvalidArgument):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Act",
            desired_effect=desired_effect, pros=["Pro"])
    assert _arguments(db_engine, game_id) == []


@pytest.mark.parametrize("pros", [[], None, "not-a-list"])
def test_arg_006_zero_or_malformed_pros_rejected(db_engine: Engine, pros: object) -> None:
    game_id = _seed_game(db_engine)
    with pytest.raises(game_service.InvalidArgument):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Act",
            desired_effect="Effect", pros=pros)


def test_arg_007_more_than_five_pros_rejected(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)
    with pytest.raises(game_service.InvalidArgument):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Act",
            desired_effect="Effect", pros=[f"Pro {n}" for n in range(6)])
    assert _arguments(db_engine, game_id) == []


@pytest.mark.parametrize("pros", [[""], ["  "], ["valid", 3]])
def test_argument_rejects_malformed_individual_pros(
    db_engine: Engine, pros: list[object]
) -> None:
    game_id = _seed_game(db_engine)
    with pytest.raises(game_service.InvalidArgument):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Act",
            desired_effect="Effect", pros=pros)


def test_arg_008_primary_argument_budget_one_is_per_game_turn_actor(
    db_engine: Engine,
) -> None:
    game_id = _seed_game(db_engine, primary_arguments_per_actor=1)
    with session_scope(db_engine) as session:
        persistence.create(session, Argument(
            game_id=game_id, turn=1, actor_id="arkania", budget_slot=1,
            action="Prior", desired_effect="Prior effect", pros=["Prior pro"],
            status="resolved"))

    with pytest.raises(game_service.ArgumentBudgetExhausted):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Second",
            desired_effect="Second effect", pros=["Pro"])

    assert len(_arguments(db_engine, game_id)) == 1
    assert _game(db_engine, game_id) == (GamePhase.argument, "arkania")


def test_argument_budget_greater_than_one_uses_next_persisted_slot(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, primary_arguments_per_actor=2)
    with session_scope(db_engine) as session:
        persistence.create(session, Argument(
            game_id=game_id, turn=1, actor_id="arkania", budget_slot=1,
            action="Prior", desired_effect="Prior effect", pros=["Pro"],
            status="resolved"))

    result = game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="Second",
        desired_effect="Second effect", pros=["Pro"])

    assert result.budget_slot == 2
    assert sorted(a.budget_slot for a in _arguments(db_engine, game_id)) == [1, 2]


def test_conflicting_unresolved_argument_is_rejected(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, primary_arguments_per_actor=2)
    with session_scope(db_engine) as session:
        persistence.create(session, Argument(
            game_id=game_id, turn=1, actor_id="arkania", budget_slot=1,
            action="Current", desired_effect="Effect", pros=["Pro"],
            status="open_for_cons"))

    with pytest.raises(game_service.ArgumentNotCurrent):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="Conflict",
            desired_effect="Effect", pros=["Pro"])
    assert len(_arguments(db_engine, game_id)) == 1


def test_phase_004_argument_submission_atomically_opens_cons(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)

    assert _game(db_engine, game_id) == (GamePhase.cons, "arkania")
    with session_scope(db_engine) as session:
        argument = persistence.get(session, Argument, argument_id)
        assert argument is not None and argument.status == "open_for_cons"
        assert session.exec(
            select(ConParticipation).where(ConParticipation.argument_id == argument_id)
        ).all() == []


def test_argument_insert_failure_rolls_back_phase_and_row(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _seed_game(db_engine)
    real_create = persistence.create

    def fail_after_flush(session, entity):  # noqa: ANN001
        created = real_create(session, entity)
        if isinstance(entity, Argument):
            raise RuntimeError("injected after Argument flush")
        return created

    monkeypatch.setattr(persistence, "create", fail_after_flush)
    with pytest.raises(RuntimeError):
        _make_current_argument(db_engine, game_id)

    assert _arguments(db_engine, game_id) == []
    assert _game(db_engine, game_id) == (GamePhase.argument, "arkania")


def test_argument_budget_and_actor_identity_are_isolated_across_games(
    db_engine: Engine,
) -> None:
    game_a = _seed_game(db_engine)
    game_b = _seed_game(db_engine)
    with session_scope(db_engine) as session:
        persistence.create(session, Argument(
            game_id=game_a, turn=1, actor_id="arkania", budget_slot=1,
            action="A prior", desired_effect="A effect", pros=["Pro"], status="resolved"))

    with pytest.raises(game_service.ArgumentBudgetExhausted):
        game_service.make_argument(
            db_engine, game_a, actor_id="arkania", action="A second",
            desired_effect="Effect", pros=["Pro"])
    result_b = game_service.make_argument(
        db_engine, game_b, actor_id="arkania", action="B first",
        desired_effect="Effect", pros=["Pro"])

    assert result_b.actor_id == "arkania"
    assert len(_arguments(db_engine, game_a)) == 1
    assert len(_arguments(db_engine, game_b)) == 1


def test_argument_phase_message_remains_legal(db_engine: Engine) -> None:
    """TRD phase contract: the active Actor may still send_message during Argument."""
    game_id = _seed_game(db_engine)
    result = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania", text="Final proposal",
        public=True, recipients=[])
    assert result.turn == 1
    assert _game(db_engine, game_id) == (GamePhase.argument, "arkania")


# T-016: TEST-CON-001…005 and TEST-PHASE-005 -------------------------------------------


def test_con_001_eligible_opponent_con_accepted(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)

    result = game_service.make_con(
        db_engine, game_id, argument_id=argument_id,
        actor_id="belvar", reason="Rail capacity is constrained")

    assert (result.argument_id, result.actor_id, result.reason) == (
        argument_id, "belvar", "Rail capacity is constrained")
    assert [(c.id, c.actor_id) for c in _cons(db_engine, argument_id)] == [
        (result.id, "belvar")
    ]
    assert _game(db_engine, game_id)[0] is GamePhase.cons


@pytest.mark.parametrize("reason", ["", "   ", None, 3])
def test_con_rejects_empty_or_malformed_reason(
    db_engine: Engine, reason: object
) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)
    with pytest.raises(game_service.InvalidCon):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="belvar", reason=reason)
    assert _cons(db_engine, argument_id) == []


def test_con_002_argument_owner_cannot_self_con_or_pass(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)

    with pytest.raises(game_service.SelfConForbidden):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="arkania", reason="Against myself")
    with pytest.raises(game_service.SelfConForbidden):
        game_service.pass_con(
            db_engine, game_id, argument_id=argument_id, actor_id="arkania")

    assert _cons(db_engine, argument_id) == []
    assert _participation(db_engine, argument_id, "arkania") is None
    assert _game(db_engine, game_id)[0] is GamePhase.cons


def test_con_unknown_actor_rejected_without_write(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)

    with pytest.raises(game_service.ActorNotInGame):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="outsider", reason="No")
    assert _cons(db_engine, argument_id) == []


def test_con_003_configured_canonical_limit_is_per_actor_argument(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, max_cons_per_actor=3)
    argument_id = _make_current_argument(db_engine, game_id)
    for number in range(3):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="belvar", reason=f"Reason {number}")

    with pytest.raises(game_service.ConParticipationComplete):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="belvar", reason="Fourth")

    # The limit is not global: another eligible Actor has an independent allowance.
    game_service.make_con(
        db_engine, game_id, argument_id=argument_id,
        actor_id="cestia", reason="Cestia's first")

    assert len(_cons(db_engine, argument_id)) == 4
    assert _participation(db_engine, argument_id, "belvar") is not None


@pytest.mark.parametrize("limit", [0, 1])
def test_con_limits_zero_and_one_follow_persisted_game_snapshot(
    db_engine: Engine, limit: int
) -> None:
    game_id = _seed_game(db_engine, max_cons_per_actor=limit)
    argument_id = _make_current_argument(db_engine, game_id)
    for number in range(limit):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="belvar", reason=f"Allowed {number}")
    expected_error = (
        game_service.ConLimitReached
        if limit == 0
        else game_service.ConParticipationComplete
    )
    with pytest.raises(expected_error):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="belvar", reason="Over limit")
    assert len(_cons(db_engine, argument_id)) == limit


def test_i030_reaching_con_limit_persists_completion(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, max_cons_per_actor=3)
    argument_id = _make_current_argument(db_engine, game_id)

    for number in range(2):
        game_service.make_con(
            db_engine,
            game_id,
            argument_id=argument_id,
            actor_id="belvar",
            reason=f"Reason {number}",
        )
    assert _participation(db_engine, argument_id, "belvar") is None

    game_service.make_con(
        db_engine,
        game_id,
        argument_id=argument_id,
        actor_id="belvar",
        reason="Final allowed reason",
    )

    assert len(_cons(db_engine, argument_id)) == 3
    assert _participation(db_engine, argument_id, "belvar") is not None
    assert _game(db_engine, game_id)[0] is GamePhase.cons
    with pytest.raises(game_service.ConParticipationComplete):
        game_service.pass_con(
            db_engine, game_id, argument_id=argument_id, actor_id="belvar"
        )


def test_i030_final_exhausted_actor_atomically_opens_adjudication(
    db_engine: Engine,
) -> None:
    game_id = _seed_game(db_engine, max_cons_per_actor=1)
    argument_id = _make_current_argument(db_engine, game_id)

    game_service.make_con(
        db_engine,
        game_id,
        argument_id=argument_id,
        actor_id="belvar",
        reason="Belvar final",
    )
    assert _game(db_engine, game_id)[0] is GamePhase.cons

    game_service.make_con(
        db_engine,
        game_id,
        argument_id=argument_id,
        actor_id="cestia",
        reason="Cestia final",
    )

    assert _game(db_engine, game_id) == (GamePhase.adjudication, "arkania")
    assert _participation(db_engine, argument_id, "belvar") is not None
    assert _participation(db_engine, argument_id, "cestia") is not None
    with session_scope(db_engine) as session:
        current = persistence.get(session, Argument, argument_id)
        assert current is not None and current.status == "adjudicating"


def test_i030_zero_limit_still_requires_explicit_pass(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, max_cons_per_actor=0)
    argument_id = _make_current_argument(db_engine, game_id)

    with pytest.raises(game_service.ConLimitReached):
        game_service.make_con(
            db_engine,
            game_id,
            argument_id=argument_id,
            actor_id="belvar",
            reason="Not allowed",
        )
    assert _participation(db_engine, argument_id, "belvar") is None

    game_service.pass_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar"
    )
    assert _participation(db_engine, argument_id, "belvar") is not None


def test_i030_final_con_completion_and_close_roll_back_together(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _seed_game(db_engine, max_cons_per_actor=1)
    argument_id = _make_current_argument(db_engine, game_id)
    game_service.make_con(
        db_engine,
        game_id,
        argument_id=argument_id,
        actor_id="belvar",
        reason="Belvar final",
    )
    real_close = game_service._close_cons_if_complete

    def fail_after_close(session, game, argument):  # noqa: ANN001
        real_close(session, game, argument)
        raise RuntimeError("injected after final-Con closure")

    monkeypatch.setattr(game_service, "_close_cons_if_complete", fail_after_close)
    with pytest.raises(RuntimeError):
        game_service.make_con(
            db_engine,
            game_id,
            argument_id=argument_id,
            actor_id="cestia",
            reason="Cestia final",
        )

    assert len(_cons(db_engine, argument_id)) == 1
    assert _participation(db_engine, argument_id, "cestia") is None
    assert _game(db_engine, game_id)[0] is GamePhase.cons
    with session_scope(db_engine) as session:
        argument = persistence.get(session, Argument, argument_id)
        assert argument is not None and argument.status == "open_for_cons"


def test_con_004_explicit_pass_persists_and_closes_actor_participation(
    db_engine: Engine,
) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)
    game_service.make_con(
        db_engine, game_id, argument_id=argument_id,
        actor_id="belvar", reason="First")

    result = game_service.pass_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar")

    assert result.passed is True
    assert _participation(db_engine, argument_id, "belvar") is not None
    with pytest.raises(game_service.ConParticipationComplete):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="belvar", reason="Too late")
    with pytest.raises(game_service.ConParticipationComplete):
        game_service.pass_con(
            db_engine, game_id, argument_id=argument_id, actor_id="belvar")
    assert len(_cons(db_engine, argument_id)) == 1


def test_con_pass_immediately_requires_no_con(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)

    game_service.pass_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar")

    assert _cons(db_engine, argument_id) == []
    assert _participation(db_engine, argument_id, "belvar") is not None


def test_con_completion_survives_fresh_engine_reload(
    db_engine: Engine, db_path: Path
) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)
    game_service.pass_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar")

    reloaded_engine = database.engine_for_path(db_path)
    try:
        # The reloaded completion is a pass: participation is closed and no Con was recorded,
        # and the fresh engine still refuses a second pass.
        assert _participation(reloaded_engine, argument_id, "belvar") is not None
        assert _cons(reloaded_engine, argument_id) == []
        with pytest.raises(game_service.ConParticipationComplete):
            game_service.pass_con(
                reloaded_engine, game_id, argument_id=argument_id, actor_id="belvar")
    finally:
        reloaded_engine.dispose()


def test_con_005_old_or_cross_game_argument_is_not_current(db_engine: Engine) -> None:
    game_a = _seed_game(db_engine)
    current_a = _make_current_argument(db_engine, game_a)
    game_b = _seed_game(db_engine)
    current_b = _make_current_argument(db_engine, game_b)

    with pytest.raises(game_service.ArgumentNotCurrent):
        game_service.make_con(
            db_engine, game_a, argument_id=current_b,
            actor_id="belvar", reason="Cross-game")
    with pytest.raises(game_service.ArgumentNotCurrent):
        game_service.pass_con(
            db_engine, game_a, argument_id=current_b, actor_id="belvar")

    assert _cons(db_engine, current_a) == []
    assert _cons(db_engine, current_b) == []


def test_stale_resolved_argument_rejected_when_another_is_current(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine, primary_arguments_per_actor=2)
    with session_scope(db_engine) as session:
        stale = persistence.create(session, Argument(
            game_id=game_id, turn=1, actor_id="arkania", budget_slot=1,
            action="Old", desired_effect="Old effect", pros=["Pro"], status="resolved"))
        stale_id = stale.id
    current_id = _make_current_argument(db_engine, game_id)

    with pytest.raises(game_service.ArgumentNotCurrent):
        game_service.make_con(
            db_engine, game_id, argument_id=stale_id,
            actor_id="belvar", reason="Stale")
    assert _cons(db_engine, stale_id) == []
    assert _cons(db_engine, current_id) == []


def test_phase_005_all_eligible_passes_atomically_open_adjudication(
    db_engine: Engine,
) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)

    first = game_service.pass_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar")
    assert first.cons_closed is False
    assert _game(db_engine, game_id)[0] is GamePhase.cons

    final = game_service.pass_con(
        db_engine, game_id, argument_id=argument_id, actor_id="cestia")
    assert final.cons_closed is True
    assert final.phase is GamePhase.adjudication
    assert _game(db_engine, game_id) == (GamePhase.adjudication, "arkania")
    with session_scope(db_engine) as session:
        current = persistence.get(session, Argument, argument_id)
        assert current is not None and current.status == "adjudicating"

    with pytest.raises(game_service.InvalidPhase):
        game_service.pass_con(
            db_engine, game_id, argument_id=argument_id, actor_id="cestia")


def test_cons_and_pass_outside_cons_phase_leave_state_unchanged(db_engine: Engine) -> None:
    game_id = _seed_game(db_engine)
    with session_scope(db_engine) as session:
        argument = persistence.create(session, Argument(
            game_id=game_id, turn=1, actor_id="arkania", budget_slot=1,
            action="Not open yet", desired_effect="Effect", pros=["Pro"],
            status="submitted"))
        argument_id = argument.id

    with pytest.raises(game_service.InvalidPhase):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id,
            actor_id="belvar", reason="Wrong phase")
    with pytest.raises(game_service.InvalidPhase):
        game_service.pass_con(
            db_engine, game_id, argument_id=argument_id, actor_id="belvar")
    assert _cons(db_engine, argument_id) == []
    assert _participation(db_engine, argument_id, "belvar") is None


def test_final_pass_failure_rolls_back_participation_and_phase(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    game_id = _seed_game(db_engine)
    argument_id = _make_current_argument(db_engine, game_id)
    game_service.pass_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar")
    real_close = game_service._close_cons_if_complete

    def fail_after_close(session, game, argument):  # noqa: ANN001
        real_close(session, game, argument)
        raise RuntimeError("injected after closure mutation")

    monkeypatch.setattr(game_service, "_close_cons_if_complete", fail_after_close)
    with pytest.raises(RuntimeError):
        game_service.pass_con(
            db_engine, game_id, argument_id=argument_id, actor_id="cestia")

    assert _participation(db_engine, argument_id, "cestia") is None
    assert _game(db_engine, game_id)[0] is GamePhase.cons
    with session_scope(db_engine) as session:
        argument = persistence.get(session, Argument, argument_id)
        assert argument is not None and argument.status == "open_for_cons"


def test_con_counts_and_participation_are_isolated_across_games_with_same_actor_ids(
    db_engine: Engine,
) -> None:
    game_a = _seed_game(db_engine, max_cons_per_actor=1)
    argument_a = _make_current_argument(db_engine, game_a)
    game_b = _seed_game(db_engine, max_cons_per_actor=1)
    argument_b = _make_current_argument(db_engine, game_b)

    game_service.make_con(
        db_engine, game_a, argument_id=argument_a, actor_id="belvar", reason="A con")
    game_service.make_con(
        db_engine, game_b, argument_id=argument_b, actor_id="belvar", reason="B con")

    assert len(_cons(db_engine, argument_a)) == 1
    assert len(_cons(db_engine, argument_b)) == 1
    assert _participation(db_engine, argument_a, "belvar") is not None
    assert _participation(db_engine, argument_b, "belvar") is not None
