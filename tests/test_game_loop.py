"""I-060 — the one ``GameLoopCondition`` value object (api.md §14, agents.md §19).

The three mode/value pairing rules used to be written three times (CLI, scenario creation, resume
validation). These tests pin the single implementation every caller now delegates to. Behaviour is
unchanged: the flat six-key mapping is still what the API body, ``Game`` columns, manifest
``game_loop`` and resume recipe carry.
"""

from __future__ import annotations

import pytest

from app.game_loop import (
    CON_POLICY_VERSION,
    CON_REPLY_POLICY_VERSION,
    DIPLOMACY_POLICY_VERSION,
    GameLoopCondition,
    InvalidGameLoopCondition,
)


def test_default_condition_is_the_released_open_open_sealed_triple() -> None:
    condition = GameLoopCondition()
    condition.validate()
    assert condition.as_dict() == {
        "diplomacy_mode": "open_diplomacy",
        "diplomacy_rounds_per_turn": None,
        "con_mode": "open_cons",
        "con_challengers_per_argument": None,
        "con_reply_mode": "sealed_cons",
        "reply_max_points": None,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"diplomacy_mode": "bounded_rounds"},  # bounded needs a positive round limit
        {"diplomacy_mode": "bounded_rounds", "diplomacy_rounds_per_turn": 0},
        {"diplomacy_mode": "bounded_rounds", "diplomacy_rounds_per_turn": True},
        {"diplomacy_mode": "open_diplomacy", "diplomacy_rounds_per_turn": 2},  # forbidden
        {"diplomacy_mode": "timed"},
        {"con_mode": "sampled_sealed"},
        {"con_mode": "sampled_sealed", "con_challengers_per_argument": 0},
        {"con_mode": "sampled_sealed", "con_challengers_per_argument": True},
        {"con_mode": "open_cons", "con_challengers_per_argument": 1},
        {"con_mode": "first_come"},
        {"con_reply_mode": "visible_reply"},
        {"con_reply_mode": "visible_reply", "reply_max_points": 0},
        {"con_reply_mode": "visible_reply", "reply_max_points": 4},
        {"con_reply_mode": "visible_reply", "reply_max_points": True},
        {"con_reply_mode": "sealed_cons", "reply_max_points": 1},
        {"con_reply_mode": "public"},
    ],
)
def test_pairing_rules_reject_each_invalid_combination(fields: dict) -> None:
    with pytest.raises(InvalidGameLoopCondition):
        GameLoopCondition(**fields).validate()


def test_every_experimental_mode_validates_with_its_bound() -> None:
    GameLoopCondition(
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=1,
        con_mode="sampled_sealed",
        con_challengers_per_argument=1,
        con_reply_mode="visible_reply",
        reply_max_points=3,
    ).validate()


def test_from_mapping_applies_legacy_defaults_and_ignores_other_keys() -> None:
    # A pre-I-053/I-055/I-046 recipe carries none of the keys and means the released defaults.
    assert GameLoopCondition.from_mapping({"max_cycles": 7}) == GameLoopCondition()
    partial = GameLoopCondition.from_mapping(
        {"con_mode": "sampled_sealed", "con_challengers_per_argument": 2, "unrelated": 1}
    )
    assert partial == GameLoopCondition(con_mode="sampled_sealed", con_challengers_per_argument=2)


def test_provenance_carries_the_three_policy_versions_beside_the_six_fields() -> None:
    condition = GameLoopCondition(con_reply_mode="visible_reply", reply_max_points=2)
    assert condition.provenance() == {
        "diplomacy_policy_version": DIPLOMACY_POLICY_VERSION,
        "diplomacy_mode": "open_diplomacy",
        "diplomacy_rounds_per_turn": None,
        "con_policy_version": CON_POLICY_VERSION,
        "con_mode": "open_cons",
        "con_challengers_per_argument": None,
        "con_reply_policy_version": CON_REPLY_POLICY_VERSION,
        "con_reply_mode": "visible_reply",
        "reply_max_points": 2,
    }
    assert (DIPLOMACY_POLICY_VERSION, CON_POLICY_VERSION, CON_REPLY_POLICY_VERSION) == (
        "diplomacy-policy-v1", "con-policy-v1", "con-reply-policy-v1",
    )
