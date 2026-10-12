"""T-075 acceptance: the clean-room total-defence scenario runs on the generic engine.

The package ``scenarios/skaro-crisis`` is scenario data only (WBS T-075, D-089): at least five
conflicting Actors with public and private information, public and private Resources including a
representational map, first-class ``rules`` (scenario-format §11.1, D-090) and a declared
``consequence_mode: bounded_effects_v1`` (§6, D-091). These tests prove it loads, plays to
completion offline, exports, re-runs identically from the same seed ("replay" in the Lean sense —
full replay is out of scope, testing.md §25) and needs no scenario-specific Python.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import Engine
from typer.testing import CliRunner

import amaw_cli
from agents.umpire import FakeUmpire
from agents.scripted import (
    build_scripted_players,
    deterministic_consequence_resolver,
    deterministic_umpire,
    run_deterministic_game,
)
from app import database, export, game_service, scenarios
from app.consequences import ConsequenceContext
from app.database import session_scope
from app.models import Actor, Effect, Game, GameStatus, Resource, Seat
from app.persistence import list_by_game
from app.umpire import AdjudicationContext
from tests.cli_helpers import capture_run_cli

REPO = Path(__file__).parents[1]
SCENARIO_ID = "skaro-crisis"
SCENARIO_DIR = REPO / "scenarios" / SCENARIO_ID
BOUNDED = "bounded_effects_v1"
SEED = 7507


def _manifest() -> dict:
    return yaml.safe_load((SCENARIO_DIR / "scenario.yaml").read_text(encoding="utf-8"))


def _rules() -> list[dict]:
    return [rule.model_dump() for rule in scenarios.load_scenario(SCENARIO_DIR).definition.rules]


def _play(engine: Engine, *, seed: int = SEED):
    """Create, start and play the scenario offline, recording every Umpire/resolver context."""
    loaded = scenarios.load_scenario(SCENARIO_DIR)
    # The scripted players Con on every opponent's Argument, which needs the released open
    # condition. Since 1.2.0 the scenario declares bounded Diplomacy and sampled Cons (I-128),
    # so this offline play overrides them explicitly.
    creation = scenarios.create_game_from_scenario(
        engine, loaded, seed=seed, diplomacy_mode="open_diplomacy", con_mode="open_cons"
    )
    game_service.start_game(engine, creation.game_id)
    umpire, resolver = deterministic_umpire(), deterministic_consequence_resolver()
    seen = SimpleNamespace(adjudications=[], consequences=[])

    def adjudicate(ctx: AdjudicationContext):
        seen.adjudications.append(ctx)
        return umpire.adjudicate(ctx)

    class Recording:
        def propose(self, ctx: ConsequenceContext):
            seen.consequences.append(ctx)
            return resolver.propose(ctx)

    run_deterministic_game(
        engine,
        creation.game_id,
        build_scripted_players(loaded.game.turn_order),
        FakeUmpire(responder=adjudicate),
        consequence_resolver=Recording(),
    )
    return loaded, creation, seen


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _normalised(value):
    """The export with UUIDs masked and wall-clock fields dropped.

    UUIDs are random per run and some export sections are ordered by id, so lists compare as
    multisets. Order-bearing values (event ids, turns, draw indexes, rolls) are integers and
    stay in the comparison.
    """
    if isinstance(value, dict):
        return {
            k: _normalised(v)
            for k, v in value.items()
            if not (k.endswith("_at") or k.endswith("_ms"))
        }
    if isinstance(value, list):
        return sorted((_normalised(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, str):
        return _UUID.sub("<uuid>", value)
    return value


# --- TEST-T075-001: the package loads and exercises every structural demand ---------------


def test_t075_001_package_is_a_complete_clean_room_portability_scenario() -> None:
    loaded = scenarios.load_scenario(SCENARIO_DIR)
    definition = loaded.definition

    assert definition.scenario.id == SCENARIO_ID
    assert definition.game.primary_arguments_per_actor == 1  # I-109 stays out (audit §9)
    assert loaded.game.consequence_mode == BOUNDED
    assert len(definition.actors) >= 5
    assert set(definition.game.turn_order) == set(definition.actors)
    assert all(a.objectives and a.private_information for a in definition.actors.values())

    rules = _rules()
    assert rules and len(rules) <= 32
    assert all(set(rule) == {"id", "text"} for rule in rules)

    resources = loaded.resources
    private = [r for r in resources if r.visibility == "private"]
    assert [r for r in resources if r.visibility == "public"] and private
    # Asymmetric information: every Actor holds at least one private Resource of its own.
    assert {actor for r in private for actor in r.visible_to} == set(definition.actors)
    maps = [r for r in resources if r.type == "map"]
    assert maps and all(r.representation_kind == "attachment" for r in maps)
    assert all(item.source_bytes and item.content_sha256 for item in resources)

    visibilities = {e.visibility for e in definition.starting_effects}
    assert visibilities == {"public", "private"}

    declared = {SCENARIO_DIR / item.path for item in definition.resources}
    packaged = {p for p in (SCENARIO_DIR / "resources").iterdir() if p.is_file()}
    assert declared == packaged

    for doc in ("README.md", "AUTHOR_NOTES.md"):
        assert (SCENARIO_DIR / doc).is_file()
    assert SCENARIO_ID in (REPO / "scenarios" / "README.md").read_text(encoding="utf-8")


# --- TEST-T075-002: the game snapshot carries rules, condition and declared content -------


def test_t075_002_game_creation_snapshots_rules_condition_and_content(db_engine: Engine) -> None:
    loaded = scenarios.load_scenario(SCENARIO_DIR)
    manifest = _manifest()
    creation = scenarios.create_game_from_scenario(db_engine, loaded, seed=SEED)

    assert set(creation.seat_tokens) == set(manifest["actors"])
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game.status is GameStatus.lobby
        assert game.scenario_rules == manifest["rules"]
        assert game.consequence_mode == BOUNDED
        assert len(list_by_game(session, Actor, creation.game_id)) == len(manifest["actors"])
        assert len(list_by_game(session, Seat, creation.game_id)) == len(manifest["actors"])
        assert len(list_by_game(session, Effect, creation.game_id)) == len(
            manifest["starting_effects"]
        )
        assert len(list_by_game(session, Resource, creation.game_id)) == len(manifest["resources"])


# --- TEST-T075-003: the scenario plays to completion on the generic engine ---------------


def test_t075_003_full_game_completes_offline_with_rules_and_consequences(
    db_engine: Engine,
) -> None:
    loaded, creation, seen = _play(db_engine)
    turns, actors = loaded.game.max_turns, len(loaded.game.turn_order)

    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game.status is GameStatus.complete and game.turn == turns
        # One server draw per adjudication; the consequence pass draws none (INV-RNG-001).
        assert game.rng_draw_index == turns * actors

    assert len(seen.adjudications) == turns * actors
    assert all(ctx.scenario_rules == _rules() for ctx in seen.adjudications)
    assert [ctx.turn for ctx in seen.consequences] == list(range(1, turns + 1))
    assert all(ctx.scenario_rules == _rules() for ctx in seen.consequences)

    record = export.build_game_export(db_engine, creation.game_id)
    assert [r["turn"] for r in record["consequence_records"]] == list(range(1, turns + 1))
    consequence_effects = [e for e in record["effects"] if e["source_consequence_turn"]]
    assert consequence_effects and all(e["visibility"] == "public" for e in consequence_effects)
    kinds = {e["type"] for e in record["events"]}
    assert {"message_sent", "con_submitted", "argument_adjudicated", "game_completed"} <= kinds


# --- TEST-T075-004: export and replay ------------------------------------------------------


@pytest.mark.slow
def test_t075_004_export_carries_provenance_and_the_same_seed_replays_identically(
    db_engine: Engine, tmp_path: Path
) -> None:
    _, first, _ = _play(db_engine)
    record = export.build_game_export(db_engine, first.game_id)
    assert record["game"]["scenario_id"] == SCENARIO_ID
    assert record["game"]["scenario_rules"] == _rules()
    assert record["game"]["consequence_mode"] == BOUNDED
    assert record["game"]["seed"] == SEED

    other = database.engine_for_path(tmp_path / "replay.db")
    database.initialize_database(other)
    try:
        _, second, _ = _play(other)
        replayed = export.build_game_export(other, second.game_id)
    finally:
        other.dispose()
    assert _normalised(replayed) == _normalised(record)


# --- TEST-T075-005: no scenario-specific Python -------------------------------------------


def test_t075_005_no_engine_code_names_the_scenario() -> None:
    manifest = _manifest()
    names = {SCENARIO_ID, *manifest["actors"], *(rule["id"] for rule in manifest["rules"])}
    sources = [
        path for package in ("amaw_cli", "app", "agents", "benchmark")
        for path in (REPO / package).rglob("*")
        if path.is_file() and path.suffix in {".py", ".md", ".txt", ".yaml", ".json"}
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        hits = sorted(name for name in names if re.search(rf"\b{re.escape(name)}\b", text))
        assert not hits, f"{path.relative_to(REPO)} names scenario content: {hits}"


# --- TEST-T075-006: `amaw run` resolves the scenario's conditions -------------------------


def test_t075_006_run_resolves_rules_prompt_and_declared_consequence_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    # Run by path: the repository scenario is not wheel-bundled (from a checkout it also runs by
    # name, TEST-I152-001).
    base = ["run", str(SCENARIO_DIR), "--player-model", "p/m", "--umpire-model", "u/m", "--seed", "1",
            "--con-reply-mode", "visible_reply", "--reply-max-points", "2"]

    # Without --consequence-model the resolver is the Umpire model (D-115).
    result = CliRunner().invoke(amaw_cli.app, base)
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["run_configuration"]["resume"]["consequence"]["model"] == "u/m"

    result = CliRunner().invoke(amaw_cli.app, [*base, "--consequence-model", "c/m"])
    assert result.exit_code == 0, result.output
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"  # D-114 default
    assert captured["runner_options"]["consequence_mode"] == BOUNDED
    resume = captured["runner_options"]["run_configuration"]["resume"]
    assert resume["consequence"]["model"] == "c/m"
    assert captured["resolver_options"]["prompt_version"] == "consequence-v1.1"

