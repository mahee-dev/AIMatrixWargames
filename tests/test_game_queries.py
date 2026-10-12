"""I-064: neutral, shared game-state reads for the service and Actor context."""

from __future__ import annotations

import ast
import inspect

from sqlalchemy import Engine

from app import context, game_queries, game_service
from app.database import session_scope
from app.models import Argument, Con, Game
from tests.api_helpers import seed, start_to_cons


def test_i064_001_argument_queries_are_scoped_to_current_game_turn_actor_and_status(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    current = start_to_cons(db_engine, creation)
    other = seed(db_engine)
    start_to_cons(db_engine, other)

    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None
        by_status = game_queries.arguments_by_status(
            session, game, ("open_for_cons", "awaiting_reply")
        )
        assert [row.id for row in by_status] == [current.id]
        assert [row.id for row in game_queries.arguments_used(session, game, "arkania")] == [
            current.id
        ]
        assert game_queries.arguments_used(session, game, "belvar") == []


def test_i064_002_con_queries_and_eligibility_are_game_local(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)

    with session_scope(db_engine) as session:
        stored = session.get(Argument, argument.id)
        game = session.get(Game, creation.game_id)
        assert stored is not None and game is not None
        stored.challenger_actor_ids = ["belvar", "not-in-this-game"]
        session.add(stored)
        con = Con(argument_id=stored.id, actor_id="belvar", reason="A bounded objection")
        session.add(con)
        session.flush()

        assert game_queries.actor_ids(session, game.id) == {
            "arkania",
            "belvar",
            "cestia",
        }
        assert game_queries.eligible_con_actor_ids(session, game, stored) == {"belvar"}
        assert game_queries.cons_submitted(session, stored.id, "belvar") == [con]
        assert game_queries.cons_submitted(session, stored.id, "cestia") == []


def test_i064_003_context_imports_queries_not_mutation_service() -> None:
    tree = ast.parse(inspect.getsource(context))
    app_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app"
        for alias in node.names
    }
    assert "game_queries" in app_imports
    assert game_service.arguments.eligible_con_actor_ids is game_queries.eligible_con_actor_ids
