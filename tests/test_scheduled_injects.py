"""T-081 acceptance: scheduled scenario injects (D-095, scenario-format §11.2, state-machine §15.5a).

An inject is an authored Effect the server releases, unconditionally and exactly once, at the
start of its turn — after expiry, before Diplomacy opens — and that no Actor can see before then.
Released, it is an ordinary Effect carrying ``source_inject_id``.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine
from sqlmodel import select

from agents.scripted import (
    build_scripted_players,
    deterministic_consequence_resolver,
    deterministic_umpire,
    run_deterministic_game,
)
from agents.umpire import FakeUmpire
from app import context as actor_context_module
from app import database, export, game_service, scenarios
from app.consequences import ConsequenceContext
from app.database import session_scope
from app.models import Effect, Game, GameEvent
from app.umpire import AdjudicationContext
from app.visibility import visible_events
from tests.api_helpers import FIXTURE_DIR, bearer, client
from tests.test_scenarios import _VALID, _write

ACTORS = ("arkania", "belvar", "cestia")
SEED = 8181

_INJECTS = """
injects:
  - id: port-strike
    turn: 2
    description: "Dockworkers at the main port begin an open-ended strike."
    visibility: public
  - id: arkania-order
    turn: 2
    description: "Arkania's capital orders its delegation to secure a deal within two turns."
    visibility: private
    visible_to:
      - arkania
    expires_turn: 4
  - id: late-storm
    turn: 3
    description: "A winter storm closes the northern shipping lane for a week."
    visibility: public
"""


def _package(tmp_path: Path, *, injects: str = _INJECTS, max_turns: int = 3, mode: str | None = None) -> Path:
    package = tmp_path / "pkg"
    shutil.copytree(FIXTURE_DIR, package)
    manifest = package / "scenario.yaml"
    text = manifest.read_text(encoding="utf-8").replace("  max_turns: 6\n", f"  max_turns: {max_turns}\n")
    if mode is not None:
        text = text.replace("  max_cons_per_actor: 3\n", f"  max_cons_per_actor: 3\n  consequence_mode: {mode}\n")
    manifest.write_text(text.rstrip("\n") + "\n" + injects, encoding="utf-8")
    return package


def _create(engine: Engine, package: Path) -> scenarios.GameCreation:
    loaded = scenarios.load_scenario(package)
    creation = scenarios.create_game_from_scenario(engine, loaded, seed=SEED)
    game_service.start_game(engine, creation.game_id)
    return creation


def _to_next_turn(engine: Engine, game_id: uuid.UUID) -> None:
    """Play one turn with the scripted driver's building blocks, stopping at the next turn start."""
    with session_scope(engine) as session:
        start_turn = session.get(Game, game_id).turn
    for actor in ACTORS:
        game_service.ready(engine, game_id, actor)
    for owner in ACTORS:
        result = game_service.make_argument(
            engine, game_id, actor_id=owner, action="Act", desired_effect="Effect", pros=["Pro"]
        )
        for opponent in ACTORS:
            if opponent != owner:
                game_service.pass_con(engine, game_id, argument_id=result.id, actor_id=opponent)
        game_service.resolve_adjudication(engine, game_id, deterministic_umpire())
        game_service.advance_after_resolution(engine, game_id)
    with session_scope(engine) as session:
        assert session.get(Game, game_id).turn == start_turn + 1


def _inject_effects(engine: Engine, game_id: uuid.UUID) -> list[SimpleNamespace]:
    with session_scope(engine) as session:
        return [
            SimpleNamespace(
                id=str(e.id), inject=e.source_inject_id, turn=e.created_turn,
                visibility=e.visibility, visible_to=list(e.visible_to), expires=e.expires_turn,
                description=e.description, active=e.active,
            )
            for e in session.exec(
                select(Effect).where(Effect.game_id == game_id, Effect.source_inject_id != None)  # noqa: E711
            ).all()
        ]


def _events(engine: Engine, game_id: uuid.UUID) -> list[SimpleNamespace]:
    with session_scope(engine) as session:
        return [
            SimpleNamespace(type=e.type, turn=e.turn, payload=dict(e.payload or {}))
            for e in session.exec(
                select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
            ).all()
        ]


# --- TEST-T081-001: validation ------------------------------------------------------------


def _scenario_with(injects: str) -> str:
    return _VALID + "\ninjects:\n" + injects


_GOOD = """\
  - id: storm
    turn: 2
    description: A storm arrives.
    visibility: public
"""


