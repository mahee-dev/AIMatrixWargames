"""I-061: one composition path for ``amaw run`` / ``amaw benchmark``; one resume-recipe builder.

Behaviour-preserving cleanup (review F1/F3/F5). These tests pin the two things the cleanup must
not break: the explicit override-refusal set cannot drift from ``run``'s condition flags, and a
sealed ``autonomous-resume-v1`` recipe round-trips byte-for-byte through the one builder.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing

import typer

import amaw_cli

# ``run`` parameters that are *not* part of the experimental condition: identity, runtime
# presentation and the analysis-plan classification (which resume reconstructs separately).
_NON_CONDITION_PARAMETERS = {
    "ctx",
    "scenario",
    "resume_game",
    "log_level",
    "log_file",
    "stream_game_details",
    "watch",  # #24: the terminal viewer is presentation; it never changes the condition
    "export_path",
    "analysis_plan",
    "run_class",
}


def _flag_name(parameter: inspect.Parameter, annotation: object) -> str:
    """The ``--flag`` a ``run`` parameter is exposed as, from its Typer option metadata."""
    option = parameter.default
    if not isinstance(option, typer.models.OptionInfo):
        option = next(
            meta for meta in typing.get_args(annotation)[1:]
            if isinstance(meta, typer.models.OptionInfo)
        )
    # In ``Annotated`` form Typer receives the flag as the first positional (``default``) and
    # reinterprets it later; in the classic form it is already in ``param_decls``.
    declared = option.param_decls or (option.default,)
    return declared[0].split("/")[0].removeprefix("--")


def test_i061_001_every_condition_opt_of_run_is_in_the_override_refusal_set() -> None:
    """F3: the named set stays explicit, but it cannot silently miss a condition flag."""
    parameters = inspect.signature(amaw_cli.run).parameters
    hints = typing.get_type_hints(amaw_cli.run, include_extras=True)
    condition_flags = {
        _flag_name(parameter, hints[name])
        for name, parameter in parameters.items()
        if name not in _NON_CONDITION_PARAMETERS
    }
    assert set(amaw_cli.RESUME_OVERRIDE_FLAGS) == condition_flags
    assert len(amaw_cli.RESUME_OVERRIDE_FLAGS) == len(set(amaw_cli.RESUME_OVERRIDE_FLAGS))


def _full_recipe() -> dict:
    from agents.guardrails import GuardrailConfig

    return {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "provider/a", "belvar": "provider/b"},
            "temperature": 0.2,
            "max_tokens": 4321,
            "timeout": 222.0,
            "reasoning_effort": "low",
            "activation_mode": "single_shot",
            "research_mode": "batched-v1",
            "search_contract": "search-v2",
            "research_history_mode": "compact-success-v1",
            "tool_surface": "broad",  # I-092
            "prompt_version": "player-v1.11",
            "guardrails": dataclasses.asdict(GuardrailConfig(max_research_tool_calls=3)),
        },
        "umpire": {
            "model": "provider/umpire",
            "temperature": 0.1,
            "max_tokens": 8765,
            "timeout": 222.0,
            "reasoning_effort": "medium",
            "prompt_version": "umpire-v1.3",
        },
        "inference_profile": {"schema": "inference-profile-v1", "name": "uniform", "phases": {}, "umpire": {}},
        "coordinator": {
            "max_cycles": 777,
            "cons_concurrency": 2,
            "diplomacy_concurrency": 3,
            "diplomacy_mode": "bounded_rounds",
            "diplomacy_rounds_per_turn": 3,
            "con_mode": "sampled_sealed",
            "con_challengers_per_argument": 2,
            "con_reply_mode": "sealed_cons",
            "reply_max_points": None,
        },
    }


def test_i061_002_sealed_recipe_round_trips_through_the_one_builder() -> None:
    """F3: parse → PlayerConfig → build gives back the identical recipe (byte-identical manifests)."""
    from agents.autonomous import build_resume_recipe
    from agents.player import PlayerConfig
    from app.game_loop import GameLoopCondition

    recipe = _full_recipe()
    config = PlayerConfig.from_resume_recipe(recipe, model="provider/a")

    assert config.model == "provider/a"
    assert config.timeout == 222.0
    assert config.research_history_mode == "compact-success-v1"
    assert config.search_contract == "search-v2"
    assert config.guardrails.max_research_tool_calls == 3
    assert config.inference_profile.name == "uniform"

    rebuilt = build_resume_recipe(
        config,
        assignments=recipe["player"]["assignments"],
        umpire=recipe["umpire"],
        max_cycles=777,
        cons_concurrency=2,
        diplomacy_concurrency=3,
        condition=GameLoopCondition.from_mapping(recipe["coordinator"]),
    )
    assert rebuilt == recipe


def test_i061_003_legacy_recipe_resolves_to_the_documented_defaults() -> None:
    """F3: a pre-condition recipe (no research keys, no profile, legacy guardrails) still resolves."""
    from agents.guardrails import GuardrailConfig
    from agents.player import PlayerConfig

    legacy_guardrails = dataclasses.asdict(GuardrailConfig())
    legacy_guardrails.pop("max_research_tool_calls")
    recipe = {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "provider/original"},
            "temperature": 0.2,
            "max_tokens": 4321,
            "timeout": 222.0,
            "reasoning_effort": "low",
            "activation_mode": "single_shot",
            "prompt_version": "player-v1.6",
            "guardrails": legacy_guardrails,
        },
        "umpire": {"model": "provider/original-umpire", "temperature": 0.1,
                   "max_tokens": 8765, "timeout": 222.0, "reasoning_effort": "medium"},
        "coordinator": {"max_cycles": 777, "cons_concurrency": 2, "diplomacy_concurrency": 3},
    }
    config = PlayerConfig.from_resume_recipe(recipe, model="provider/original")
    assert config.research_mode == "sequential-v1"
    assert config.search_contract == "search-v1"
    assert config.research_history_mode == "full-v1"
    assert config.prompt_version == "player-v1.6"
    assert config.con_reply_mode == "sealed_cons"
    assert config.guardrails.max_research_tool_calls == GuardrailConfig().max_research_tool_calls
    assert config.inference_profile.name == "uniform"
