"""I-156 acceptance: ``umpire-v1.7``, v1.6 plus a branch audit (D-112, agents.md §21/§22/§31).

v1.7 is a new immutable prompt file: v1.6 with one added "Branch audit" section and nothing else.
It is rules-aware, keeps v1.6's Effect bound and schema, and may be pinned for a rule-free game.
Since D-114 it is also every game's default. v1.6 stays byte-identical, so every run sealed under it keeps its hash.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from agents.model_client import OpenRouterClient
from agents.umpire import (
    RULES_AWARE_UMPIRE_PROMPT_VERSIONS,
    UMPIRE_PROMPT_VERSIONS,
    AIUmpire,
    load_umpire_prompt,
)
from amaw_cli.conditions import _umpire_prompt_version
from app.config import Settings
from app.umpire import AdjudicationContext
from tests.api_helpers import REPO_ROOT

V16, V17 = "umpire-v1.6", "umpire-v1.7"
# The hash sealed for umpire-v1.6 in retained run manifests (tests/fixtures/i120/bench_skaro.json).
V16_SEALED_SHA256 = "5bcc4ae5ef5b8758bd676d21b1808dd348e8014b2b9c2a09fb101d95a1dec4af"

_RULE_FREE = AdjudicationContext(
    scenario_summary="A regional standoff.",
    turn=2,
    turn_length="2 weeks",
    argument={"actor_id": "arkania", "action": "Act", "desired_effect": "Effect", "pros": ["P"]},
    cons=[],
    relevant_effects=[],
    relevant_hidden_information=[],
)
_WITH_RULES = AdjudicationContext(
    **{**_RULE_FREE.__dict__, "scenario_rules": [{"id": "r1", "text": "A rule."}]}
)


def _umpire(version: str, requests: list) -> AIUmpire:
    output = {
        "mode": "automatic_success",
        "probability": None,
        "rationale": "Self-binding; no other Actor's decision is needed.",
        "success_effects": ["Arkania has committed to the act."],
        "failure_effects": [],
        "success_supersedes_effect_ids": [],
        "failure_supersedes_effect_ids": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "gen-1",
            "model": "test/umpire",
            "choices": [{"message": {"role": "assistant", "content": json.dumps(output)},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIUmpire(client, model="test/umpire", prompt_version=version)


def test_i156_001_v17_is_a_new_file_extending_v16_only_with_the_branch_audit() -> None:
    docs = (REPO_ROOT / "docs/prompts/umpire-v1.7.md").read_text(encoding="utf-8")
    v16 = (REPO_ROOT / "docs/prompts/umpire-v1.6.md").read_bytes()

    assert V17 in UMPIRE_PROMPT_VERSIONS and V17 in RULES_AWARE_UMPIRE_PROMPT_VERSIONS
    assert load_umpire_prompt(version=V17) == docs
    assert (REPO_ROOT / "amaw_resources/prompts/umpire-v1.7.md").read_text(encoding="utf-8") == docs
    assert hashlib.sha256(v16).hexdigest() == V16_SEALED_SHA256
    assert load_umpire_prompt(version=V16).encode() == v16

    start = docs.index("## Branch audit")
    end = docs.index("## Modes and probability")
    stripped = docs[:start] + docs[end:]
    assert stripped.replace("Prompt v1.7", "Prompt v1.6", 1) == v16.decode()
    section = docs[start:end]
    assert "no other Actor made a new discretionary decision or commitment" in section
    assert "every selectable branch" in section
    assert "at least one Effect" in section


def test_i156_001_v17_keeps_the_v16_bound_and_schema_identity() -> None:
    requests: list = []
    umpire = _umpire(V17, requests)
    umpire.adjudicate(_WITH_RULES)
    schema = requests[0]["response_format"]["json_schema"]["schema"]["properties"]

    assert schema["success_effects"]["maxItems"] == 3
    assert schema["failure_effects"]["maxItems"] == 3
    configuration = umpire.provenance_configuration()
    assert configuration["prompt_version"] == V17
    assert configuration["prompt_hash"] != V16_SEALED_SHA256


def test_i156_001_v17_is_pinnable_for_rule_free_games_and_is_the_default() -> None:
    # D-112 made v1.7 selectable only; D-114 then made it every game's default (TEST-I159-001).
    settings = Settings(_env_file=None)
    requests: list = []

    assert _umpire(V17, requests).adjudicate_recorded(_RULE_FREE).failure is None
    assert _umpire(V17, requests).adjudicate_recorded(_WITH_RULES).failure is None
    for rules in (False, True):
        for reply in ("sealed_cons", "visible_reply"):
            pinned = _umpire_prompt_version(
                settings, explicit=V17, con_reply_mode=reply, scenario_declares_rules=rules
            )
            default = _umpire_prompt_version(
                settings, explicit=None, con_reply_mode=reply, scenario_declares_rules=rules
            )
            assert pinned == V17 and default == V17


@pytest.mark.llm
def test_i156_002_a_v2_case_is_adjudicated_under_v17_end_to_end(tmp_path: Path) -> None:
    """Provider smoke: one held-out corpus v2 case, once, under v1.7 and the shared condition.

    Needs ``OPENROUTER_API_KEY``. The comparison itself is ``scripts/run_adjudication_v2.py``.
    """
    import yaml

    from benchmark.adjudication_corpus import CORPUS_V2_DIR, load_corpus, read_calls, run_corpus

    # The corpora are held back from the public repository until the AI 2027 authors agree.
    if not CORPUS_V2_DIR.is_dir():
        pytest.skip("adjudication corpus v2 is not in this checkout")
    shared = yaml.safe_load((CORPUS_V2_DIR / "thresholds.yaml").read_text())["conditions"]["shared"]
    corpus = load_corpus(CORPUS_V2_DIR)

    def umpire_for(version: str) -> AIUmpire:
        return AIUmpire.for_openrouter(
            Settings(), model=shared["umpire_model"], prompt_version=version,
            timeout=shared["timeout_seconds"],
        )

    run_corpus(
        corpus, umpire_for, repetitions=1, condition="single_umpire_v1_7", out_dir=tmp_path,
        case_ids=["skaro-dc-authorised-deployment"], prompt_for=lambda case: V17,
    )
    [call] = read_calls(tmp_path)
    provenance = json.loads((tmp_path / "provenance.json").read_text())

    assert call["failure"] is None and call["attempts"]
    assert call["prompt_version"] == V17
    assert provenance["adjudicators"][V17]["model"] == shared["umpire_model"]
