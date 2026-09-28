"""T-033 (service layer): `game_service.save_note` (api.md §11, INV-NOTE-001/002).

Notes are private, game-scoped and Actor-scoped. Creation is idempotent through the reserved
``save_note`` operation family (T-023). Reads go through the visibility service.

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from app import game_service, visibility
from app.database import session_scope
from tests.api_helpers import seed


def _notes_of(engine: Engine, game_id, actor_id) -> list[str]:
    with session_scope(engine) as session:
        return [n.text for n in visibility.visible_notes(session, game_id, actor_id)]


def test_save_note_creates_actor_scoped_note(db_engine: Engine) -> None:
    creation = seed(db_engine)
    result = game_service.save_note(
        db_engine, creation.game_id, actor_id="arkania", text="Cestia may stay neutral."
    )

    assert result.actor_id == "arkania"
    assert result.text == "Cestia may stay neutral."
    # INV-NOTE-001/002: visible to its owner, invisible to others.
    assert _notes_of(db_engine, creation.game_id, "arkania") == ["Cestia may stay neutral."]
    assert _notes_of(db_engine, creation.game_id, "belvar") == []


def test_save_note_empty_text_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    with pytest.raises(game_service.InvalidNote):
        game_service.save_note(db_engine, creation.game_id, actor_id="arkania", text="  ")


def test_save_note_unknown_actor_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    with pytest.raises(game_service.ActorNotInGame):
        game_service.save_note(db_engine, creation.game_id, actor_id="nobody", text="x")


def test_save_note_idempotent_replay_and_conflict(db_engine: Engine) -> None:
    creation = seed(db_engine)
    first = game_service.save_note(
        db_engine, creation.game_id, actor_id="arkania", text="once", idempotency_key="N1"
    )
    replay = game_service.save_note(
        db_engine, creation.game_id, actor_id="arkania", text="once", idempotency_key="N1"
    )
    assert first.id == replay.id
    assert first.created_at == replay.created_at  # the original result is returned verbatim
    assert _notes_of(db_engine, creation.game_id, "arkania") == ["once"]  # created once

    with pytest.raises(game_service.IdempotencyConflict):
        game_service.save_note(
            db_engine, creation.game_id, actor_id="arkania", text="different", idempotency_key="N1"
        )
