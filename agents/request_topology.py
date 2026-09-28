"""Privacy-safe logical request-size telemetry for the reference player (I-056).

This measures a deterministic canonical-JSON *projection* at PydanticAI's model-wrapper boundary.
It is deliberately not described as provider wire bytes: concrete adapters may rewrite roles,
schemas and whitespace after this point. Raw content exists only while the request already exists
in memory; the returned telemetry contains counts and an activation-keyed prefix digest only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_core import to_jsonable_python

from amaw_contracts import (
    REQUEST_TOPOLOGY_COMPONENTS,
    REQUEST_TOPOLOGY_SCHEMA_VERSION,
    canonical_json,
)


BOUNDARY = "pydantic-ai-model-wrapper-logical-request"
ENCODING = "canonical-json-utf8"
HASH_SCHEME = "hmac-sha256-ephemeral-activation-key"
ACTIVATION_PACKET_MARKER = "=== DECISION CONTEXT (JSON) ==="

_PACKET_PUBLIC_FIELDS = (
    "scenario",
    "turn",
    "max_turns",
    "phase",
    "status",
    "active_actor_id",
    "requires_input",
    "available_actions",
    "context_mode",
    "memory_version",
    "limits",
)


def _fallback(value: Any) -> dict[str, str]:
    """Represent an unknown runtime type without retaining its string/content representation."""
    return {"runtime_type": type(value).__name__}


def _jsonable(value: Any) -> Any:
    return to_jsonable_python(value, serialize_unknown=True, fallback=_fallback)


def _canonical(value: Any) -> bytes:
    return canonical_json(_jsonable(value))


def _tool_definition(tool: Any) -> dict[str, Any]:
    """The model-visible definition fields, excluding local metadata and callbacks."""
    value = {
        "name": getattr(tool, "name", None),
        "description": getattr(tool, "description", None),
        "parameters_json_schema": getattr(tool, "parameters_json_schema", None),
        "return_schema": getattr(tool, "return_schema", None),
        "strict": getattr(tool, "strict", None),
        "kind": getattr(tool, "kind", None),
    }
    return {key: nested for key, nested in value.items() if nested is not None}


def _append(payloads: dict[str, list[Any]], component: str, value: Any) -> None:
    if value is None or value == [] or value == {} or value == "":
        return
    payloads[component].append(value)


def _split_activation_prompt(content: str) -> tuple[str, dict[str, Any] | None]:
    prefix, marker, body = content.partition(ACTIVATION_PACKET_MARKER)
    if not marker:
        return content, None
    try:
        packet = json.loads(body.strip())
    except (TypeError, ValueError):
        return content, None
    if not isinstance(packet, dict):
        return content, None
    # Retain the literal framing/marker contribution, but canonicalize packet fields separately.
    return f"{prefix}{marker}", packet


def _categorize_packet(payloads: dict[str, list[Any]], packet: dict[str, Any]) -> None:
    public = {name: packet.get(name) for name in _PACKET_PUBLIC_FIELDS if name in packet}
    _append(payloads, "scenario_public_state", public)
    _append(payloads, "actor_private_dossier", packet.get("you"))
    _append(payloads, "effects", packet.get("effects"))
    _append(payloads, "incremental_messages", packet.get("messages_received"))
    _append(payloads, "strategic_memory", packet.get("strategic_memory"))
    _append(payloads, "current_argument_cons", packet.get("current_argument"))


def _categorize_compacted_research(
    payloads: dict[str, list[Any]], content: str
) -> bool:
    """Recognize I-057's exact retained-result envelope after history processing."""
    try:
        retained = json.loads(content)
    except (TypeError, ValueError):
        return False
    if (
        not isinstance(retained, dict)
        or retained.get("schema_version") != "research-history-compact-success-v1"
        or not isinstance(retained.get("retained_research_results"), list)
    ):
        return False
    for item in retained["retained_research_results"]:
        if not isinstance(item, dict):
            return False
        tool_name = item.get("tool_name")
        if tool_name in {
            "search_resources", "read_resource",
            "search_resources_batch", "read_resources_batch",
        }:
            _append(payloads, "resource_results", item.get("data"))
        elif tool_name == "read_messages":
            _append(payloads, "incremental_messages", item.get("data"))
        else:
            return False
    return True


def _categorize_tool_return(payloads: dict[str, list[Any]], part: ToolReturnPart) -> None:
    plumbing = {
        "part_kind": part.part_kind,
        "tool_name": part.tool_name,
        "tool_call_id": part.tool_call_id,
    }
    _append(payloads, "prior_tool_plumbing", plumbing)
    if part.tool_name in {"search_resources", "read_resource"}:
        _append(payloads, "resource_results", part.content)
    elif part.tool_name == "read_messages":
        _append(payloads, "incremental_messages", part.content)
    elif part.tool_name == "observe" and isinstance(part.content, dict):
        observed = part.content.get("data")
        if isinstance(observed, dict):
            _categorize_packet(payloads, observed)
            _append(
                payloads,
                "prior_tool_plumbing",
                {key: value for key, value in part.content.items() if key != "data"},
            )
        else:
            _append(payloads, "prior_tool_plumbing", part.content)
    else:
        _append(payloads, "prior_tool_plumbing", part.content)


