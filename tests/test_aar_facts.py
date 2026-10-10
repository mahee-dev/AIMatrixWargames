"""Screen 1's model credit and screen 5 come from counted export data only (#18)."""

from __future__ import annotations

from aar.facts import model_name, run_facts
from tests.aar_helpers import minimal_export


def _run(actor: str | None, role: str, model: str, calls: int, tokens: tuple[int, int],
         cost: float | None, costed: int) -> dict:
    return {"actor_id": actor, "role": role, "model_requested": model, "model_returned": model,
            "model_calls": calls, "input_tokens": tokens[0], "output_tokens": tokens[1],
            "cost_usd": cost, "costed_model_calls": costed}


def _export() -> dict:
    return minimal_export(
        model_assignments={"a": "deepseek/deepseek-v4-flash-0731", "b": "anthropic/claude-opus-5.5"},
        run_manifest={"umpire": {"model": "openai/gpt-6", "prompt_version": "umpire-v1.7"},
                      "player": {"prompt_version": "player-v1.11"}, "consequence": None},
        agent_runs=[
            _run("a", "player", "deepseek/deepseek-v4-flash-0731", 3, (1000, 200), 0.01, 3),
            _run("a", "player", "deepseek/deepseek-v4-flash-0731", 2, (500, 100), None, 0),
            _run("b", "player", "anthropic/claude-opus-5.5", 4, (2000, 400), 0.20, 4),
            _run(None, "umpire", "openai/gpt-6", 1, (300, 50), 0.05, 1),
        ],
        run_summary={"estimated_cost_usd": 0.26, "input_tokens": 3800, "output_tokens": 750,
                     "reasoning_tokens": 120, "total_runtime_ms": 5_400_000,
                     "player_model_calls": 9, "umpire_calls": 1,
                     "cost_coverage": {"calls_with_cost": 8, "total_calls": 10, "complete": False}},
        messages=[
            {"id": "m1", "turn": 1, "sender_actor_id": "a", "recipients": ["b"], "public": False, "text": "x"},
            {"id": "m2", "turn": 1, "sender_actor_id": "a", "recipients": [], "public": True, "text": "y"},
        ],
        adjudications=[
            {"argument_id": "x1", "mode": "probabilistic", "probability": 0.5, "roll": 0.2, "success": True},
            {"argument_id": "x2", "mode": "automatic_failure", "probability": None, "roll": None, "success": False},
        ],
    )


def test_model_names_are_readable() -> None:
    assert model_name("deepseek/deepseek-v4-flash-0731") == "DeepSeek V4 Flash"
    assert model_name("openai/gpt-6") == "GPT 6"
    assert model_name("anthropic/claude-opus-5.5") == "Claude Opus 5.5"


def test_per_actor_rows_sum_the_agent_runs() -> None:
    facts = run_facts(_export())
    alpha = facts.row("a")
    assert (alpha.model, alpha.calls, alpha.tokens, alpha.messages, alpha.secret) == (
        "DeepSeek V4 Flash", 5, 1800, 2, 1)
    assert alpha.cost == 0.01
    umpire = facts.row("umpire")
    assert (umpire.model, umpire.calls, umpire.prompt_version) == ("GPT 6", 1, "umpire-v1.7")


def test_totals_and_cost_coverage() -> None:
    facts = run_facts(_export())
    assert facts.cost == 0.26 and facts.cost_partial is True
    assert (facts.costed_calls, facts.total_calls) == (8, 10)
    assert facts.tokens == 4550 and facts.reasoning_tokens == 120
    assert facts.runtime_hours == 1.5 and facts.model_calls == 10
    assert (facts.granted, facts.adjudicated, facts.rolled) == (1, 2, 1)
    assert (facts.messages, facts.secret_messages) == (2, 1)


def test_model_credit_lists_mixed_models_per_actor() -> None:
    assert run_facts(_export()).model_credit({"a": "Alpha", "b": "Beta"}, "sides") == (
        "Alpha: DeepSeek V4 Flash · Beta: Claude Opus 5.5")


def test_model_credit_for_one_model() -> None:
    export = _export()
    export["model_assignments"] = {"a": "x/model-a", "b": "x/model-a"}
    assert run_facts(export).model_credit({"a": "A", "b": "B"}, "sides") == "Model A played both sides"


def test_old_exports_without_a_manifest_still_work() -> None:
    export = minimal_export()
    facts = run_facts(export)
    assert facts.cost is None and facts.row("a").model == "unknown"
    assert facts.model_credit({"a": "A", "b": "B"}, "Actors") == ""
