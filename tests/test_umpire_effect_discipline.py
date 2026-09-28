"""T-082 acceptance: ``umpire-v1.6`` Effect discipline (D-096, agents.md §21/§22/§31).

v1.6 is v1.5 plus an Effect-discipline section. Under v1.6 each Effect list holds at most 3
entries: the structured-output schema states it (``umpire-output-v2``) and the client retries a
longer list within the D-063 four-attempt budget. The server's own per-branch cap and every
earlier prompt condition are unchanged. v1.6 is the default for scenarios that declare rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from agents.model_client import OpenRouterClient
from agents.umpire import (
    UMPIRE_MAX_ATTEMPTS,
    UMPIRE_PROMPT_VERSION_EFFECTS,
    UMPIRE_PROMPT_VERSIONS,
    AIUmpire,
    load_umpire_prompt,
)
from app.umpire import MAX_EFFECTS_PER_BRANCH, AdjudicationContext
from tests.api_helpers import REPO_ROOT
from tests.cli_helpers import capture_run_cli, invoke_run
from tests.test_scenario_rules import _package

V15, V16 = "umpire-v1.5", "umpire-v1.6"

_CTX = AdjudicationContext(
    scenario_summary="A regional standoff.",
    turn=2,
    turn_length="2 weeks",
    argument={"actor_id": "arkania", "action": "Act", "desired_effect": "Effect", "pros": ["P"]},
    cons=[],
    relevant_effects=[],
    relevant_hidden_information=[],
    scenario_rules=[{"id": "r1", "text": "A rule."}],
)


def _output(n: int) -> dict:
    return {
        "mode": "probabilistic",
        "probability": 0.5,
        "rationale": "Weighed.",
        "success_effects": [f"Success change {i}." for i in range(n)],
        "failure_effects": ["Failure change."],
        "success_supersedes_effect_ids": [],
        "failure_supersedes_effect_ids": [],
    }


def _umpire(version: str, outputs: list[dict], requests: list | None = None) -> AIUmpire:
    queue = list(outputs)

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(json.loads(request.content))
        output = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(200, json={
            "id": "gen-1",
            "model": "test/umpire",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(output)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIUmpire(client, model="test/umpire", prompt_version=version)


# --- TEST-T082-001: the prompt is packaged and is v1.5 plus the discipline section --------


def test_t082_001_v16_is_packaged_and_extends_v15_only_with_effect_discipline() -> None:
    assert UMPIRE_PROMPT_VERSION_EFFECTS == V16 and V16 in UMPIRE_PROMPT_VERSIONS
    docs = (REPO_ROOT / "docs/prompts/umpire-v1.6.md").read_text(encoding="utf-8")
    assert load_umpire_prompt(version=V16) == docs
    assert (REPO_ROOT / "amaw_resources/prompts/umpire-v1.6.md").read_text(encoding="utf-8") == docs

    v15 = load_umpire_prompt(version=V15)
    start = docs.index("Effect discipline.")
    end = docs.index("Failure Effects describe")
    stripped = docs[:start] + docs[end:]
    assert stripped.replace("Prompt v1.6", "Prompt v1.5", 1) == v15
    section = docs[start:end]
    assert "At most 3 Effects in each branch" in section
    assert "supersede the earlier one" in section


# --- TEST-T082-002: the structured-output bound -------------------------------------------


def test_t082_002_v16_response_format_bounds_each_effect_list_at_three() -> None:
    requests: list = []
    _umpire(V16, [_output(2)], requests).adjudicate(_CTX)
    schema = requests[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert schema["success_effects"]["maxItems"] == 3
    assert schema["failure_effects"]["maxItems"] == 3

    requests.clear()
    _umpire(V15, [_output(2)], requests).adjudicate(_CTX)
    schema = requests[0]["response_format"]["json_schema"]["schema"]["properties"]
    assert schema["success_effects"]["maxItems"] == MAX_EFFECTS_PER_BRANCH == 10


# --- TEST-T082-003: an over-bound output is retried; v1.5 is unchanged ---------------------


def test_t082_003_v16_retries_an_over_bound_output_and_accepts_the_next() -> None:
    requests: list = []
    result = _umpire(V16, [_output(4), _output(3)], requests).adjudicate_recorded(_CTX)
    assert result.proposal is not None and len(result.proposal.success_effects) == 3
    assert len(requests) == 2
    assert "effect_bound_exceeded" in (result.attempts[0]["failure_reason"] or "")
    assert result.failure is None


def test_t082_003_v16_persistent_over_bound_output_makes_the_umpire_unavailable() -> None:
    requests: list = []
    result = _umpire(V16, [_output(4)], requests).adjudicate_recorded(_CTX)
    assert result.proposal is None
    assert "effect_bound_exceeded" in (result.failure or "")
    assert len(requests) == UMPIRE_MAX_ATTEMPTS


def test_t082_003_v15_still_accepts_four_effects() -> None:
    result = _umpire(V15, [_output(4)]).adjudicate_recorded(_CTX)
    assert result.proposal is not None and len(result.proposal.success_effects) == 4


# --- TEST-T082-004: tool schema identity --------------------------------------------------


def test_t082_004_tool_schema_version_follows_the_prompt() -> None:
    v16 = _umpire(V16, [_output(1)]).adjudicate_recorded(_CTX)
    v15 = _umpire(V15, [_output(1)]).adjudicate_recorded(_CTX)
    assert v16.attempts[0]["tool_schema_version"] == "umpire-output-v2"
    assert v16.attempts[0]["prompt_version"] == V16
    assert v15.attempts[0]["tool_schema_version"] == "umpire-output-v1"


# --- TEST-T082-005: CLI defaults ----------------------------------------------------------


def test_t082_005_cli_pins_v16_for_rules_and_rule_free_scenarios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D-114 moved every default to umpire-v1.7 (TEST-I159-001); v1.6 is reached by pinning it.
    captured = capture_run_cli(tmp_path, monkeypatch)
    ruled = _package(tmp_path, name="ruled")
    plain = _package(tmp_path, None, name="plain")

    assert invoke_run(ruled, "--umpire-prompt-version", V16).exit_code == 0
    assert captured["umpire_options"]["prompt_version"] == V16
    assert invoke_run(ruled, "--umpire-prompt-version", V15).exit_code == 0
    assert captured["umpire_options"]["prompt_version"] == V15
    result = invoke_run(ruled, "--umpire-prompt-version", V16, "--con-reply-mode", "visible_reply",
                        "--reply-max-points", "2")
    assert result.exit_code == 0, result.output
    assert captured["umpire_options"]["prompt_version"] == V16

    assert invoke_run(plain, "--umpire-prompt-version", V16).exit_code == 0
    assert captured["umpire_options"]["prompt_version"] == V16
