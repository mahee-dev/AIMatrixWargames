"""Scenario manifest loading, validation and game creation (T-009…T-011).

The scenario→game pipeline: a scenario directory's ``scenario.yaml`` is parsed (with a
duplicate-mapping-key guard at the YAML boundary), validated against the Pydantic manifest
schema and the scenario-format §6/§11/§19 rules, and then turned into a persisted Game +
Actors + Seats + starting Effects that exist in LOBBY (TRD §7.1, state-machine §14).

Failure is separated into three classes (the task's explicit boundary): YAML/parse
(:class:`ScenarioParseError`), schema/rule (:class:`ScenarioValidationError`), and
game creation/persistence (:class:`GameCreationError`).

TEST-SCN-001/002 are the T-009 acceptance IDs; the remaining scenario tests cover individual
§6/§11/§19 rules. The canonical fixture (T-010) and game creation (T-011) follow. All tests
run on the isolated temp-SQLite fixtures from T-003/T-007, order-independent and key-free.
"""

from __future__ import annotations

import textwrap
import uuid
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from app import persistence, scenarios
from app.database import create_db_and_tables, engine_for_path, session_scope
from app.models import Actor, Effect, Game, GamePhase, GameStatus, Resource, Seat
from tests.api_helpers import FIXTURE_DIR, seed


# A minimal valid manifest per scenario-format §3, used as the baseline that individual
# validation tests mutate to isolate one rule at a time.
_VALID = """\
scenario:
  id: unit-demo
  version: 1
  name: Unit Demo
  description: A tiny valid scenario.

game:
  max_turns: 6
  in_world_turn_length: "1 week"
  seed: 42
  turn_order:
    - actor_a
    - actor_b
    - actor_c
  primary_arguments_per_actor: 1
  max_cons_per_actor: 3

actors:
  actor_a:
    name: Actor A
    public_description: A stakeholder.
    private_brief: You control Actor A.
    objectives:
      - Achieve A.
    private_information:
      - Private fact A.
  actor_b:
    name: Actor B
    public_description: Another stakeholder.
    private_brief: You control Actor B.
    objectives:
      - Achieve B.
  actor_c:
    name: Actor C
    public_description: A third stakeholder.
    private_brief: You control Actor C.
    objectives:
      - Achieve C.

starting_effects:
  - description: The situation is tense but stable.
    visibility: public
  - description: Actor A's plan is incomplete.
    visibility: private
    visible_to:
      - actor_a
    expires_turn: 3
"""


def _write(tmp_path: Path, text: str) -> Path:
    """Write ``text`` as scenario.yaml into a fresh directory and return that directory."""
    (tmp_path / "scenario.yaml").write_text(textwrap.dedent(text))
    return tmp_path


def _mutate(base: str, old: str, new: str) -> str:
    assert old in base, f"fixture text does not contain {old!r}"
    return base.replace(old, new)


def _with_resources(resources_yaml: str) -> str:
    return _VALID + "\nresources:\n" + textwrap.indent(textwrap.dedent(resources_yaml), "  ")


# --- T-009: TEST-SCN-001 / TEST-SCN-002 --------------------------------------------------


def test_scn_001_valid_scenario_loads(tmp_path: Path) -> None:
    """TEST-SCN-001: a valid scenario is accepted; Actors, turn order and Effects load."""
    scenario = scenarios.load_scenario(_write(tmp_path, _VALID))

    assert scenario.scenario.id == "unit-demo"
    assert set(scenario.actors) == {"actor_a", "actor_b", "actor_c"}
    assert scenario.game.turn_order == ["actor_a", "actor_b", "actor_c"]
    assert scenario.actors["actor_a"].name == "Actor A"
    assert [e.visibility for e in scenario.starting_effects] == ["public", "private"]
    assert scenario.starting_effects[1].visible_to == ["actor_a"]
    assert scenario.starting_effects[1].expires_turn == 3


def test_scn_002_unknown_actor_in_turn_order_rejected(tmp_path: Path) -> None:
    """TEST-SCN-002: an unknown Actor in turn_order fails validation before game creation."""
    text = _mutate(_VALID, "    - actor_c\n", "    - actor_c\n    - actor_missing\n")
    with pytest.raises(scenarios.ScenarioValidationError) as exc:
        scenarios.load_scenario(_write(tmp_path, text))
    assert any("actor_missing" in e for e in exc.value.errors)


