"""The narrator: one model call that turns a finished game's export into the cards' story.

The model reads a compact digest of the export (Actors and objectives, rulings and Effects in play
order, every Message with its visibility) and must answer with the strict story schema. Its
answer is checked by :func:`aar.story.validate_story`; it gets one retry with the problems listed,
plus one repeat from another provider after an empty answer, and a second failure yields no
story, so the cards fall back to the record. The digest never names the models that played. The result is cached in ``narration.json`` with its provenance, reused
while the export is unchanged, and validated again on every load so a hand edit cannot smuggle an
invented event onto a card (#18, D-121). OpenRouter stays outside ``app/`` (TRD §4).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from aar.model import AfterActionReport
from aar.story import Story, response_format, validate_story
from agents.model_client import ModelClientError, ModelConfig, ModelResult, ProviderErrorCategory
from amaw_resources import read_prompt

NARRATOR_PROMPT_VERSION = "aar-narrator-v1.0"
NARRATOR_PROMPT_VERSIONS = (NARRATOR_PROMPT_VERSION,)
MESSAGE_CHARS = 600
TEMPERATURE = 0.7
# Reasoning tokens count against ``max_tokens``, so reasoning gets its own cap inside it and the
# rest is always left for the story itself.
MAX_TOKENS = 16000
REASONING_MAX_TOKENS = 4000
CALL_DEADLINE_SECONDS = 600.0


class NarratorClient(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], *, config: ModelConfig, max_attempts: int = 2
    ) -> ModelResult: ...


@dataclass
class Narration:
    """What the narrator produced: a valid story, or ``None`` with the reasons."""

    story: Story | None
    errors: list[str] = field(default_factory=list)
    model_requested: str | None = None
    model_returned: str | None = None
    usage: list[dict[str, Any]] = field(default_factory=list)
    cached: bool = False


def export_sha256(export: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(export, sort_keys=True).encode("utf-8")).hexdigest()


def narrator_input(report: AfterActionReport, export: dict[str, Any]) -> dict[str, Any]:
    """The game record the narrator reads. Models and providers are deliberately absent."""
    effect_ids: dict[str, list[dict[str, str]]] = {}
    for effect in export.get("effects") or []:
        if effect.get("source_argument_id"):
            effect_ids.setdefault(effect["source_argument_id"], []).append(
                {"id": effect["id"], "description": effect["description"]})
    return {
        "scenario": {"id": report.scenario_id, "version": report.scenario_version,
                     "summary": report.scenario_summary, "turns_played": report.turns_played},
        "actors": [{"id": a.id, "name": a.name, "public_description": a.public_description,
                    "objectives": a.objectives} for a in report.actors],
        "turns": [
            {"turn": t.number, "arguments": [
                {"id": a.id, "actor_id": a.actor_id, "action": a.action,
                 "desired_effect": a.desired_effect,
                 "ruling": a.ruling.model_dump() if a.ruling else None,
                 "effects": effect_ids.get(a.id, [])}
                for a in t.arguments]}
            for t in report.turns
        ],
        "messages": [
            {"id": m["id"], "turn": m["turn"], "sender": m["sender_actor_id"],
             "recipients": m.get("recipients") or [], "visibility": "public" if m.get("public") else "private",
             "text": (m.get("text") or "")[:MESSAGE_CHARS]}
            for m in export.get("messages") or []
        ],
        "final_effects": [
            {"id": e["id"], "description": e["description"]}
            for e in export.get("effects") or []
            if e.get("active") and e.get("created_turn") == report.turns_played
        ],
    }


def _parse(completion: str, export: dict[str, Any]) -> tuple[Story | None, list[str]]:
    text = completion.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        story = Story.model_validate(json.loads(text) | {"narrated": True})
    except (json.JSONDecodeError, TypeError) as exc:
        return None, [f"the answer is not a JSON object: {exc}"]
    except ValidationError as exc:
        return None, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return story, validate_story(story, export)


def _complete(
    client: NarratorClient, messages: list[dict[str, Any]], config: ModelConfig
) -> tuple[ModelResult, ModelConfig]:
    """One narrator call; a model that cannot take the reasoning cap is asked again without it.

    ``require_parameters`` makes OpenRouter answer 404 when no endpoint of the model accepts every
    parameter, which is how a non-reasoning model refuses ``reasoning``. Dropping the cap and
    repeating the call once keeps narration open to every model. The returned config is the one
    that worked, so a retry for invalid output does not hit the same refusal.
    """
    try:
        return client.complete(messages, config=config), config
    except ModelClientError as exc:
        if exc.category is not ProviderErrorCategory.UNSUPPORTED_MODEL or "reasoning" not in config.extra_body:
            raise
    plain = replace(config, extra_body={k: v for k, v in config.extra_body.items() if k != "reasoning"})
    return client.complete(messages, config=plain), plain


def _config_ignoring_provider(config: ModelConfig, provider: str | None) -> ModelConfig:
    """Exclude the downstream provider that returned an empty answer (D-061)."""
    if not provider or provider.casefold() == "openrouter":
        return config
    routing = dict(config.extra_body.get("provider") or {})
    ignored = list(routing.get("ignore") or [])
    if provider in ignored:
        return config
    routing["ignore"] = [*ignored, provider]
    return replace(config, extra_body={**config.extra_body, "provider": routing})


def narrate(
    report: AfterActionReport, export: dict[str, Any], client: NarratorClient, *, model: str
) -> Narration:
    config = ModelConfig(
        model=model, temperature=TEMPERATURE, max_tokens=MAX_TOKENS, deadline=CALL_DEADLINE_SECONDS,
        extra_body={"response_format": response_format(), "provider": {"require_parameters": True},
                    "reasoning": {"max_tokens": REASONING_MAX_TOKENS}},
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": read_prompt(NARRATOR_PROMPT_VERSION)},
        {"role": "user", "content": json.dumps(narrator_input(report, export), ensure_ascii=False)},
    ]
    outcome = Narration(story=None, model_requested=model)
    empty_answer_forgiven = False
    attempt = 0
    while attempt < 2:
        result, config = _complete(client, messages, config)
        outcome.model_returned = result.model_returned
        outcome.usage.append(result.usage or {})
        if not result.completion.strip() and not empty_answer_forgiven:
            # An empty answer is a provider failure, not a story to fix: ask once more with that
            # provider excluded, keeping the fix-it retry for the story (as the Umpire, D-061).
            empty_answer_forgiven = True
            config = _config_ignoring_provider(config, result.provider)
            continue
        attempt += 1
        story, errors = _parse(result.completion, export)
        if story is not None and not errors:
            outcome.story, outcome.errors = story, []
            return outcome
        outcome.errors = errors
        if attempt == 1:
            messages += [
                {"role": "assistant", "content": result.completion},
                {"role": "user", "content": "Your story breaks these rules. Fix every one and return "
                                            "the whole JSON object again:\n- " + "\n- ".join(errors)},
            ]
    return outcome


def load_narration(path: Path, export: dict[str, Any]) -> Narration | None:
    """The saved narration for exactly this export, validated again; ``None`` when there is none."""
    if not path.is_file():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("export_sha256") != export_sha256(export) or not saved.get("story"):
        return None
    try:
        story = Story.model_validate(saved["story"] | {"narrated": True})
    except ValidationError as exc:
        return Narration(story=None, errors=[str(exc)], cached=True)
    errors = validate_story(story, export)
    return Narration(story=None if errors else story, errors=errors, cached=True,
                     model_requested=saved.get("model_requested"),
                     model_returned=saved.get("model_returned"))


def load_or_narrate(
    path: Path,
    report: AfterActionReport,
    export: dict[str, Any],
    client_factory: Callable[[], NarratorClient],
    *,
    model: str,
    renarrate: bool = False,
) -> Narration:
    """Reuse ``path`` while it matches this export; otherwise narrate and save. A cached story is
    validated again, so a hand-edited ``narration.json`` is held to the same rules."""
    digest = export_sha256(export)
    cached = None if renarrate else load_narration(path, export)
    if cached is not None:
        return cached
    outcome = narrate(report, export, client_factory(), model=model)
    if outcome.story is not None:
        path.write_text(json.dumps({
            "prompt_version": NARRATOR_PROMPT_VERSION,
            "model_requested": outcome.model_requested,
            "model_returned": outcome.model_returned,
            "usage": outcome.usage,
            "export_sha256": digest,
            "story": outcome.story.model_dump(exclude={"narrated"}),
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    return outcome