def _component_payloads(messages: Any, parameters: Any) -> dict[str, list[Any]]:
    payloads = {name: [] for name in REQUEST_TOPOLOGY_COMPONENTS if name != "framework_envelope"}
    for tool in getattr(parameters, "function_tools", ()):
        _append(payloads, "tool_schemas", _tool_definition(tool))
    for tool in getattr(parameters, "output_tools", ()):
        _append(payloads, "output_schemas", _tool_definition(tool))
    _append(payloads, "output_schemas", getattr(parameters, "output_object", None))

    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                _append(payloads, "prior_tool_plumbing", _jsonable(part))
            continue
        if not isinstance(message, ModelRequest):
            _append(payloads, "prior_tool_plumbing", _jsonable(message))
            continue
        for part in message.parts:
            if isinstance(part, SystemPromptPart):
                _append(payloads, "system_prompts", part.content)
            elif isinstance(part, UserPromptPart) and isinstance(part.content, str):
                prompt, packet = _split_activation_prompt(part.content)
                if packet is not None:
                    _append(payloads, "player_prompts", prompt)
                    _categorize_packet(payloads, packet)
                elif not _categorize_compacted_research(payloads, part.content):
                    _append(payloads, "player_prompts", prompt)
            elif isinstance(part, ToolReturnPart):
                _categorize_tool_return(payloads, part)
            elif isinstance(part, RetryPromptPart):
                _append(payloads, "prior_tool_plumbing", _jsonable(part))
            else:
                _append(payloads, "prior_tool_plumbing", _jsonable(part))
    return payloads


def _common_prefix(left: bytes, right: bytes) -> bytes:
    end = min(len(left), len(right))
    index = 0
    while index < end and left[index] == right[index]:
        index += 1
    return right[:index]


class RequestTopologyProfiler:
    """Measure one activation's successive logical requests without retaining their content."""

    def __init__(self, *, digest_key: bytes | None = None) -> None:
        self._configured_digest_key = digest_key
        self.reset()

    def reset(self) -> None:
        self._digest_key = self._configured_digest_key or secrets.token_bytes(32)
        self._previous: bytes | None = None
        self._requests: list[dict[str, Any]] = []

    def capture(
        self,
        messages: Any,
        model_settings: Any,
        model_request_parameters: Any,
        *,
        request_number: int,
    ) -> dict[str, Any]:
        payloads = _component_payloads(messages, model_request_parameters)
        envelope = {
            "model_settings": _jsonable(model_settings),
            "message_count": len(messages),
            "output_mode": getattr(model_request_parameters, "output_mode", None),
            "allow_text_output": getattr(model_request_parameters, "allow_text_output", None),
        }
        projection = {
            name: value
            for name, value in payloads.items()
            if value
        }
        projection["framework_envelope"] = envelope
        serialized = _canonical(projection)
        component_bytes = {
            name: len(_canonical(payloads[name])) if payloads.get(name) else 0
            for name in REQUEST_TOPOLOGY_COMPONENTS
            if name != "framework_envelope"
        }
        component_bytes["framework_envelope"] = len(serialized) - sum(
            component_bytes.values()
        )
        prefix = b"" if self._previous is None else _common_prefix(self._previous, serialized)
        record = {
            "sequence": request_number,
            "measured_bytes": len(serialized),
            "growth_bytes": (
                None if self._previous is None else len(serialized) - len(self._previous)
            ),
            "stable_prefix_bytes": len(prefix),
            "stable_prefix_digest": (
                hmac.new(self._digest_key, prefix, hashlib.sha256).hexdigest()
                if prefix else None
            ),
            "component_bytes": component_bytes,
            "provider_usage": {
                "available": False,
                "input_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
            },
        }
        self._previous = serialized
        self._requests.append(record)
        return record

    @staticmethod
    def attach_provider_usage(record: dict[str, Any], usage: dict[str, Any]) -> None:
        record["provider_usage"] = {
            "available": True,
            "input_tokens": usage["input_tokens"],
            "cache_read_tokens": usage["cache_read_tokens"],
            "cache_write_tokens": usage["cache_write_tokens"],
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": REQUEST_TOPOLOGY_SCHEMA_VERSION,
            "measurement": {
                "boundary": BOUNDARY,
                "encoding": ENCODING,
                "hash_scheme": HASH_SCHEME,
                "provider_wire_exact": False,
                "tokenizer": None,
            },
            # JSON round-trip gives callers detached content-free records without retaining a
            # second mutable reference into the profiler.
            "requests": json.loads(json.dumps(self._requests)),
        }
