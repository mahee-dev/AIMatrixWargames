"""Game start, turn start and the diplomacy phase (T-013 + T-014, WBS 2.4.1–2.4.2).

These exercise the one authoritative state-machine service ``app.game_service``:

- **T-013** — ``LOBBY → DIPLOMACY`` game start (state-machine §14/§15): status/phase/turn
  bookkeeping, illegal-start rejection, and Effect **expiry** at the turn-start boundary
  (§15.5, D-010) which deactivates but never deletes (INV-EFFECT-004).
- **T-014** — the diplomacy phase (state-machine §16, api.md §7/§12): public/private Message
  creation proven through the T-012 visibility service (no second visibility path here),
  per-Actor readiness, all-ready closure to ARGUMENT with the first turn-order Actor, and the
  mandatory rule that a new private Message to an already-ready recipient clears that
  recipient's readiness *before* closure is evaluated (§16, TEST-PHASE-003).

Security-style assertions state both what IS and what IS NOT true (readiness present/absent,
Message visible/hidden). Each test runs on the isolated ``db_engine`` fixture (order-
independent, key-free).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine

from app import game_service, persistence, visibility
from app.database import session_scope
from app.models import (
    Actor,
    Effect,
    Game,
    GamePhase,
    GameStatus,
    Seat,
)
from tests.api_helpers import seed, start_to_diplomacy

TURN_ORDER = ["arkania", "belvar", "cestia"]


def _make_actor(game_id: uuid.UUID, actor_id: str) -> Actor:
    return Actor(
        game_id=game_id,
        id=actor_id,
        name=actor_id.title(),
        public_description=f"{actor_id} public",
        private_brief=f"{actor_id} brief",
        objectives=[f"{actor_id} objective"],
        private_information=[f"{actor_id} secret"],
    )


def _seed_lobby_game(
    engine: Engine, *, turn_order: list[str] | None = None, with_seats: bool = True
) -> uuid.UUID:
    """Create a LOBBY game with three Actors (and, by default, their Seats)."""
    order = turn_order if turn_order is not None else list(TURN_ORDER)
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="scn",
                turn=0,
                max_turns=6,
                phase=GamePhase.diplomacy,
                status=GameStatus.lobby,
                turn_order=order,
                active_actor_id=None,
                seed=4242,
                diplomacy_ready_actor_ids=[],
            ),
        )
        for actor_id in TURN_ORDER:
            persistence.create(session, _make_actor(game.id, actor_id))
            if with_seats:
                persistence.create(
                    session, Seat(game_id=game.id, actor_id=actor_id, token_hash=f"h-{actor_id}")
                )
        return game.id


@dataclass(frozen=True)
class _GameSnapshot:
    turn: int
    phase: GamePhase
    status: GameStatus
    active_actor_id: str | None
    diplomacy_ready_actor_ids: list[str]


def _reload(engine: Engine, game_id: uuid.UUID) -> _GameSnapshot:
    """Read the game's authoritative fields in a *fresh* session (proves persistence).

    Snapshotted inside the session so the returned value is safe to read after the transaction
    closes (attributes are expired on commit otherwise).
    """
    with session_scope(engine) as session:
        game = persistence.get(session, Game, game_id)
        return _GameSnapshot(
            turn=game.turn,
            phase=game.phase,
            status=game.status,
            active_actor_id=game.active_actor_id,
            diplomacy_ready_actor_ids=list(game.diplomacy_ready_actor_ids),
        )


# =======================================================================================
# T-013 — Game start & turn start
# =======================================================================================


def test_phase_001_game_starts_in_diplomacy(db_engine: Engine) -> None:
    """TEST-PHASE-001: after admin start, status=running, phase=diplomacy, turn=1."""
    game_id = _seed_lobby_game(db_engine)

    game_service.start_game(db_engine, game_id)

    game = _reload(db_engine, game_id)  # fresh session — proves persisted, not in-memory
    assert game.status is GameStatus.running
    assert game.phase is GamePhase.diplomacy
    assert game.turn == 1
    assert game.active_actor_id is None
    assert game.diplomacy_ready_actor_ids == []


def test_illegal_repeat_start_is_rejected_and_leaves_state_unchanged(db_engine: Engine) -> None:
    """A second start of a running game fails; the running state is not disturbed."""
    game_id = _seed_lobby_game(db_engine)
    game_service.start_game(db_engine, game_id)

    with pytest.raises(game_service.IllegalStart):
        game_service.start_game(db_engine, game_id)

    game = _reload(db_engine, game_id)
    assert game.status is GameStatus.running
    assert game.turn == 1


def test_start_from_non_lobby_status_fails(db_engine: Engine) -> None:
    """Starting a game whose status is not lobby is refused."""
    game_id = _seed_lobby_game(db_engine)
    with session_scope(db_engine) as session:
        persistence.update(session, persistence.get(session, Game, game_id),
                           status=GameStatus.complete)

    with pytest.raises(game_service.IllegalStart):
        game_service.start_game(db_engine, game_id)


def test_start_requires_configured_seats(db_engine: Engine) -> None:
    """§14 guard 'Seats configured': a game with no Seats cannot start; stays in lobby."""
    game_id = _seed_lobby_game(db_engine, with_seats=False)

    with pytest.raises(game_service.IllegalStart):
        game_service.start_game(db_engine, game_id)

    assert _reload(db_engine, game_id).status is GameStatus.lobby


def test_start_requires_valid_turn_order(db_engine: Engine) -> None:
    """A turn_order naming an Actor that does not exist is an invalid start."""
    game_id = _seed_lobby_game(db_engine, turn_order=["arkania", "belvar", "zulu"])

    with pytest.raises(game_service.IllegalStart):
        game_service.start_game(db_engine, game_id)

    assert _reload(db_engine, game_id).status is GameStatus.lobby


def test_start_of_absent_game_fails_closed(db_engine: Engine) -> None:
    """Starting a nonexistent game raises GameNotFound rather than committing blind."""
    with pytest.raises(game_service.GameNotFound):
        game_service.start_game(db_engine, uuid.uuid4())


def test_effect_expiry_evaluated_at_turn_start_boundary(db_engine: Engine) -> None:
    """§15.5 / D-010: at turn-1 start, expires_turn<=1 deactivates; others untouched; retained.

    Boundary made explicit with starting Effects (created_turn=0):
      * expires_turn=1  -> inactive at the start of turn 1;
      * expires_turn=2  -> still active during turn 1;
      * expires_turn=None -> never expires;
      * an already-inactive Effect is unrelated and must not be re-touched or deleted.
    """
    game_id = _seed_lobby_game(db_engine)
    with session_scope(db_engine) as session:
        expiring = persistence.create(session, Effect(
            game_id=game_id, description="expires at turn 1", visibility="public",
            created_turn=0, expires_turn=1, active=True))
        surviving = persistence.create(session, Effect(
            game_id=game_id, description="expires at turn 2", visibility="public",
            created_turn=0, expires_turn=2, active=True))
        permanent = persistence.create(session, Effect(
            game_id=game_id, description="no expiry", visibility="public",
            created_turn=0, expires_turn=None, active=True))
        already_off = persistence.create(session, Effect(
            game_id=game_id, description="already inactive", visibility="public",
            created_turn=0, expires_turn=5, active=False))
        ids = (expiring.id, surviving.id, permanent.id, already_off.id)

    game_service.start_game(db_engine, game_id)

    with session_scope(db_engine) as session:  # fresh read — survives reload
        expiring_r, surviving_r, permanent_r, already_off_r = (
            persistence.get(session, Effect, i) for i in ids
        )
        # The row is retained (never deleted), only deactivated.
        assert expiring_r is not None
        assert expiring_r.active is False
        # Not at the boundary yet / never expires: still active.
        assert surviving_r.active is True
        assert permanent_r.active is True
        # Unrelated pre-inactive Effect is untouched (still inactive, still present).
        assert already_off_r.active is False


# =======================================================================================
# T-014 — Diplomacy phase: Messages
# =======================================================================================


def _running_game(db_engine: Engine) -> uuid.UUID:
    game_id = _seed_lobby_game(db_engine)
    game_service.start_game(db_engine, game_id)
    return game_id


def test_public_message_is_created_with_server_turn_and_seen_by_all(db_engine: Engine) -> None:
    """A public Message gets the current server turn and is visible to every Actor (T-012)."""
    game_id = _running_game(db_engine)

    result = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania",
        text="regional stability", public=True, recipients=[])

    assert result.turn == 1
    assert result.public is True
    with session_scope(db_engine) as session:
        for actor_id in TURN_ORDER:
            visible = visibility.visible_messages(session, game_id, actor_id)
            assert [m.text for m in visible] == ["regional stability"]


def test_private_message_visible_only_to_sender_and_recipient(db_engine: Engine) -> None:
    """Private A->B is visible to A and B and hidden from C — proven via the T-012 service."""
    game_id = _running_game(db_engine)

    game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania",
        text="SECRET-DIPLOMACY-A-TO-B", public=False, recipients=["belvar"])

    with session_scope(db_engine) as session:
        for viewer in ("arkania", "belvar"):
            texts = [m.text for m in visibility.visible_messages(session, game_id, viewer)]
            assert "SECRET-DIPLOMACY-A-TO-B" in texts
        cestia_texts = [
            m.text for m in visibility.visible_messages(session, game_id, "cestia")
        ]
        assert "SECRET-DIPLOMACY-A-TO-B" not in cestia_texts


def test_public_message_with_recipients_is_rejected(db_engine: Engine) -> None:
    """INV-MSG-002 / api §7.2: a public Message must carry an empty recipient list."""
    game_id = _running_game(db_engine)
    with pytest.raises(game_service.InvalidMessage):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania",
            text="hi", public=True, recipients=["belvar"])


def test_private_message_without_recipients_is_rejected(db_engine: Engine) -> None:
    """api §7.2: a private Message must contain at least one recipient."""
    game_id = _running_game(db_engine)
    with pytest.raises(game_service.InvalidRecipient):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania",
            text="hi", public=False, recipients=[])


def test_empty_message_text_is_rejected(db_engine: Engine) -> None:
    game_id = _running_game(db_engine)
    with pytest.raises(game_service.InvalidMessage):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania",
            text="   ", public=True, recipients=[])


def test_message_from_unknown_sender_is_rejected(db_engine: Engine) -> None:
    game_id = _running_game(db_engine)
    with pytest.raises(game_service.ActorNotInGame):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="outsider",
            text="hi", public=True, recipients=[])


def test_message_to_recipient_not_in_game_is_rejected(db_engine: Engine) -> None:
    """INV-MSG-004: every recipient must be an Actor in the same game."""
    game_id = _running_game(db_engine)
    with pytest.raises(game_service.InvalidRecipient):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania",
            text="hi", public=False, recipients=["zulu"])


def test_message_in_lobby_is_rejected(db_engine: Engine) -> None:
    """LOBBY cannot accept diplomacy mutations (status not running)."""
    game_id = _seed_lobby_game(db_engine)  # not started
    with pytest.raises(game_service.InvalidPhase):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania",
            text="hi", public=True, recipients=[])


# =======================================================================================
# T-014 — Diplomacy phase: readiness and closure
# =======================================================================================


def test_readiness_persists_and_partial_readiness_keeps_diplomacy_open(db_engine: Engine) -> None:
    """A ready is authoritative persisted state; two of three ready leaves diplomacy open."""
    game_id = _running_game(db_engine)

    game_service.ready(db_engine, game_id, "arkania")
    game_service.ready(db_engine, game_id, "belvar")

    game = _reload(db_engine, game_id)
    assert set(game.diplomacy_ready_actor_ids) == {"arkania", "belvar"}
    assert game.phase is GamePhase.diplomacy
    assert game.active_actor_id is None


def test_phase_002_all_ready_closes_diplomacy_to_argument(db_engine: Engine) -> None:
    """TEST-PHASE-002: when all Actors ready, phase→argument, active→first turn-order Actor."""
    game_id = _running_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    game_service.ready(db_engine, game_id, "belvar")

    still_open = _reload(db_engine, game_id)
    assert still_open.phase is GamePhase.diplomacy  # not yet — cestia outstanding

    result = game_service.ready(db_engine, game_id, "cestia")
    assert result.diplomacy_closed is True

    game = _reload(db_engine, game_id)
    assert game.phase is GamePhase.argument
    assert game.active_actor_id == "arkania"  # first in turn_order
    assert game.turn == 1  # turn preserved
    assert game.status is GameStatus.running


def test_phase_003_private_message_clears_recipient_readiness(db_engine: Engine) -> None:
    """TEST-PHASE-003: private Message to a ready recipient clears it; phase stays diplomacy."""
    game_id = _running_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    game_service.ready(db_engine, game_id, "belvar")

    game_service.send_message(
        db_engine, game_id, sender_actor_id="cestia",
        text="reopening talks with belvar", public=False, recipients=["belvar"])

    game = _reload(db_engine, game_id)
    assert "belvar" not in game.diplomacy_ready_actor_ids  # cleared
    assert "arkania" in game.diplomacy_ready_actor_ids  # untouched
    assert game.phase is GamePhase.diplomacy  # still open


def test_closure_uses_current_readiness_not_stale_set(db_engine: Engine) -> None:
    """The race at the boundary: a message clears A after A readied; closure must not use it.

    A ready, B ready, then C privately messages A (clearing A), then C readies. The ready set
    is now {B, C} — diplomacy must stay open. Only re-readying A closes it.
    """
    game_id = _running_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    game_service.ready(db_engine, game_id, "belvar")
    game_service.send_message(
        db_engine, game_id, sender_actor_id="cestia",
        text="a word with arkania", public=False, recipients=["arkania"])
    game_service.ready(db_engine, game_id, "cestia")

    mid = _reload(db_engine, game_id)
    assert set(mid.diplomacy_ready_actor_ids) == {"belvar", "cestia"}
    assert mid.phase is GamePhase.diplomacy  # A was cleared — not all ready

    game_service.ready(db_engine, game_id, "arkania")
    assert _reload(db_engine, game_id).phase is GamePhase.argument


def test_private_message_to_multiple_recipients_clears_each_ready_one(db_engine: Engine) -> None:
    """Every ready recipient of a private Message is cleared, not just the first."""
    game_id = _running_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    game_service.ready(db_engine, game_id, "belvar")

    game_service.send_message(
        db_engine, game_id, sender_actor_id="cestia",
        text="joint proposal", public=False, recipients=["arkania", "belvar"])

    game = _reload(db_engine, game_id)
    assert game.diplomacy_ready_actor_ids == []
    assert game.phase is GamePhase.diplomacy


def test_public_message_does_not_clear_readiness(db_engine: Engine) -> None:
    """Only a *private* Message to a ready recipient resets readiness (§16, WBS T-014)."""
    game_id = _running_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    game_service.ready(db_engine, game_id, "belvar")

    game_service.send_message(
        db_engine, game_id, sender_actor_id="cestia",
        text="public statement", public=True, recipients=[])

    game = _reload(db_engine, game_id)
    assert set(game.diplomacy_ready_actor_ids) == {"arkania", "belvar"}

    # And the phase still closes normally once the last Actor readies.
    game_service.ready(db_engine, game_id, "cestia")
    assert _reload(db_engine, game_id).phase is GamePhase.argument


def test_duplicate_ready_is_rejected(db_engine: Engine) -> None:
    """Readiness is not idempotent at the service layer (idempotency is T-023)."""
    game_id = _running_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    with pytest.raises(game_service.AlreadyReady):
        game_service.ready(db_engine, game_id, "arkania")


def test_ready_by_unknown_actor_is_rejected(db_engine: Engine) -> None:
    game_id = _running_game(db_engine)
    with pytest.raises(game_service.ActorNotInGame):
        game_service.ready(db_engine, game_id, "outsider")


def test_ready_in_lobby_is_rejected(db_engine: Engine) -> None:
    game_id = _seed_lobby_game(db_engine)  # not started
    with pytest.raises(game_service.InvalidPhase):
        game_service.ready(db_engine, game_id, "arkania")


def test_after_diplomacy_closes_ready_is_rejected_but_argument_message_is_allowed(
    db_engine: Engine,
) -> None:
    """TRD phase contract: ready ends with diplomacy; messaging remains legal in Argument."""
    game_id = _running_game(db_engine)
    for actor_id in TURN_ORDER:
        game_service.ready(db_engine, game_id, actor_id)
    assert _reload(db_engine, game_id).phase is GamePhase.argument

    with pytest.raises(game_service.InvalidPhase):
        game_service.ready(db_engine, game_id, "arkania")
    message = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania",
        text="argument-phase statement", public=True, recipients=[])
    assert message.turn == 1
    assert _reload(db_engine, game_id).phase is GamePhase.argument


# --- service text limits ------------------------------------------------------------------


def test_i012_service_text_and_umpire_limits(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    with pytest.raises(game_service.InvalidMessage):
        game_service.send_message(
            db_engine,
            creation.game_id,
            sender_actor_id="arkania",
            text="x" * (game_service.MAX_TEXT_CHARS + 1),
            public=True,
            recipients=[],
        )
