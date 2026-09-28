"""T-065 acceptance for the shipped Orison example scenario."""

from pathlib import Path

from sqlalchemy import Engine

from app import scenarios
from app.database import session_scope
from app.models import Actor, Effect, Game, GameStatus, Resource, Seat
from app.persistence import list_by_game


SCENARIO_DIR = Path(__file__).parents[1] / "scenarios" / "orison-crisis"


def test_t_065_orison_package_is_a_runnable_polished_example() -> None:
    loaded = scenarios.load_scenario(SCENARIO_DIR)
    definition = loaded.definition

    assert definition.scenario.id == "orison-crisis"
    assert definition.game.max_turns == 6
    assert definition.game.primary_arguments_per_actor == 1
    assert definition.game.turn_order == ["ardin", "vesper", "orison"]
    assert set(definition.actors) == set(definition.game.turn_order)
    assert len(definition.actors) == 3
    assert all(actor.objectives for actor in definition.actors.values())
    assert all(actor.private_information for actor in definition.actors.values())

    public_resources = [item for item in loaded.resources if item.visibility == "public"]
    private_resources = [item for item in loaded.resources if item.visibility == "private"]
    assert public_resources
    assert private_resources
    assert {actor_id for item in private_resources for actor_id in item.visible_to} == set(
        definition.actors
    )
    assert all(item.source_bytes and item.content_sha256 for item in loaded.resources)
    assert {item.representation_kind for item in loaded.resources} == {"text", "attachment"}

    declared_files = {SCENARIO_DIR / item.path for item in definition.resources}
    packaged_resource_files = {
        path for path in (SCENARIO_DIR / "resources").iterdir() if path.is_file()
    }
    assert declared_files == packaged_resource_files


def test_t_065_orison_package_creates_a_complete_lobby_snapshot(db_engine: Engine) -> None:
    loaded = scenarios.load_scenario(SCENARIO_DIR)
    creation = scenarios.create_game_from_scenario(db_engine, loaded, seed=12345)

    assert set(creation.seat_tokens) == {"ardin", "vesper", "orison"}
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None
        assert game.status == GameStatus.lobby
        assert game.seed == 12345
        assert len(list_by_game(session, Actor, creation.game_id)) == 3
        assert len(list_by_game(session, Seat, creation.game_id)) == 3
        assert len(list_by_game(session, Effect, creation.game_id)) == 5
        assert len(list_by_game(session, Resource, creation.game_id)) == 10


def test_t_065_readme_gets_a_new_user_to_first_run() -> None:
    readme = (SCENARIO_DIR / "README.md").read_text(encoding="utf-8")

    assert "uv run amaw run orison-crisis" in readme
