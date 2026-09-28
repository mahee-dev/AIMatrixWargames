"""T-083 acceptance: ``skaro-crisis`` 1.1.0 — late-game pressure through scheduled injects (I-116,
D-095) and a cheaper recommended run (bounded Diplomacy, sampled Cons, ``umpire-v1.6``).

Content only: the engine is unchanged, and the T-075 acceptance tests keep passing.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import select
from typer.testing import CliRunner

import amaw_cli
from app import scenarios
from app.database import session_scope
from app.models import Effect, Game, GameStatus
from tests.cli_helpers import capture_run_cli
from tests.test_scenario_skaro_crisis import SCENARIO_DIR, _play

EXPECTED = {
    "port-systems-intrusion": (3, "public", []),
    "drevna-exercise-extension": (3, "private", ["drevna"]),
    "aldmoor-parliament-deadline": (3, "private", ["aldmoor"]),
    "storm-closes-links": (4, "public", []),
    "vessel-anchored-near-landing": (4, "public", []),
}


# --- TEST-T083-001: the 1.1.0 package declares five unconditional injects -----------------


def test_t083_001_version_and_injects() -> None:
    definition = scenarios.load_scenario(SCENARIO_DIR).definition
    # The injects are unchanged since 1.1.0; TEST-I128-001 pins the 1.2.0 version and content.
    assert definition.game.primary_arguments_per_actor == 1
    assert {
        i.id: (i.turn, i.visibility, list(i.visible_to)) for i in definition.injects
    } == EXPECTED
    # D-095: injects are unconditional, so none may presuppose how play went on the cable.
    assumption = re.compile(
        r"(?i)cable (is|was|has been) (repaired|restored|broken)|(repaired|restored) cable"
        r"|still (broken|failed|down)|\bagain\b|\bif the\b|\bunless\b"
    )
    for inject in definition.injects:
        assert not assumption.search(inject.description), inject.id


# --- TEST-T083-002: offline play releases each inject at its turn --------------------------


def test_t083_002_offline_play_releases_every_inject_and_completes(db_engine: Engine) -> None:
    _, creation, seen = _play(db_engine)
    with session_scope(db_engine) as session:
        assert session.get(Game, creation.game_id).status is GameStatus.complete
        released = {
            e.source_inject_id: (e.created_turn, e.visibility, list(e.visible_to))
            for e in session.exec(
                select(Effect).where(
                    Effect.game_id == creation.game_id,
                    Effect.source_inject_id != None,  # noqa: E711
                )
            ).all()
        }
    assert released == EXPECTED
    # Turn-4 adjudications see the storm; turn-2 adjudications see no inject yet.
    turn4 = [ctx for ctx in seen.adjudications if ctx.turn == 4]
    assert turn4 and all(
        any("winter storm" in e["description"] for e in ctx.relevant_effects) for ctx in turn4
    )
    assert not any(
        "harbour" in e["description"] for ctx in seen.adjudications if ctx.turn == 2
        for e in ctx.relevant_effects
    )


# --- TEST-T083-003: the documented run command selects the intended conditions -------------


def _readme_command() -> list[str]:
    readme = (SCENARIO_DIR / "README.md").read_text(encoding="utf-8")
    block = re.search(r"```bash\n(uv run amaw run skaro-crisis.*?)```", readme, re.S)
    assert block, "README lacks the recommended run command"
    return shlex.split(block.group(1).replace("\\\n", " "))


def test_t083_003_readme_command_parses_to_the_cheaper_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = _readme_command()
    assert command[:4] == ["uv", "run", "amaw", "run"]
    args = command[3:]
    captured = capture_run_cli(tmp_path, monkeypatch)
    # The README runs the scenario by name, which resolves from the checkout root (D-115).
    monkeypatch.chdir(SCENARIO_DIR.parents[1])
    result = CliRunner().invoke(
        amaw_cli.app, [*args, "--player-model", "p/m", "--umpire-model", "u/m", "--seed", "1"]
    )
    assert result.exit_code == 0, result.output
    runner = captured["runner_options"]
    assert (runner["diplomacy_mode"], runner["diplomacy_rounds_per_turn"]) == ("bounded_rounds", 3)
    assert (runner["con_mode"], runner["con_challengers_per_argument"]) == ("sampled_sealed", 2)
    assert (runner["con_reply_mode"], runner["reply_max_points"]) == ("visible_reply", 2)
    assert runner["consequence_mode"] == "bounded_effects_v1"
    assert runner["run_configuration"]["resume"]["consequence"]["model"] == "u/m"  # D-115 default
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"  # D-114 default