def test_t081_001_valid_injects_load_and_absent_means_empty(tmp_path: Path) -> None:
    loaded = scenarios.load_scenario(_write(tmp_path, _scenario_with(_GOOD)))
    assert [i.id for i in loaded.definition.injects] == ["storm"]
    (tmp_path / "b").mkdir()
    assert scenarios.load_scenario(_write(tmp_path / "b", _VALID)).definition.injects == []


@pytest.mark.parametrize(
    ("injects", "fragment"),
    [
        (_GOOD.replace("turn: 2", "turn: 1"), "turn"),
        (_GOOD.replace("turn: 2", "turn: 7"), "turn"),
        (_GOOD.replace("id: storm", "id: Bad Id"), "id"),
        (_GOOD + _GOOD, "duplicate"),
        (_GOOD.replace("A storm arrives.", "'   '"), "description"),
        (_GOOD.replace("A storm arrives.", "TODO write this"), "marker"),
        (_GOOD.replace("A storm arrives.", "x" * 1001), "description"),
        (_GOOD.replace("visibility: public", "visibility: private"), "visible_to"),
        (_GOOD.replace("visibility: public", "visibility: private\n    visible_to: [ghost]"), "unknown Actor"),
        (_GOOD.replace("visibility: public", "visibility: public\n    visible_to: [actor_a]"), "visible_to"),
        (_GOOD + "    expires_turn: 2\n", "expires_turn"),
        (_GOOD + "    condition: repaired\n", "condition"),
        ("".join(_GOOD.replace("storm", f"s{i}") for i in range(33)), "33"),
    ],
)
def test_t081_001_malformed_injects_are_rejected(tmp_path: Path, injects: str, fragment: str) -> None:
    with pytest.raises(scenarios.ScenarioValidationError) as raised:
        scenarios.load_scenario(_write(tmp_path, _scenario_with(injects)))
    joined = " ".join(raised.value.errors)
    assert "inject" in joined, joined
    assert fragment.lower() in joined.lower(), joined


# --- TEST-T081-002: release at the turn start ---------------------------------------------


def test_t081_002_injects_release_at_their_turn_start_in_order(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path))
    game_id = creation.game_id
    assert _inject_effects(db_engine, game_id) == []

    _to_next_turn(db_engine, game_id)
    released = _inject_effects(db_engine, game_id)
    assert [(e.inject, e.turn, e.visibility, e.visible_to, e.expires) for e in released] == [
        ("port-strike", 2, "public", [], None),
        ("arkania-order", 2, "private", ["arkania"], 4),
    ]

    events = _events(db_engine, game_id)
    completed = max(i for i, e in enumerate(events) if e.type == "turn_completed")
    diplomacy = next(
        i for i, e in enumerate(events)
        if i > completed and e.type == "phase_started" and e.payload == {"phase": "diplomacy"}
    )
    between = events[completed + 1:diplomacy]
    assert [(e.type, e.payload.get("source_inject_id")) for e in between] == [
        ("effect_created", "port-strike"),
        ("effect_created", "arkania-order"),
    ]
    assert all(e.turn == 2 for e in between)

    _to_next_turn(db_engine, game_id)
    assert [e.inject for e in _inject_effects(db_engine, game_id)] == [
        "port-strike", "arkania-order", "late-storm",
    ]


# --- TEST-T081-003: hidden until released; private injects stay private -------------------


def test_t081_003_no_actor_sees_an_inject_before_release(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path))
    game_id = creation.game_id
    api = client(db_engine)
    for actor in ACTORS:
        body = json.dumps(api.get(f"/games/{game_id}/context", headers=bearer(creation.seat_tokens[actor])).json())
        events = json.dumps(api.get(f"/games/{game_id}/events", headers=bearer(creation.seat_tokens[actor])).json())
        for text in ("port-strike", "Dockworkers", "arkania-order", "secure a deal", "late-storm", "winter storm"):
            assert text not in body and text not in events

    _to_next_turn(db_engine, game_id)
    for actor in ACTORS:
        view = actor_context_module.actor_context(db_engine, game_id, actor)
        descriptions = " ".join(e["description"] for e in view["effects"])
        assert "Dockworkers" in descriptions
        assert ("secure a deal" in descriptions) is (actor == "arkania")
        assert "winter storm" not in descriptions  # turn 3's inject is not out yet
        with session_scope(db_engine) as session:
            seen = [e.payload.get("source_inject_id") for e in visible_events(session, game_id, actor)]
        assert "port-strike" in seen
        assert ("arkania-order" in seen) is (actor == "arkania")
        assert "late-storm" not in seen


