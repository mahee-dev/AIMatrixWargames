"""The experimental game-loop condition a Game runs under (api.md §14, agents.md §19).

One frozen value carrying the declared, persisted conditions — Diplomacy (I-053), Con
opposition (I-055), Con reply (I-046) and Consequence Management (T-074, D-091) — and the one
place their mode/value pairing rules are
written (I-060). Every caller that used to restate the rules (CLI flag resolution, game creation,
resume-recipe validation, the autonomous coordinator) delegates here. The wire and persisted
shapes are unchanged: :meth:`GameLoopCondition.as_dict` is the flat six-key mapping the API body,
``Game`` columns and resume recipe carry; :meth:`GameLoopCondition.provenance` adds the three
policy versions the run manifest and each AgentRun record.

``consequence_mode`` (D-091) is deliberately *not* one of the six flat fields: its keys join
:meth:`~GameLoopCondition.as_dict` and :meth:`~GameLoopCondition.provenance` only when the mode
is not ``none``, so the provenance of a released-loop game stays byte-identical, and an absent
key means ``none`` for every legacy recipe and manifest.

A new game's condition is resolved by :func:`resolve_game_loop_condition`: the explicit
creation-time value, then the scenario's ``game.*`` default, then the released default (D-099).
Resume never re-resolves; it restores the sealed condition.

Scenario-specific checks that need more than the six fields (``sampled_sealed`` must be able to
seat its K challengers against the real opponent count) stay with game creation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

DIPLOMACY_MODE_OPEN = "open_diplomacy"
DIPLOMACY_MODE_BOUNDED = "bounded_rounds"
DIPLOMACY_MODES = (DIPLOMACY_MODE_OPEN, DIPLOMACY_MODE_BOUNDED)
DIPLOMACY_POLICY_VERSION = "diplomacy-policy-v1"

CON_MODE_OPEN = "open_cons"
CON_MODE_SAMPLED_SEALED = "sampled_sealed"
CON_MODES = (CON_MODE_OPEN, CON_MODE_SAMPLED_SEALED)
CON_POLICY_VERSION = "con-policy-v1"

CON_REPLY_MODE_SEALED = "sealed_cons"
CON_REPLY_MODE_VISIBLE = "visible_reply"
CON_REPLY_MODES = (CON_REPLY_MODE_SEALED, CON_REPLY_MODE_VISIBLE)
CON_REPLY_POLICY_VERSION = "con-reply-policy-v1"
CONSEQUENCE_MODE_NONE = "none"
CONSEQUENCE_MODE_BOUNDED = "bounded_effects_v1"
CONSEQUENCE_MODES = (CONSEQUENCE_MODE_NONE, CONSEQUENCE_MODE_BOUNDED)
CONSEQUENCE_POLICY_VERSION = "consequence-policy-v1"
# D-094 (I-109): a budget above 1 runs round-robin Argument rounds (state-machine §17/§20). The
# policy has no separate scenario field — ``primary_arguments_per_actor`` selects it — and is
# sealed by name only when it departs from the released single round.
ARGUMENT_SEQUENCING_ROUND_ROBIN = "round_robin_v1"
MAX_PRIMARY_ARGUMENTS_PER_ACTOR = 3

# api.md §14 / state-machine §18.1: a reply is 0..reply_max_points points, the bound is 1..3.
REPLY_MAX_POINTS_LIMIT = 3

GAME_LOOP_FIELDS = (
    "diplomacy_mode",
    "diplomacy_rounds_per_turn",
    "con_mode",
    "con_challengers_per_argument",
    "con_reply_mode",
    "reply_max_points",
)


# Every field of the condition: the six flat ones plus ``consequence_mode`` (D-091), for the
# callers that compare two conditions field by field.
CONDITION_FIELDS = (*GAME_LOOP_FIELDS, "consequence_mode")


class InvalidGameLoopCondition(ValueError):
    """The six fields do not form a legal condition (api.md §14 pairing rules)."""


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


@dataclass(frozen=True)
class GameLoopCondition:
    """The declared Diplomacy / Con / Con-reply condition. Defaults are the released behaviour."""

    diplomacy_mode: str = DIPLOMACY_MODE_OPEN
    diplomacy_rounds_per_turn: int | None = None
    con_mode: str = CON_MODE_OPEN
    con_challengers_per_argument: int | None = None
    con_reply_mode: str = CON_REPLY_MODE_SEALED
    reply_max_points: int | None = None
    # D-091 / state-machine §21: Option A (``none``, released) or Option B (``bounded_effects_v1``).
    consequence_mode: str = CONSEQUENCE_MODE_NONE

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> GameLoopCondition:
        """Read the six fields from any flat mapping (API body, recipe ``coordinator``, manifest
        ``game_loop``). Absent keys mean the released default — a pre-condition recipe carries none
        of them (additive legacy compatibility, testing.md §26/§27/§29). Other keys are ignored."""
        return cls(
            diplomacy_mode=mapping.get("diplomacy_mode", DIPLOMACY_MODE_OPEN),
            diplomacy_rounds_per_turn=mapping.get("diplomacy_rounds_per_turn"),
            con_mode=mapping.get("con_mode", CON_MODE_OPEN),
            con_challengers_per_argument=mapping.get("con_challengers_per_argument"),
            con_reply_mode=mapping.get("con_reply_mode", CON_REPLY_MODE_SEALED),
            reply_max_points=mapping.get("reply_max_points"),
            consequence_mode=mapping.get("consequence_mode", CONSEQUENCE_MODE_NONE),
        )

    def validate(self) -> None:
        """Enforce the api.md §14 pairing rules; raise :class:`InvalidGameLoopCondition`."""
        if self.diplomacy_mode not in DIPLOMACY_MODES:
            raise InvalidGameLoopCondition(
                f"diplomacy_mode must be one of {', '.join(DIPLOMACY_MODES)}; "
                f"got {self.diplomacy_mode!r}"
            )
        if self.diplomacy_mode == DIPLOMACY_MODE_BOUNDED:
            if not _positive_int(self.diplomacy_rounds_per_turn):
                raise InvalidGameLoopCondition(
                    "bounded_rounds requires diplomacy_rounds_per_turn to be a positive integer"
                )
        elif self.diplomacy_rounds_per_turn is not None:
            raise InvalidGameLoopCondition(
                "diplomacy_rounds_per_turn is only valid with diplomacy_mode='bounded_rounds'"
            )

        if self.con_mode not in CON_MODES:
            raise InvalidGameLoopCondition(
                f"con_mode must be one of {', '.join(CON_MODES)}; got {self.con_mode!r}"
            )
        if self.con_mode == CON_MODE_SAMPLED_SEALED:
            if not _positive_int(self.con_challengers_per_argument):
                raise InvalidGameLoopCondition(
                    "sampled_sealed requires con_challengers_per_argument to be a positive integer"
                )
        elif self.con_challengers_per_argument is not None:
            raise InvalidGameLoopCondition(
                "con_challengers_per_argument is only valid with con_mode='sampled_sealed'"
            )

        if self.con_reply_mode not in CON_REPLY_MODES:
            raise InvalidGameLoopCondition(
                f"con_reply_mode must be one of {', '.join(CON_REPLY_MODES)}; "
                f"got {self.con_reply_mode!r}"
            )
        if self.con_reply_mode == CON_REPLY_MODE_VISIBLE:
            if (
                self.reply_max_points is None
                or not _positive_int(self.reply_max_points)
                or self.reply_max_points > REPLY_MAX_POINTS_LIMIT
            ):
                raise InvalidGameLoopCondition(
                    "visible_reply requires reply_max_points to be an integer from 1 to "
                    f"{REPLY_MAX_POINTS_LIMIT}"
                )
        elif self.reply_max_points is not None:
            raise InvalidGameLoopCondition(
                "reply_max_points is only valid with con_reply_mode='visible_reply'"
            )

        if self.consequence_mode not in CONSEQUENCE_MODES:
            raise InvalidGameLoopCondition(
                f"consequence_mode must be one of {', '.join(CONSEQUENCE_MODES)}; "
                f"got {self.consequence_mode!r}"
            )

    def as_dict(self) -> dict[str, Any]:
        """The flat six-key mapping (API body, ``Game`` columns, recipe ``coordinator``), plus
        ``consequence_mode`` only when it is not ``none`` (D-091)."""
        mapping = {name: getattr(self, name) for name in GAME_LOOP_FIELDS}
        if self.consequence_mode != CONSEQUENCE_MODE_NONE:
            mapping["consequence_mode"] = self.consequence_mode
        return mapping

    def provenance(self) -> dict[str, Any]:
        """The manifest / AgentRun ``game_loop`` record: policy versions beside the fields."""
        return {
            "diplomacy_policy_version": DIPLOMACY_POLICY_VERSION,
            "diplomacy_mode": self.diplomacy_mode,
            "diplomacy_rounds_per_turn": self.diplomacy_rounds_per_turn,
            "con_policy_version": CON_POLICY_VERSION,
            "con_mode": self.con_mode,
            "con_challengers_per_argument": self.con_challengers_per_argument,
            "con_reply_policy_version": CON_REPLY_POLICY_VERSION,
            "con_reply_mode": self.con_reply_mode,
            "reply_max_points": self.reply_max_points,
            **(
                {
                    "consequence_policy_version": CONSEQUENCE_POLICY_VERSION,
                    "consequence_mode": self.consequence_mode,
                }
                if self.consequence_mode != CONSEQUENCE_MODE_NONE
                else {}
            ),
        }


@dataclass(frozen=True)
class GameLoopConditionLayer:
    """The condition values one source declares; ``None`` means this source declares nothing.

    A new game's condition has three sources, highest first: the explicit creation-time value
    (CLI flag or set ``AMAW_*`` value, API body field), the scenario's ``game.*`` default, and the
    released default (:class:`GameLoopCondition`'s own defaults). Omission is ``None``, never
    falsiness, so an explicit value equal to the released default still wins (I-118, D-099).
    """

    diplomacy_mode: str | None = None
    diplomacy_rounds_per_turn: int | None = None
    con_mode: str | None = None
    con_challengers_per_argument: int | None = None
    con_reply_mode: str | None = None
    reply_max_points: int | None = None
    consequence_mode: str | None = None


# Each mode and the count that is only meaningful with one of its values (api.md §14).
_PAIRED_COUNTS = (
    ("diplomacy_mode", "diplomacy_rounds_per_turn"),
    ("con_mode", "con_challengers_per_argument"),
    ("con_reply_mode", "reply_max_points"),
)


def resolve_game_loop_condition(
    explicit: GameLoopConditionLayer,
    scenario: GameLoopConditionLayer = GameLoopConditionLayer(),
) -> GameLoopCondition:
    """Resolve a new game's condition: explicit value, then scenario default, then released.

    Every entry point that creates or seals a new game — game creation behind the admin API,
    ``amaw run``, ``amaw benchmark`` — resolves through this one function, so the condition the
    runner seals is the one the server persists (I-118, D-099). Each field takes the first
    source that declares it. A count follows its mode: when the count is not explicit it is
    inherited only from a source whose mode equals the resolved mode, so an explicit
    ``open_diplomacy`` over a scenario's ``bounded_rounds`` never carries the scenario's bound.
    The result is not validated here; callers apply :meth:`GameLoopCondition.validate` as before.
    Resume never calls this: it restores the sealed, already-resolved condition.
    """
    released = GameLoopCondition()
    resolved: dict[str, Any] = {}
    for mode_field, count_field in _PAIRED_COUNTS:
        mode = next(
            (value for value in (getattr(explicit, mode_field), getattr(scenario, mode_field))
             if value is not None),
            getattr(released, mode_field),
        )
        count = getattr(explicit, count_field)
        if count is None and getattr(scenario, mode_field) == mode:
            count = getattr(scenario, count_field)
        resolved[mode_field] = mode
        resolved[count_field] = count
    resolved["consequence_mode"] = next(
        (value for value in (explicit.consequence_mode, scenario.consequence_mode)
         if value is not None),
        released.consequence_mode,
    )
    return GameLoopCondition(**resolved)
