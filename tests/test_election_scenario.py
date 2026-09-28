"""I-037/I-041 regression checks for the Swedish v5 scenario package.

v5 is superseded by ``scenarios/swedish-government`` and lives under ``scenarios/archive/``;
the checks stay because I-037/I-041 were fixed against this exact package.
"""

from pathlib import Path

from app import scenarios


SCENARIO_DIR = (
    Path(__file__).resolve().parents[1]
    / "scenarios"
    / "archive"
    / "svensk-regeringsbildning-2026-v5"
)


def test_i037_swedish_v5_is_concrete_and_gives_negotiation_room() -> None:
    loaded = scenarios.load_scenario(SCENARIO_DIR)
    assert loaded.scenario.id == "svensk-regeringsbildning-2026"
    assert loaded.scenario.version == 5
    assert loaded.game.max_turns == 4
    assert loaded.game.in_world_turn_length == "48 hours"

    effects = "\n".join(effect.description for effect in loaded.starting_effects).lower()
    assert "fyrpartial" in effects or "flerpartspaket" in effects
    assert "härledd" in effects
    assert "trovärdig budgetväg" in effects
    assert "175" in effects and "determinist" in effects
    for marker in ("todo", "tbd", "fixme", "fyll_i", "<placeholder>"):
        assert marker not in effects


def test_i037_swedish_v5_has_decision_relevant_private_leverage() -> None:
    loaded = scenarios.load_scenario(SCENARIO_DIR)
    leverage = {
        actor_id: " ".join(actor.private_information).lower()
        for actor_id, actor in loaded.actors.items()
    }
    assert "skol" in leverage["l"] and "innan" in leverage["l"]
    assert "tolerans" in leverage["c"] and "regeringsdeltagande" in leverage["c"]
    assert "turn 3" in leverage["kd"]


def test_i041_swedish_v5_has_the_current_349_seat_resource() -> None:
    current = scenarios.load_scenario(SCENARIO_DIR)
    assert current.scenario.id == "svensk-regeringsbildning-2026"
    assert current.scenario.version == 5

    rows = (SCENARIO_DIR / "resources" / "valresultat_2026.csv").read_text(
        encoding="utf-8"
    ).splitlines()
    seats = {party: int(count) for party, count in (row.split(",") for row in rows[1:])}
    assert seats == {
        "S": 100,
        "M": 70,
        "SD": 62,
        "C": 25,
        "V": 29,
        "KD": 22,
        "MP": 22,
        "L": 19,
    }
    assert sum(seats.values()) == 349