# --- T-045: TEST-SCN-003…005 + trusted package boundary -------------------------------


def test_scn_003_duplicate_resource_id_rejected(tmp_path: Path) -> None:
    text = _with_resources(
        """\
        - id: duplicate
          name: First
          type: anything
          content: first
          visibility: public
        - id: duplicate
          name: Second
          type: still-open
          content: second
          visibility: public
        """
    )
    with pytest.raises(scenarios.ScenarioValidationError, match="duplicate Resource"):
        scenarios.load_scenario(_write(tmp_path, text))


def test_scn_004_missing_resource_file_rejected_before_creation(tmp_path: Path) -> None:
    text = _with_resources(
        """\
        - id: missing
          name: Missing
          type: reference
          path: resources/not-there.md
          visibility: public
        """
    )
    with pytest.raises(scenarios.ScenarioValidationError, match="missing"):
        scenarios.load_scenario(_write(tmp_path, text))


def test_scn_005_hashes_exact_bytes_and_persists_resource_snapshots(
    tmp_path: Path, db_engine: Engine
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (package / "resources").mkdir()
    file_bytes = b"exact bytes\r\nincluding line endings\n"
    (package / "resources" / "facts.md").write_bytes(file_bytes)
    manifest_text = _with_resources(
        """\
        - id: file-facts
          name: File Facts
          type: arbitrary-domain-value
          path: resources/facts.md
          visibility: public
          tags: [facts, source]
          metadata: {weight: 2}
        - id: inline-facts
          name: Inline Facts
          type: note
          content: "Inline π"
          visibility: private
          visible_to: [actor_a]
        """
    )
    manifest_bytes = manifest_text.encode()
    (package / "scenario.yaml").write_bytes(manifest_bytes)

    loaded = scenarios.load_scenario(package)
    reloaded_unchanged = scenarios.load_scenario(package)
    first_manifest_hash = loaded.resource_manifest_sha256
    assert reloaded_unchanged.scenario_content_sha256 == loaded.scenario_content_sha256
    assert reloaded_unchanged.resource_manifest_sha256 == first_manifest_hash
    assert loaded.scenario_content_sha256 == sha256(manifest_bytes).hexdigest()
    assert {r.id: r.content_sha256 for r in loaded.resources} == {
        "file-facts": sha256(file_bytes).hexdigest(),
        "inline-facts": sha256("Inline π".encode()).hexdigest(),
    }

    creation = scenarios.create_game_from_scenario(db_engine, loaded)
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        rows = sorted(
            persistence.list_by_game(session, Resource, creation.game_id), key=lambda r: r.id
        )
        assert game.scenario_content_sha256 == sha256(manifest_bytes).hexdigest()
        assert game.scenario_source_bytes == manifest_bytes
        assert game.resource_manifest_sha256 == first_manifest_hash
        assert [r.id for r in rows] == ["file-facts", "inline-facts"]
        assert [r.source_kind for r in rows] == ["file", "inline"]
        assert rows[0].resource_metadata == {"weight": 2}
        assert rows[0].attachment_name == "facts.md"
        assert rows[1].attachment_name is None
        assert rows[0].source_bytes == file_bytes
        assert rows[1].source_bytes == "Inline π".encode()

    # The loaded boundary and persisted game are snapshots: later file mutation/deletion is inert.
    (package / "resources" / "facts.md").write_bytes(b"changed")
    assert loaded.resource_manifest_sha256 == first_manifest_hash
    with session_scope(db_engine) as session:
        stored = session.get(Resource, (creation.game_id, "file-facts"))
        assert stored.source_bytes == file_bytes
        assert stored.content_sha256 == sha256(file_bytes).hexdigest()

    changed = scenarios.load_scenario(package)
    assert changed.resources[0].content_sha256 == sha256(b"changed").hexdigest()
    assert changed.resource_manifest_sha256 != first_manifest_hash
    (package / "resources" / "facts.md").unlink()
    with session_scope(db_engine) as session:
        stored = session.get(Resource, (creation.game_id, "file-facts"))
        assert stored.source_bytes == file_bytes
        assert stored.content_sha256 == sha256(file_bytes).hexdigest()


def test_scn_005_manifest_order_is_deterministic(tmp_path: Path) -> None:
    declarations = [
        "- id: zed\n  name: Zed\n  type: x\n  content: z\n  visibility: public\n",
        "- id: alpha\n  name: Alpha\n  type: y\n  content: a\n"
        "  visibility: private\n  visible_to: [actor_b]\n",
    ]
    package_a = tmp_path / "a"
    package_b = tmp_path / "b"
    package_a.mkdir()
    package_b.mkdir()
    _write(package_a, _with_resources("".join(declarations)))
    _write(package_b, _with_resources("".join(reversed(declarations))))
    first = scenarios.load_scenario(package_a)
    second = scenarios.load_scenario(package_b)
    assert first.scenario_content_sha256 != second.scenario_content_sha256
    assert first.resource_manifest_sha256 == second.resource_manifest_sha256
    assert scenarios.canonical_resource_manifest(
        first.resources
    ) == scenarios.canonical_resource_manifest(tuple(reversed(first.resources)))


def test_scn_005_semantically_equivalent_manifest_bytes_have_distinct_hashes(
    tmp_path: Path,
) -> None:
    package_a = tmp_path / "a"
    package_b = tmp_path / "b"
    package_a.mkdir()
    package_b.mkdir()
    first = scenarios.load_scenario(_write(package_a, _VALID))
    second = scenarios.load_scenario(_write(package_b, _VALID + "\n# byte-only change\n"))
    assert first.definition == second.definition
    assert first.scenario_content_sha256 != second.scenario_content_sha256
    assert first.resource_manifest_sha256 == second.resource_manifest_sha256


@pytest.mark.parametrize(
    "resource_yaml, expected",
    [
        (
            "- id: r\n  name: R\n  type: x\n  content: x\n  visibility: private\n",
            "requires visible_to",
        ),
        (
            "- id: r\n  name: R\n  type: x\n  content: x\n"
            "  visibility: private\n  visible_to: [nobody]\n",
            "unknown Actor",
        ),
        (
            "- id: r\n  name: R\n  type: x\n  content: x\n"
            "  visibility: public\n  visible_to: [actor_a]\n",
            "must not set visible_to",
        ),
        ("- id: r\n  name: R\n  type: x\n  path: /etc/passwd\n  visibility: public\n", "absolute"),
        ("- id: r\n  name: R\n  type: x\n  path: ../outside.md\n  visibility: public\n", ".."),
    ],
)
def test_invalid_resource_visibility_and_paths_rejected(
    tmp_path: Path, resource_yaml: str, expected: str
) -> None:
    with pytest.raises(scenarios.ScenarioValidationError, match=expected):
        scenarios.load_scenario(_write(tmp_path, _with_resources(resource_yaml)))


def test_resource_directory_rejected(tmp_path: Path) -> None:
    (tmp_path / "resources").mkdir()
    yaml_text = "- id: r\n  name: R\n  type: x\n  path: resources\n  visibility: public\n"
    with pytest.raises(scenarios.ScenarioValidationError, match="regular file"):
        scenarios.load_scenario(_write(tmp_path, _with_resources(yaml_text)))


def test_resource_symlink_escape_rejected(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    try:
        (package / "escape.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    yaml_text = "- id: r\n  name: R\n  type: x\n  path: escape.md\n  visibility: public\n"
    with pytest.raises(scenarios.ScenarioValidationError, match="outside"):
        scenarios.load_scenario(_write(package, _with_resources(yaml_text)))


def test_same_resource_id_allowed_across_games_but_duplicate_pair_rejected(
    db_engine: Engine
) -> None:
    first = scenarios.create_game_from_scenario(db_engine, scenarios.load_scenario(FIXTURE_DIR))
    second = scenarios.create_game_from_scenario(db_engine, scenarios.load_scenario(FIXTURE_DIR))
    with session_scope(db_engine) as session:
        assert session.get(Resource, (first.game_id, "background")) is not None
        assert session.get(Resource, (second.game_id, "background")) is not None
        original = session.get(Resource, (first.game_id, "background"))
        duplicate = Resource(
            game_id=first.game_id,
            id=original.id,
            name=original.name,
            type=original.type,
            visibility=original.visibility,
            visible_to=original.visible_to,
            tags=original.tags,
            resource_metadata=original.resource_metadata,
            source_kind=original.source_kind,
            source_bytes=original.source_bytes,
            attachment_name=original.attachment_name,
            content_sha256=original.content_sha256,
        )
        session.expunge(original)
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# --- T-009: focused validation rules (scenario-format §6, §11, §19) ----------------------


def test_malformed_yaml_raises_parse_error(tmp_path: Path) -> None:
    with pytest.raises(scenarios.ScenarioParseError):
        scenarios.load_scenario(_write(tmp_path, "scenario: [unbalanced\n"))


def test_duplicate_actor_id_rejected_at_parse_boundary(tmp_path: Path) -> None:
    """Duplicate mapping keys would be silently collapsed by YAML; the loader must catch them."""
    text = _mutate(
        _VALID,
        "  actor_c:\n    name: Actor C\n    public_description: A third stakeholder.\n"
        "    private_brief: You control Actor C.\n    objectives:\n      - Achieve C.\n",
        "  actor_c:\n    name: Actor C\n    public_description: A third stakeholder.\n"
        "    private_brief: You control Actor C.\n    objectives:\n      - Achieve C.\n"
        "  actor_a:\n    name: Dup A\n    public_description: dup.\n"
        "    private_brief: dup.\n    objectives:\n      - Dup.\n",
    )
    with pytest.raises(scenarios.ScenarioParseError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_missing_scenario_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(scenarios.ScenarioError):
        scenarios.load_scenario(tmp_path)


def test_duplicate_actor_in_turn_order_rejected(tmp_path: Path) -> None:
    text = _mutate(_VALID, "    - actor_c\n", "    - actor_c\n    - actor_a\n")
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_empty_turn_order_rejected(tmp_path: Path) -> None:
    text = _mutate(
        _VALID,
        "  turn_order:\n    - actor_a\n    - actor_b\n    - actor_c\n",
        "  turn_order: []\n",
    )
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_invalid_max_turns_rejected(tmp_path: Path) -> None:
    text = _mutate(_VALID, "  max_turns: 6\n", "  max_turns: 0\n")
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_invalid_primary_argument_count_rejected(tmp_path: Path) -> None:
    text = _mutate(_VALID, "  primary_arguments_per_actor: 1\n", "  primary_arguments_per_actor: 0\n")
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_negative_max_cons_rejected(tmp_path: Path) -> None:
    text = _mutate(_VALID, "  max_cons_per_actor: 3\n", "  max_cons_per_actor: -1\n")
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_actor_without_objectives_rejected(tmp_path: Path) -> None:
    text = _mutate(
        _VALID,
        "  actor_b:\n    name: Actor B\n    public_description: Another stakeholder.\n"
        "    private_brief: You control Actor B.\n    objectives:\n      - Achieve B.\n",
        "  actor_b:\n    name: Actor B\n    public_description: Another stakeholder.\n"
        "    private_brief: You control Actor B.\n    objectives: []\n",
    )
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_missing_required_scenario_field_rejected(tmp_path: Path) -> None:
    text = _mutate(_VALID, "  id: unit-demo\n", "")
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_invalid_effect_visibility_rejected(tmp_path: Path) -> None:
    text = _mutate(
        _VALID, "    visibility: public\n", "    visibility: secret\n",
    )
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_private_effect_without_visible_to_rejected(tmp_path: Path) -> None:
    text = _mutate(
        _VALID,
        "  - description: Actor A's plan is incomplete.\n    visibility: private\n"
        "    visible_to:\n      - actor_a\n    expires_turn: 3\n",
        "  - description: Actor A's plan is incomplete.\n    visibility: private\n",
    )
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_private_effect_visible_to_unknown_actor_rejected(tmp_path: Path) -> None:
    text = _mutate(_VALID, "      - actor_a\n    expires_turn: 3\n", "      - nobody\n    expires_turn: 3\n")
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


def test_unknown_top_level_field_rejected_strict(tmp_path: Path) -> None:
    """Strict policy (scenario-format §4): unknown structured fields are rejected."""
    text = _VALID + "\nbogus_section:\n  x: 1\n"
    with pytest.raises(scenarios.ScenarioValidationError):
        scenarios.load_scenario(_write(tmp_path, text))


@pytest.mark.parametrize(
    "marker",
    ["TODO choose state", "tbd", "FIXME resolve", "FYLL_I_PROCEDURLAGE", "<placeholder>"],
)
def test_scn_006_unresolved_marker_in_starting_effect_rejected(
    tmp_path: Path, marker: str
) -> None:
    """TEST-SCN-006: authoring markers may not enter authoritative initial state."""
    text = _mutate(_VALID, "The situation is tense but stable.", marker)
    with pytest.raises(scenarios.ScenarioValidationError, match="unresolved authoring marker"):
        scenarios.load_scenario(_write(tmp_path, text))


def test_scn_006_marker_discussion_outside_starting_effect_is_allowed(tmp_path: Path) -> None:
    """The lint is deliberately scoped to authoritative Effect descriptions."""
    text = _mutate(
        _VALID,
        "description: A tiny valid scenario.",
        "description: Documentation may explain a TODO marker.",
    )
    assert scenarios.load_scenario(_write(tmp_path, text)).scenario.id == "unit-demo"


# --- T-010: canonical deterministic fixture ---------------------------------------------


def test_canonical_fixture_validates_through_loader() -> None:
    """T-010: tests/fixtures/deterministic-game/ loads through the T-009 loader."""
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    assert len(scenario.actors) == 3
    assert scenario.game.max_turns == 6
    assert scenario.game.turn_order == ["arkania", "belvar", "cestia"]  # order preserved
    assert scenario.game.primary_arguments_per_actor == 1
    assert scenario.game.seed is not None
    assert len(scenario.starting_effects) >= 1
    # public and private resource declarations are present (parsed, not materialised here)
    assert any(r.visibility == "private" for r in scenario.resources)
    assert any(r.visibility == "public" for r in scenario.resources)


def test_canonical_fixture_turn_order_matches_actors() -> None:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    assert set(scenario.game.turn_order) == set(scenario.actors)


# --- T-011: game creation from a validated scenario -------------------------------------


def _load_fixture() -> scenarios.LoadedScenario:
    return scenarios.load_scenario(FIXTURE_DIR)


def test_create_game_produces_one_lobby_game(db_engine: Engine) -> None:
    scenario = _load_fixture()
    result = scenarios.create_game_from_scenario(db_engine, scenario)

    with session_scope(db_engine) as session:
        game = persistence.get(session, Game, result.game_id)
        assert game is not None
        assert game.status is GameStatus.lobby
        assert game.turn == 0
        assert game.phase is GamePhase.diplomacy  # phase the game will enter at start (T-013)
        assert game.active_actor_id is None
        assert game.rng_draw_index == 0
        assert game.diplomacy_ready_actor_ids == []


def test_create_game_snapshots_execution_rules(db_engine: Engine) -> None:
    scenario = _load_fixture()
    result = scenarios.create_game_from_scenario(db_engine, scenario)
    with session_scope(db_engine) as session:
        game = persistence.get(session, Game, result.game_id)
        assert game.scenario_id == scenario.scenario.id
        assert game.max_turns == scenario.game.max_turns
        assert game.turn_order == scenario.game.turn_order
        assert game.primary_arguments_per_actor == scenario.game.primary_arguments_per_actor
        assert game.max_cons_per_actor == scenario.game.max_cons_per_actor
        assert game.seed == scenario.game.seed


def test_create_game_persists_actor_snapshots(db_engine: Engine) -> None:
    scenario = _load_fixture()
    result = scenarios.create_game_from_scenario(db_engine, scenario)
    with session_scope(db_engine) as session:
        actors = persistence.list_by_game(session, Actor, result.game_id)
        assert len(actors) == 3
        first_id = scenario.game.turn_order[0]
        actor = persistence.get(session, Actor, {"game_id": result.game_id, "id": first_id})
        src = scenario.actors[first_id]
        assert actor.public_description == src.public_description
        assert actor.private_brief == src.private_brief
        assert actor.objectives == src.objectives
        assert actor.private_information == src.private_information


def test_create_game_makes_one_seat_per_actor_hash_only(db_engine: Engine) -> None:
    scenario = _load_fixture()
    result = scenarios.create_game_from_scenario(db_engine, scenario)
    assert set(result.seat_tokens) == set(scenario.actors)
    with session_scope(db_engine) as session:
        seats = persistence.list_by_game(session, Seat, result.game_id)
        assert len(seats) == 3
        for seat in seats:
            raw = result.seat_tokens[seat.actor_id]
            # Only the hash is persisted, never the raw token.
            assert seat.token_hash != raw
            assert seat.token_hash == scenarios.hash_token(raw)


def test_create_game_persists_starting_effects(db_engine: Engine) -> None:
    scenario = _load_fixture()
    result = scenarios.create_game_from_scenario(db_engine, scenario)
    with session_scope(db_engine) as session:
        effects = persistence.list_by_game(session, Effect, result.game_id)
        assert len(effects) == len(scenario.starting_effects)
        for eff in effects:
            assert eff.created_turn == 0
            assert eff.active is True
            assert eff.source_argument_id is None
            assert eff.supersedes == []


def test_create_game_reloads_in_fresh_engine(db_path: Path) -> None:
    """The created game reads back through a fresh engine — no in-memory carry-over (TRD §26)."""
    engine_a = engine_for_path(db_path)
    create_db_and_tables(engine_a)
    result = scenarios.create_game_from_scenario(engine_a, _load_fixture())
    game_id = result.game_id
    engine_a.dispose()

    engine_b = engine_for_path(db_path)
    try:
        with session_scope(engine_b) as session:
            game = persistence.get(session, Game, game_id)
            assert game is not None and game.status is GameStatus.lobby
            assert len(persistence.list_by_game(session, Actor, game_id)) == 3
            assert len(persistence.list_by_game(session, Seat, game_id)) == 3
    finally:
        engine_b.dispose()


def test_create_game_requires_a_seed(db_engine: Engine) -> None:
    scenario = _load_fixture()
    scenario.game.seed = None
    with pytest.raises(scenarios.GameCreationError):
        scenarios.create_game_from_scenario(db_engine, scenario, seed=None)


def test_injected_failure_rolls_back_entire_creation(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure partway through creation leaves no partial Game/Actors/Seats/Effects."""
    scenario = _load_fixture()
    real_create = persistence.create
    calls = {"n": 0}

    def flaky_create(session, entity):
        calls["n"] += 1
        if calls["n"] == 3:  # fail after the Game and first Actor are added
            raise RuntimeError("injected mid-creation failure")
        return real_create(session, entity)

    monkeypatch.setattr(scenarios.persistence, "create", flaky_create)
    with pytest.raises(RuntimeError, match="injected"):
        scenarios.create_game_from_scenario(db_engine, scenario)

    with session_scope(db_engine) as session:
        assert session.get(Game, uuid.UUID(int=0)) is None  # sanity: db reachable
        # No game, no actors, no seats, no effects survived the rollback.
        from sqlmodel import select

        assert session.exec(select(Game)).all() == []
        assert session.exec(select(Actor)).all() == []
        assert session.exec(select(Seat)).all() == []
        assert session.exec(select(Effect)).all() == []
        assert session.exec(select(Resource)).all() == []


def test_resource_creation_failure_rolls_back_game_and_all_related_rows(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = scenarios.load_scenario(FIXTURE_DIR)
    real_create = persistence.create

    def fail_on_resource(session, entity):
        if isinstance(entity, Resource):
            raise RuntimeError("injected Resource creation failure")
        return real_create(session, entity)

    monkeypatch.setattr(scenarios.persistence, "create", fail_on_resource)
    with pytest.raises(RuntimeError, match="Resource creation"):
        scenarios.create_game_from_scenario(db_engine, loaded)

    from sqlmodel import select

    with session_scope(db_engine) as session:
        assert session.exec(select(Game)).all() == []
        assert session.exec(select(Actor)).all() == []
        assert session.exec(select(Seat)).all() == []
        assert session.exec(select(Effect)).all() == []
        assert session.exec(select(Resource)).all() == []


# --- the scenario context is snapshotted on the Game --------------------------------------


def test_i013_scenario_context_is_snapshotted(db_engine: Engine) -> None:
    creation = seed(db_engine)
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game.scenario_version is not None
        assert game.scenario_summary and game.scenario_summary != game.scenario_id
