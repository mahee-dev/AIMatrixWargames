"""The narrator writes the story from the export with one model call, retries once with the
validation errors, and caches its answer; it never sees which models played (#18)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agents.model_client import ModelClientError, ModelConfig, ModelResult, ProviderErrorCategory
from aar.model import build_report
from aar.narrator import NARRATOR_PROMPT_VERSION, load_or_narrate, narrate, narrator_input
from tests.test_aar_story import _export, _story


class FakeClient:
    def __init__(self, *answers: dict[str, Any] | str) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[list[dict[str, Any]], ModelConfig]] = []

    def complete(self, messages: list[dict[str, Any]], *, config: ModelConfig, max_attempts: int = 2) -> ModelResult:
        self.calls.append((messages, config))
        answer = self.answers.pop(0)
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return ModelResult(completion=text, model_requested=config.model, model_returned=config.model + "-x",
                           provider="fake", usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.002},
                           latency_seconds=0.1)


def _narrated(client: FakeClient, export: dict[str, Any] | None = None):  # noqa: ANN202
    export = export or _export()
    return narrate(build_report(export), export, client, model="m/narrator")


def _model_story() -> dict[str, Any]:
    story = _story()
    story.pop("schema_version")
    return story


def test_a_valid_answer_is_accepted_first_time() -> None:
    client = FakeClient(_model_story())
    outcome = _narrated(client)
    assert outcome.story is not None and outcome.story.narrated
    assert outcome.errors == [] and len(client.calls) == 1
    config = client.calls[0][1]
    assert config.extra_body["response_format"]["json_schema"]["strict"] is True



def test_reasoning_has_its_own_budget_inside_the_output_cap() -> None:
    """Reasoning tokens count against ``max_tokens``; capping them leaves room for the story.

    With one 6000-token cap, deepseek-v4-flash-0731 spent all 6000 on reasoning and returned an
    empty answer; uncapped at 16000 it reasoned past the call deadline.
    """
    client = FakeClient(_model_story())
    _narrated(client)
    config = client.calls[0][1]
    reasoning = config.extra_body["reasoning"]["max_tokens"]
    assert config.max_tokens >= 16000
    assert config.max_tokens - reasoning >= 6000



class NoReasoningClient(FakeClient):
    """A model with no endpoint that accepts ``reasoning``: OpenRouter answers 404."""

    def complete(self, messages: list[dict[str, Any]], *, config: ModelConfig, max_attempts: int = 2) -> ModelResult:
        if "reasoning" in config.extra_body:
            self.calls.append((messages, config))
            raise ModelClientError("provider returned 404", category=ProviderErrorCategory.UNSUPPORTED_MODEL)
        return super().complete(messages, config=config, max_attempts=max_attempts)


def test_a_model_without_reasoning_support_is_asked_again_without_the_cap() -> None:
    """``require_parameters`` makes OpenRouter refuse ``reasoning`` for a non-reasoning model.

    The narrator then repeats the call once without it, so the reasoning cap never locks a model
    out of narration.
    """
    client = NoReasoningClient(_model_story())
    outcome = _narrated(client)
    assert outcome.story is not None and outcome.errors == []
    assert ["reasoning" in config.extra_body for _, config in client.calls] == [True, False]
    assert client.calls[1][1].max_tokens == client.calls[0][1].max_tokens


def test_a_404_with_no_reasoning_to_drop_still_fails() -> None:
    """Only the ``reasoning`` refusal is recovered; a genuinely unknown model still raises."""

    class Unknown(FakeClient):
        def complete(self, messages, *, config, max_attempts=2):  # noqa: ANN001, ANN202
            self.calls.append((messages, config))
            raise ModelClientError("provider returned 404", category=ProviderErrorCategory.UNSUPPORTED_MODEL)

    client = Unknown()
    with pytest.raises(ModelClientError):
        _narrated(client)
    assert ["reasoning" in config.extra_body for _, config in client.calls] == [True, False]



def test_an_empty_answer_is_asked_again_from_another_provider() -> None:
    """A provider that returns no content does not use up the retry meant for fixing the story.

    deepseek-v4-flash-0731 served by one provider ignored the reasoning cap and returned an empty
    answer; the narrator repeats the call with that provider excluded, as the Umpire does.
    """
    client = FakeClient("", _model_story())
    outcome = _narrated(client)
    assert outcome.story is not None and outcome.errors == []
    assert len(client.calls) == 2 and len(outcome.usage) == 2
    assert client.calls[1][1].extra_body["provider"]["ignore"] == ["fake"]
    assert client.calls[1][1].extra_body["provider"]["require_parameters"] is True


def test_an_empty_answer_leaves_the_fix_it_retry_in_place() -> None:
    client = FakeClient("", "not json", _model_story())
    outcome = _narrated(client)
    assert outcome.story is not None and len(client.calls) == 3
    assert "Your story breaks these rules" in client.calls[2][0][-1]["content"]


def test_only_one_empty_answer_is_forgiven() -> None:
    client = FakeClient("", "", "")
    outcome = _narrated(client)
    assert outcome.story is None and len(client.calls) == 3


def test_an_invalid_answer_is_retried_once_with_the_errors() -> None:
    bad = _model_story()
    bad["result"]["cites"] = ["invented"]
    client = FakeClient(bad, _model_story())
    outcome = _narrated(client)
    assert outcome.story is not None and len(client.calls) == 2
    retry = client.calls[1][0][-1]["content"]
    assert "invented" in retry


def test_two_invalid_answers_give_no_story() -> None:
    bad = _model_story()
    bad["result"]["cites"] = ["invented"]
    outcome = _narrated(FakeClient("not json", bad))
    assert outcome.story is None
    assert any("invented" in e for e in outcome.errors)


def test_the_input_names_no_models() -> None:
    export = _export()
    export["model_assignments"] = {"a": "secret-lab/hidden-model", "b": "secret-lab/hidden-model"}
    export["agent_runs"] = [{"actor_id": "a", "role": "player", "model_requested": "secret-lab/hidden-model"}]
    text = json.dumps(narrator_input(build_report(export), export))
    assert "hidden-model" not in text and "secret-lab" not in text
    assert "m-priv" in text and "x1" in text and "e1" in text


def test_the_system_prompt_is_the_versioned_file() -> None:
    client = FakeClient(_model_story())
    _narrated(client)
    system = client.calls[0][0][0]
    assert system["role"] == "system" and "SCQA" in system["content"]
    assert NARRATOR_PROMPT_VERSION == "aar-narrator-v1.0"


def test_the_narration_is_cached_until_the_export_changes(tmp_path: Path) -> None:
    export = _export()
    path = tmp_path / "narration.json"
    client = FakeClient(_model_story(), _model_story())
    first = load_or_narrate(path, build_report(export), export, lambda: client, model="m/narrator")
    assert first.story is not None and len(client.calls) == 1
    saved = json.loads(path.read_text())
    assert saved["prompt_version"] == "aar-narrator-v1.0" and saved["model_returned"] == "m/narrator-x"
    assert saved["export_sha256"] and saved["story"]["result"]["headline"]

    again = load_or_narrate(path, build_report(export), export, lambda: pytest.fail("no call"), model="m/narrator")
    assert again.story is not None and again.cached

    export["messages"][0]["text"] += " Changed."
    load_or_narrate(path, build_report(export), export, lambda: client, model="m/narrator")
    assert len(client.calls) == 2


def test_an_edited_cache_is_still_validated(tmp_path: Path) -> None:
    export = _export()
    path = tmp_path / "narration.json"
    client = FakeClient(_model_story())
    load_or_narrate(path, build_report(export), export, lambda: client, model="m/narrator")
    saved = json.loads(path.read_text())
    saved["story"]["result"]["cites"] = ["made-up"]
    path.write_text(json.dumps(saved))
    outcome = load_or_narrate(path, build_report(export), export, lambda: pytest.fail("no call"), model="m/narrator")
    assert outcome.story is None and any("made-up" in e for e in outcome.errors)


def test_renarrate_forces_a_new_call(tmp_path: Path) -> None:
    export = _export()
    path = tmp_path / "narration.json"
    client = FakeClient(_model_story(), _model_story())
    load_or_narrate(path, build_report(export), export, lambda: client, model="m/narrator")
    load_or_narrate(path, build_report(export), export, lambda: client, model="m/narrator", renarrate=True)
    assert len(client.calls) == 2


@pytest.mark.llm
def test_the_default_model_narrates_the_canonical_game(canonical_export: dict) -> None:
    """Layer 4: a real narration of the canonical game must pass validation (needs a key)."""
    from agents.model_client import OpenRouterClient
    from app.config import Settings

    settings = Settings()
    with OpenRouterClient.from_settings(settings) as client:
        outcome = narrate(build_report(canonical_export), canonical_export, client, model=settings.umpire_model)
    assert outcome.story is not None, outcome.errors
