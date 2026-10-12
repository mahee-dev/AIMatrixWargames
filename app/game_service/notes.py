"""Private Notes: an Actor's own record, idempotent like every other player operation."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlmodel import Session

from app import game_queries, persistence
from app.game_errors import ActorNotInGame, InvalidNote
from app.game_results import NoteResult
from app.models import Game, Note
from app.game_service._common import MAX_TEXT_CHARS, _OP_SAVE_NOTE, _run_idempotent


# --- T-033: private Notes ---------------------------------------------------------------


def save_note(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    actor_id: str,
    text: str,
    idempotency_key: str | None = None,
) -> NoteResult:
    """Create one private, game-/Actor-scoped Note (api.md §11, INV-NOTE-001/002).

    The ``actor_id`` is the authoritative identity supplied at the service boundary (from the
    Seat token — T-028); it is validated to belong to the game. ``text`` must be non-empty. No
    phase guard applies — Notes are private scratch space, legal in any phase. Reads are the
    visibility service's concern (:func:`app.visibility.visible_notes`); nothing here is ever
    surfaced to another Actor. Creation is idempotent through the reserved ``save_note`` family
    (T-023), so a retried save with the same key + text replays the original Note rather than
    duplicating it. No :class:`~app.models.GameEvent` is emitted — a Note is private and must
    never reach the (visibility-filtered but shared) event stream.
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        if actor_id not in game_queries.actor_ids(session, game.id):
            raise ActorNotInGame(
                f"actor {actor_id!r} does not belong to game {game.id!r}"
            )
        if not isinstance(text, str) or not text.strip():
            raise InvalidNote("note text must be a non-empty string")
        if len(text) > MAX_TEXT_CHARS:
            raise InvalidNote(f"note text exceeds {MAX_TEXT_CHARS} characters")

        note = persistence.create(
            session, Note(game_id=game.id, actor_id=actor_id, text=text)
        )
        captured.update(
            id=note.id,
            actor_id=note.actor_id,
            text=note.text,
            created_at=note.created_at,
        )

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_SAVE_NOTE,
        actor_id=actor_id,
        key=idempotency_key,
        payload={"text": text},
        result_type=NoteResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return NoteResult(**captured)
