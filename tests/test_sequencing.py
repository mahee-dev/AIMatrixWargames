"""T-021 acceptance tests: Actor advancement, turn sequencing and game completion.

Covers TEST-PHASE-006…008 and the batch's boundary rules (state-machine §20/§21/§22): the
one authoritative advancement path advances by ``turn_order`` position, completes the turn
after the last Actor (Consequences disabled — §21 Option A), reuses the T-013 turn-start
helper for the next turn, completes the game at ``turn == max_turns`` (never overshoots), and
survives a process restart. Everything runs offline through the FakeUmpire (testing.md §3).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine

from app import database, game_service, persistence
from app.database import session_scope
from app.models import AdjudicationMode, Effect, Game, GamePhase, GameStatus
from app.umpire import AdjudicationProposal
from agents.umpire import FakeUmpire
from tests.db_helpers import actor_row

ACTORS = ["arkania", "belvar", "cestia"]
SEED = 424242
_OK = AdjudicationProposal(
    mode=AdjudicationMode.automatic_success, rationale="clear", success_effects=["It works."]
)


def _running_argument_game(
    engine: Engine, *, turn: int = 1, max_turns: int = 6, actors: list[str] = ACTORS
) -> uuid.UUID:
    """A running game in the ARGUMENT phase, active on the first Actor of ``turn_order``."""
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="sequencing",
                turn=turn,
                max_turns=max_turns,
                phase=GamePhase.argument,
                status=GameStatus.running,
                turn_order=list(actors),
                active_actor_id=actors[0],
                seed=SEED,
                primary_arguments_per_actor=1,
                max_cons_per_actor=3,
            ),
        )
        for actor_id in actors:
            persistence.create(session, actor_row(game.id, actor_id))
        return game.id


def _game(engine: Engine, game_id: uuid.UUID) -> Game:
    with session_scope(engine) as session:
        game = persistence.get(session, Game, game_id)
        assert game is not None
        session.expunge(game)
        return game


def _resolve_active(engine: Engine, game_id: uuid.UUID) -> None:
    """Run the real argument→cons→adjudication flow for the current active Actor."""
    game = _game(engine, game_id)
    owner = game.active_actor_id
    assert owner is not None
    argument_id = game_service.make_argument(
        engine,
        game_id,
        actor_id=owner,
        action="Act",
        desired_effect="Effect",
        pros=["Because"],
    ).id
    for opponent in [a for a in game.turn_order if a != owner]:
        game_service.pass_con(engine, game_id, argument_id=argument_id, actor_id=opponent)
    assert _game(engine, game_id).phase is GamePhase.adjudication
    game_service.resolve_adjudication(engine, game_id, FakeUmpire(_OK))


# --- TEST-PHASE-006 — Adjudication advances Actor ---------------------------------------


def test_phase_006_adjudication_advances_actor(db_engine: Engine) -> None:
    """After Actor A resolution, the next Actor becomes active and phase → argument."""
    game_id = _running_argument_game(db_engine)
    _resolve_active(db_engine, game_id)  # arkania

    result = game_service.advance_after_resolution(db_engine, game_id)

    assert result.active_actor_id == "belvar"
    assert result.phase is GamePhase.argument
    assert result.turn == 1
    assert result.turn_completed is False
    assert result.game_completed is False
    game = _game(db_engine, game_id)
    assert game.active_actor_id == "belvar"
    assert game.phase is GamePhase.argument
    assert game.turn == 1


def test_advancement_walks_the_whole_turn_order(db_engine: Engine) -> None:
    """A → B → C, one Argument opportunity per Actor, turn unchanged until the last."""
    game_id = _running_argument_game(db_engine)
    _resolve_active(db_engine, game_id)  # arkania
    assert game_service.advance_after_resolution(db_engine, game_id).active_actor_id == "belvar"
    _resolve_active(db_engine, game_id)  # belvar
    assert game_service.advance_after_resolution(db_engine, game_id).active_actor_id == "cestia"


# --- TEST-PHASE-007 — Final Actor advances turn ----------------------------------------


def test_phase_007_final_actor_advances_turn(db_engine: Engine) -> None:
    """After the final Actor resolves on a non-final turn: turn += 1, phase → diplomacy."""
    game_id = _running_argument_game(db_engine, turn=1, max_turns=6)
    for _ in ACTORS:  # resolve A, B, C, advancing after each
        _resolve_active(db_engine, game_id)
        result = game_service.advance_after_resolution(db_engine, game_id)

    assert result.turn == 2
    assert result.phase is GamePhase.diplomacy
    assert result.active_actor_id is None
    assert result.turn_completed is True
    assert result.game_completed is False
    game = _game(db_engine, game_id)
    assert game.turn == 2
    assert game.phase is GamePhase.diplomacy
    assert game.active_actor_id is None
    assert game.diplomacy_ready_actor_ids == []  # readiness reset at turn start (§15)
    assert game.status is GameStatus.running


def test_turn_start_expiry_runs_on_advancement(db_engine: Engine) -> None:
    """The next turn evaluates Effect expiry at the T-013 boundary (D-010), not a new one."""
    game_id = _running_argument_game(db_engine, turn=1, max_turns=6)
    with session_scope(db_engine) as session:
        persistence.create(
            session,
            Effect(
                game_id=game_id,
                description="expires at turn 2",
                visibility="public",
                visible_to=[],
                created_turn=1,
                expires_turn=2,
                active=True,
            ),
        )
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        game_service.advance_after_resolution(db_engine, game_id)
    with session_scope(db_engine) as session:
        effect = session.exec(
            Effect.__table__.select().where(Effect.description == "expires at turn 2")
        ).first()
    assert effect.active is False  # deactivated at the start of turn 2


# --- TEST-PHASE-008 — Final turn completes game ----------------------------------------


def test_phase_008_final_turn_completes_game(db_engine: Engine) -> None:
    """After the last Actor of turn == max_turns resolves, the game completes."""
    game_id = _running_argument_game(db_engine, turn=6, max_turns=6)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        result = game_service.advance_after_resolution(db_engine, game_id)

    assert result.status is GameStatus.complete
    assert result.phase is GamePhase.complete
    assert result.active_actor_id is None
    assert result.turn == 6  # never overshoots max_turns
    assert result.turn_completed is True
    assert result.game_completed is True


# --- Off-by-one boundary ---------------------------------------------------------------


def test_penultimate_turn_last_actor_starts_new_turn_not_completion(db_engine: Engine) -> None:
    """turn = max_turns - 1, last Actor resolved → a new turn begins, game not complete."""
    game_id = _running_argument_game(db_engine, turn=5, max_turns=6)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        result = game_service.advance_after_resolution(db_engine, game_id)
    assert result.game_completed is False
    assert result.turn == 6
    assert result.phase is GamePhase.diplomacy
    assert _game(db_engine, game_id).status is GameStatus.running


def test_final_turn_last_actor_completes_game(db_engine: Engine) -> None:
    """turn = max_turns, last Actor resolved → the game completes exactly once."""
    game_id = _running_argument_game(db_engine, turn=6, max_turns=6)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        result = game_service.advance_after_resolution(db_engine, game_id)
    assert result.game_completed is True
    assert _game(db_engine, game_id).turn == 6


# --- Restart safety --------------------------------------------------------------------


def test_completed_terminal_state_survives_restart(db_engine: Engine, db_path) -> None:
    """A completed game reloads from a fresh Engine with consistent terminal state."""
    game_id = _running_argument_game(db_engine, turn=6, max_turns=6)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        game_service.advance_after_resolution(db_engine, game_id)
    db_engine.dispose()

    reopened = database.engine_for_path(db_path)
    try:
        game = _game(reopened, game_id)
        assert game.status is GameStatus.complete
        assert game.phase is GamePhase.complete
        assert game.active_actor_id is None
        assert game.turn == 6
    finally:
        reopened.dispose()


# --- Terminal-state mutation protection ------------------------------------------------


def test_player_mutations_refused_on_completed_game(db_engine: Engine) -> None:
    """Once complete, player mutations fail; read-only retrieval is unaffected (§22)."""
    game_id = _running_argument_game(db_engine, turn=6, max_turns=6)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        game_service.advance_after_resolution(db_engine, game_id)

    with pytest.raises(game_service.InvalidPhase):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="a", desired_effect="d", pros=["p"]
        )
    with pytest.raises(game_service.InvalidPhase):
        game_service.ready(db_engine, game_id, "arkania")
    with pytest.raises(game_service.InvalidPhase):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania", text="hi", public=True
        )


# --- Invalid advancement ---------------------------------------------------------------


def test_advance_rejected_when_not_in_adjudication(db_engine: Engine) -> None:
    """Advancement requires the post-resolution ADJUDICATION phase (§20)."""
    game_id = _running_argument_game(db_engine)  # still in argument phase
    with pytest.raises(game_service.InvalidPhase):
        game_service.advance_after_resolution(db_engine, game_id)


def test_advance_rejected_when_current_argument_unresolved(db_engine: Engine) -> None:
    """In adjudication but not yet resolved (no record) → refuse, state unchanged."""
    game_id = _running_argument_game(db_engine)
    game = _game(db_engine, game_id)
    argument_id = game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="a", desired_effect="d", pros=["p"]
    ).id
    for opponent in [a for a in game.turn_order if a != "arkania"]:
        game_service.pass_con(db_engine, game_id, argument_id=argument_id, actor_id=opponent)
    # phase is now adjudication but resolve_adjudication has NOT run
    with pytest.raises(game_service.ArgumentNotCurrent):
        game_service.advance_after_resolution(db_engine, game_id)
    assert _game(db_engine, game_id).phase is GamePhase.adjudication  # unchanged


def test_advance_rejected_on_absent_game(db_engine: Engine) -> None:
    with pytest.raises(game_service.GameNotFound):
        game_service.advance_after_resolution(db_engine, uuid.uuid4())


def test_advance_rejected_on_completed_game(db_engine: Engine) -> None:
    """A second advancement of a completed game is refused (INV-GAME-005)."""
    game_id = _running_argument_game(db_engine, turn=6, max_turns=6)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        game_service.advance_after_resolution(db_engine, game_id)
    with pytest.raises(game_service.InvalidPhase):
        game_service.advance_after_resolution(db_engine, game_id)
