"""An automatic failure that establishes nothing is retried through another provider (#45).

Twice, one downstream provider answered an adjudication with a short ``automatic_failure``
claiming the Argument was empty, although the Argument on record was complete; across 447 earlier
rulings no genuine automatic failure established nothing on either branch. The Umpire client
treats that shape as unusable output and retries it through a different provider within the D-063
attempt budget. §25 does not forbid an empty failure, so the last attempt's answer is accepted:
the check diversifies routing and never pauses a game on its own.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agents.model_client import OpenRouterClient
from agents.umpire import UMPIRE_MAX_ATTEMPTS, UMPIRE_PROMPT_VERSIONS, AIUmpire
from app.umpire import AdjudicationContext

_CTX = AdjudicationContext(
    scenario_summary="A procurement dispute.",
    turn=3,
    turn_length="12 hours",
    argument={"actor_id": "aster", "action": "File the reconciliation evidence.",
              "desired_effect": "The evidence is on file.", "pros": ["It is required."]},
    cons=[],
    relevant_effects=[],
    relevant_hidden_information=[],
    scenario_rules=[{"id": "r1", "text": "A rule."}],
)

EMPTY_FAILURE = {
    "mode": "automatic_failure", "probability": None,
    "rationale": "No Matrix Argument was provided.",
    "success_effects": [], "failure_effects": [],
    "success_supersedes_effect_ids": [], "failure_supersedes_effect_ids": [],
}
FAILURE = {**EMPTY_FAILURE, "rationale": "Outside Aster's authority.",
           "failure_effects": ["Aster's filing is refused for lack of authority."]}
EMPTY_SUCCESS = {**EMPTY_FAILURE, "mode": "automatic_success",
                 "rationale": "A self-binding reaffirmation; nothing new to record."}


def _umpire(outputs: list[tuple[str, dict]], requests: list, version: str = "umpire-v1.7"
            ) -> AIUmpire:
    queue = list(outputs)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        provider, output = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(200, json={
            "id": "gen-1", "model": "test/umpire", "provider": provider,
            "choices": [{"message": {"role": "assistant", "content": json.dumps(output)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIUmpire(client, model="test/umpire", prompt_version=version)


def _ignored(request: dict) -> list[str]:
    return list((request.get("provider") or {}).get("ignore") or [])


def test_an_empty_automatic_failure_is_retried_through_another_provider() -> None:
    requests: list = []
    result = _umpire([("OpenInference", EMPTY_FAILURE), ("Baidu", FAILURE)], requests
                     ).adjudicate_recorded(_CTX)

    assert result.proposal is not None
    assert result.proposal.failure_effects == FAILURE["failure_effects"]
    assert len(requests) == 2
    assert _ignored(requests[0]) == [] and _ignored(requests[1]) == ["OpenInference"]
    assert "empty_automatic_failure" in (result.attempts[0]["failure_reason"] or "")
    assert result.failure is None


def test_a_persistent_empty_failure_is_accepted_on_the_last_attempt() -> None:
    """Every provider agreeing is accepted: an empty failure is legal, so no game pauses on it."""
    requests: list = []
    providers = ["OpenInference", "Baidu", "Reka", "Wafer"][:UMPIRE_MAX_ATTEMPTS]
    result = _umpire([(p, EMPTY_FAILURE) for p in providers], requests).adjudicate_recorded(_CTX)

    assert len(requests) == UMPIRE_MAX_ATTEMPTS
    assert result.proposal is not None and result.proposal.mode.value == "automatic_failure"
    assert result.proposal.failure_effects == [] and result.failure is None


def test_a_failure_that_establishes_something_is_accepted_at_once() -> None:
    requests: list = []
    result = _umpire([("OpenInference", FAILURE)], requests).adjudicate_recorded(_CTX)
    assert result.proposal is not None and len(requests) == 1


def test_an_empty_automatic_success_is_accepted_at_once() -> None:
    """A self-binding reaffirmation legitimately records nothing new (13 such rulings on record)."""
    requests: list = []
    result = _umpire([("OpenInference", EMPTY_SUCCESS)], requests).adjudicate_recorded(_CTX)
    assert result.proposal is not None and len(requests) == 1


@pytest.mark.parametrize("version", UMPIRE_PROMPT_VERSIONS)
def test_the_retry_applies_under_every_prompt_version(version: str) -> None:
    """The fault seen was a provider's answer, not a prompt's, so no version is exempt."""
    from dataclasses import replace

    requests: list = []
    rule_free = replace(_CTX, scenario_rules=[])  # v1.3 and v1.4 refuse a rules scenario
    _umpire([("OpenInference", EMPTY_FAILURE), ("Baidu", FAILURE)], requests, version
            ).adjudicate_recorded(rule_free)
    assert len(requests) == 2
