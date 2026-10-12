"""How the reference player classifies what went wrong: the player errors, the activation failure
categories (only a transient failure is retried) and the invalid-tool-call classification that
benchmark analysis counts (agents.md §18, T-041)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import httpx
from pydantic import ValidationError
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ModelRetry,
    ToolRetryError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.messages import ToolReturnPart

from amaw_contracts import INVALID_TOOL_CALL_CATEGORIES, INVALID_TOOL_CALL_SCHEMA_VERSION
from agents.model_client import _STATUS_CATEGORY, ProviderErrorCategory


class PlayerError(RuntimeError):
    """An unrecoverable player-side failure (e.g. the authoritative context could not be read)."""


class PlayerConfigError(PlayerError):
    """The player is misconfigured — e.g. a real model was requested with no OpenRouter key."""


# Failure categories for one activation (T-041). Only ``TRANSIENT`` is retried (once); the rest
# are terminal for the activation and hand control to the runner's stall-recovery policy.
FAIL_TRANSIENT = "transient"  # a clearly transient model/network failure — retry once
FAIL_DETERMINISTIC = "deterministic"  # auth/validation/config — retrying cannot help
FAIL_GUARDRAIL = "guardrail"  # a per-activation ceiling (iterations/tool calls) was hit


class ActivationFailed(PlayerError):
    """One activation ultimately failed (agents.md §18 step 3 / TRD §35).

    Raised only after the single transient retry is exhausted, or immediately for a deterministic
    or guardrail-ceiling failure. It is the structured signal the :class:`LifecycleRunner` turns
    into a recorded failure plus a legal auto-ready/auto-pass — never a swallowed exception and
    never an unbounded loop. :attr:`category` is one of the ``FAIL_*`` constants.
    """

    def __init__(self, message: str, *, category: str, counts: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.detail = message
        self.counts = counts or {}
        self.activation_id: str | None = None


def classify_invalid_tool_calls(
    messages: list[Any],
    *,
    executed_rejections: list[dict[str, Any]] | None = None,
    decision_tool_name: str | None = None,
) -> dict[str, Any]:
    """Classify events in the compatible invalid-call total without retaining content."""
    from pydantic_ai.messages import RetryPromptPart

    events: list[dict[str, Any]] = []

    def append(
        category: str,
        reason: str,
        *,
        action: str | None,
        error_code: str | None,
        http_status: int | None,
        ensuing_model_retry: bool,
    ) -> None:
        events.append(
            {
                "sequence": len(events) + 1,
                "category": category,
                "reason": reason,
                "action": action,
                "error_code": error_code,
                "http_status": http_status,
                "ensuing_model_retry": ensuing_model_retry,
            }
        )

    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict):
                content = part.content
                if content.get("ok") is not False:
                    continue
                raw_error = content.get("error")
                tool_error = raw_error if isinstance(raw_error, dict) else {}
                code = tool_error.get("code")
                status = content.get("http_status")
                append(
                    "callable_tool_failure",
                    "server_rejection" if isinstance(status, int) else "client_guardrail",
                    action=part.tool_name,
                    error_code=code if isinstance(code, str) else None,
                    http_status=status if isinstance(status, int) else None,
                    ensuing_model_retry=True,
                )
            elif (
                decision_tool_name is not None
                and isinstance(part, RetryPromptPart)
                and part.tool_name == decision_tool_name
            ):
                reason = (
                    "schema_validation"
                    if isinstance(part.content, list)
                    else "output_validation"
                )
                error_code = None
                if isinstance(part.content, str):
                    try:
                        retry_payload = json.loads(part.content)
                    except (TypeError, ValueError):
                        retry_payload = None
                    if isinstance(retry_payload, dict) and retry_payload.get("ok") is False:
                        error = retry_payload.get("error")
                        if isinstance(error, dict) and isinstance(error.get("code"), str):
                            error_code = error["code"]
                            reason = "client_guardrail"
                append(
                    "decision_validation_retry",
                    reason,
                    action=part.tool_name,
                    error_code=error_code,
                    http_status=None,
                    ensuing_model_retry=True,
                )

    for rejected in executed_rejections or []:
        status = rejected.get("http_status")
        append(
            "executed_action_rejection",
            "server_rejection" if isinstance(status, int) else "execution_precondition",
            action=rejected.get("action") if isinstance(rejected.get("action"), str) else None,
            error_code=rejected.get("code") if isinstance(rejected.get("code"), str) else None,
            http_status=status if isinstance(status, int) else None,
            ensuing_model_retry=False,
        )

    counts_by_category = {
        category: sum(event["category"] == category for event in events)
        for category in INVALID_TOOL_CALL_CATEGORIES
        if any(event["category"] == category for event in events)
    }
    reasons = sorted({event["reason"] for event in events})
    return {
        "schema_version": INVALID_TOOL_CALL_SCHEMA_VERSION,
        "total": len(events),
        "counts_by_category": counts_by_category,
        "counts_by_reason": {
            reason: sum(event["reason"] == reason for event in events) for reason in reasons
        },
        "events": events,
    }


_CAUSE_CHAIN_LIMIT = 5
_CAUSE_ERRORS_LIMIT = 10
_CAUSE_TEXT_CHARS = 160


def _failure_cause(exc: BaseException) -> dict[str, Any]:
    """Why one attempt failed, in a form safe for the failure record and the export.

    The record names the cause, never the content: the exception types down the chain, the
    framework's own message, and for validation errors only each field path and error type.
    Rejected values, ``msg`` texts that can echo them, and the model's output are left out,
    because failure records reach exports that may be shared beyond the Actor's own material.
    A guardrail retry keeps only its code. An operator can then tell, from the export alone, a
    model that double-encodes objects from one that never calls the decision tool (#33).
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < _CAUSE_CHAIN_LIMIT and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    cause: dict[str, Any] = {"exception_chain": [type(item).__name__ for item in chain]}
    if isinstance(exc, UnexpectedModelBehavior):
        cause["message"] = exc.message[:_CAUSE_TEXT_CHARS]
    errors: list[dict[str, str]] = []
    for item in chain:
        if isinstance(item, ValidationError):
            errors.extend(_error_locations(item.errors()))
        elif isinstance(item, ToolRetryError):
            content = item.tool_retry.content
            if isinstance(content, str):
                cause["retry_prompt"] = _retry_prompt_summary(content)
            else:
                errors.extend(_error_locations(content))
        elif isinstance(item, ModelRetry):
            cause["retry_prompt"] = _retry_prompt_summary(item.message)
    if errors:
        cause["validation_errors"] = errors[:_CAUSE_ERRORS_LIMIT]
    return cause


def _error_locations(errors: Iterable[Any]) -> list[dict[str, str]]:
    return [
        {"loc": ".".join(str(part) for part in error.get("loc", ())), "type": str(error.get("type"))}
        for error in errors
    ]


def _retry_prompt_summary(content: str) -> Any:
    """A guardrail retry is reduced to its code; other retry text is the framework's, clipped."""
    try:
        payload = json.loads(content)
    except ValueError:
        return content[:_CAUSE_TEXT_CHARS]
    code = (payload.get("error") or {}).get("code") if isinstance(payload, dict) else None
    return {"code": code} if isinstance(code, str) else {"code": None}


def _cause_summary(cause: dict[str, Any]) -> str:
    """One line for the failure reason: the chain, then the first field or retry code."""
    text = " <- ".join(cause["exception_chain"])
    errors = cause.get("validation_errors")
    prompt = cause.get("retry_prompt")
    if errors:
        text += f" ({errors[0]['loc']}: {errors[0]['type']})"
    elif isinstance(prompt, dict) and prompt.get("code"):
        text += f" ({prompt['code']})"
    return text


def _classify_activation_error(exc: BaseException) -> str:
    """Map an exception raised by the model run to a ``FAIL_*`` category (drives the one retry).

    Reuses T-038's status→category table (:data:`agents.model_client._STATUS_CATEGORY`) so the
    reference player and the raw OpenRouter client agree on what "transient" means — no second
    classification. Network-level httpx failures are transient. A model-behaviour failure
    (``UnexpectedModelBehavior``: output-validation exhaustion, an unknown tool, unusable tool
    output) is *deliberately* treated as transient too — it is one bad sample, and one retry on a
    fresh sample is cheap and bounded (D-066). Everything unrecognised is deterministic (retrying
    cannot help), so we never retry blindly.
    """
    if isinstance(exc, UsageLimitExceeded):
        return FAIL_GUARDRAIL
    if isinstance(exc, ModelHTTPError):
        category = _STATUS_CATEGORY.get(exc.status_code, ProviderErrorCategory.OTHER)
        return FAIL_TRANSIENT if category is ProviderErrorCategory.TRANSIENT else FAIL_DETERMINISTIC
    # The native OpenRouter adapter uses ModelAPIError for a documented intermittent HTTP-200
    # body with no completion/error envelope. It is explicitly a transient provider hiccup.
    if isinstance(exc, (ModelAPIError, UnexpectedModelBehavior)):
        return FAIL_TRANSIENT
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return FAIL_TRANSIENT
    return FAIL_DETERMINISTIC
