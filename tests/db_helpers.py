"""Row builders and counters for service-layer tests that set exact database states."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlmodel import func, select

from app.database import session_scope
from app.models import Actor


def actor_row(game_id: uuid.UUID, actor_id: str = "arkania") -> Actor:
    """An Actor row with placeholder public and private text."""
    return Actor(
        game_id=game_id,
        id=actor_id,
        name=actor_id.title(),
        public_description="public",
        private_brief="private",
    )


def count_rows(engine: Engine, model, **where) -> int:  # noqa: ANN001
    """How many ``model`` rows match every ``column=value`` in ``where``."""
    with session_scope(engine) as session:
        stmt = select(func.count()).select_from(model)
        for column, value in where.items():
            stmt = stmt.where(getattr(model, column) == value)
        return session.exec(stmt).one()