# --- TEST-T081-004: exactly once, across a restart, no RNG --------------------------------


def test_t081_004_released_once_even_across_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "game.db"
    first = database.engine_for_path(path)
    database.initialize_database(first)
    creation = _create(first, _package(tmp_path))
    with session_scope(first) as session:
        draws_before = session.get(Game, creation.game_id).rng_draw_index
    _to_next_turn(first, creation.game_id)
    with session_scope(first) as session:
        game = session.get(Game, creation.game_id)
        # Three adjudications drew; the release itself drew nothing.
        assert game.rng_draw_index == draws_before + 3
    first.dispose()

    second = database.engine_for_path(path)
    database.initialize_database(second)
    run_deterministic_game(
        second, creation.game_id, build_scripted_players(ACTORS), deterministic_umpire()
    )
    injects = [e.inject for e in _inject_effects(second, creation.game_id)]
    assert injects == ["port-strike", "arkania-order", "late-storm"]
    created = [e for e in _events(second, creation.game_id) if e.payload.get("source_inject_id")]
    assert len(created) == 3
    second.dispose()


def test_t081_004_an_existing_release_is_never_duplicated(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path))
    _to_next_turn(db_engine, creation.game_id)
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        game_service.turns._release_injects(session, game)  # a second attempt for the same turn
    assert [e.inject for e in _inject_effects(db_engine, creation.game_id)] == [
        "port-strike", "arkania-order",
    ]


# --- TEST-T081-005: the Umpire and the consequence pass see released injects -----------------


def test_t081_005_umpire_and_consequence_pass_see_released_injects(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, mode="bounded_effects_v1"))
    seen = SimpleNamespace(adjudications=[], consequences=[])
    umpire, resolver = deterministic_umpire(), deterministic_consequence_resolver()

    def adjudicate(ctx: AdjudicationContext):
        seen.adjudications.append(ctx)
        return umpire.adjudicate(ctx)

    class Recording:
        def propose(self, ctx: ConsequenceContext):
            seen.consequences.append(ctx)
            return resolver.propose(ctx)

    run_deterministic_game(
        db_engine, creation.game_id, build_scripted_players(ACTORS),
        FakeUmpire(responder=adjudicate), consequence_resolver=Recording(),
    )
    turn_two = [ctx for ctx in seen.adjudications if ctx.turn == 2]
    assert all(
        any("Dockworkers" in e["description"] for e in ctx.relevant_effects) for ctx in turn_two
    )
    # The private inject reaches the Umpire only as owner-tagged hidden information (D-029).
    assert all(
        {"kind": "private_effect", "actor_id": "arkania", "text": "Arkania's capital orders its delegation to secure a deal within two turns."}
        in ctx.relevant_hidden_information
        for ctx in turn_two
    )
    assert not any(
        "Dockworkers" in e["description"] for ctx in seen.adjudications if ctx.turn == 1 for e in ctx.relevant_effects
    )
    turn_two_pass = next(ctx for ctx in seen.consequences if ctx.turn == 2)
    assert any("Dockworkers" in e["description"] for e in turn_two_pass.active_effects)
    assert not any("secure a deal" in e["description"] for e in turn_two_pass.active_effects)


# --- TEST-T081-006: scenarios without injects; the export ---------------------------------


def test_t081_006_export_records_injects_and_their_effects(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path))
    run_deterministic_game(
        db_engine, creation.game_id, build_scripted_players(ACTORS), deterministic_umpire()
    )
    exported = export.build_game_export(db_engine, creation.game_id)
    assert exported["export_format_version"] == "16"
    assert [i["id"] for i in exported["game"]["scenario_injects"]] == [
        "port-strike", "arkania-order", "late-storm",
    ]
    sources = sorted(e["source_inject_id"] for e in exported["effects"] if e["source_inject_id"])
    assert sources == ["arkania-order", "late-storm", "port-strike"]


def test_t081_006_a_scenario_without_injects_is_unchanged(db_engine: Engine, tmp_path: Path) -> None:
    creation = _create(db_engine, _package(tmp_path, injects=""))
    run_deterministic_game(
        db_engine, creation.game_id, build_scripted_players(ACTORS), deterministic_umpire()
    )
    exported = export.build_game_export(db_engine, creation.game_id)
    assert exported["game"]["scenario_injects"] == []
    assert all(e["source_inject_id"] is None for e in exported["effects"])
    assert not [e for e in exported["events"] if "source_inject_id" in (e.get("payload") or {})]
