"""Safe Resource materialisation and deterministic authorised search/read projections.

This module interprets transport formats only. Scenario-defined ``Resource.type`` and metadata
remain opaque (INV-RES-001). Format results are derived once from the immutable bytes captured by
``app.scenarios``. T-047 search accepts only an already-authorised candidate collection from
``app.visibility``; T-048 read projections make sensitive persistence fields unrepresentable.
This module performs no database query, visibility decision, filesystem read or mutation.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Literal

import yaml

from amaw_contracts import (
    SEARCH_CONTRACTS,
    SEARCH_CONTRACT_DEFAULT,
    SEARCH_CONTRACT_V1,
    SEARCH_CONTRACT_V2,
)
from app.models import Resource

MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 1_000_000
MAX_SEARCH_RESULTS = 20
MAX_SNIPPET_CHARS = 160
# D-084: frozen integer ranking weights for the experimental token-coverage contract.
SEARCH_V2_NAME_TOKEN_WEIGHT = 8
SEARCH_V2_TAG_TOKEN_WEIGHT = 4
SEARCH_V2_TEXT_TOKEN_WEIGHT = 1
SEARCH_V2_EXACT_NAME_BONUS = 1000
SEARCH_V2_NAME_PHRASE_BONUS = 200
SEARCH_V2_TAG_PHRASE_BONUS = 100
SEARCH_V2_TEXT_PHRASE_BONUS = 25


class FormatMaterializationError(ValueError):
    """A Resource cannot be represented safely under the T-046 format policy."""


@dataclass(frozen=True)
class ResourceMaterialization:
    """Stable text or opaque attachment metadata derived from snapshotted source bytes."""

    representation_kind: Literal["text", "attachment"]
    media_type: str
    text_content: str | None
    attachment_name: str | None
    size_bytes: int


class InvalidResourceQuery(ValueError):
    """The Actor supplied no searchable text after whitespace trimming."""


class InvalidSearchContract(ValueError):
    """The caller selected no known versioned Resource-search contract."""


@dataclass(frozen=True)
class ResourceSearchResult:
    """The complete safe Actor-facing search projection."""

    id: str
    name: str
    type: str
    tags: tuple[str, ...]
    snippet: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "tags": list(self.tags),
            "snippet": self.snippet,
        }


SearchReplayKey = tuple[str, str, str, str, str]
SearchReplayValue = tuple[ResourceSearchResult, ...]


@dataclass
class _PendingSearch:
    """One in-flight key shared by concurrent duplicate callers only."""

    event: threading.Event
    value: SearchReplayValue | None = None
    error: BaseException | None = None


class ActivationSearchReplayCache:
    """Bounded single-flight cache for immutable same-activation search results.

    Keys are supplied by the HTTP boundary as ``(game, Actor, activation, search contract,
    normalized query)``. Keeping every visibility and ranking dimension in the key prevents a
    result authorised or ranked for one context from being reused in another. The cache belongs
    to one application instance, is never persisted, and is bypassed when an activation
    correlation id is absent.

    A per-key pending result makes concurrent duplicates single-flight without serializing
    unrelated Actors or queries. Callers still perform their own access-log write.
    """

    def __init__(self, *, max_entries: int = 1024) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[SearchReplayKey, SearchReplayValue] = OrderedDict()
        self._inflight: dict[SearchReplayKey, _PendingSearch] = {}
        self._lock = threading.Lock()

    def get_or_compute(
        self,
        key: SearchReplayKey,
        compute: Callable[[], SearchReplayValue],
    ) -> SearchReplayValue:
        """Return one cached immutable projection or compute and retain it atomically."""
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
            pending = self._inflight.get(key)
            owner = pending is None
            if owner:
                pending = _PendingSearch(event=threading.Event())
                self._inflight[key] = pending

        assert pending is not None
        if not owner:
            pending.event.wait()
            if pending.error is not None:
                raise pending.error
            assert pending.value is not None
            return pending.value

        try:
            value = compute()
        except BaseException as exc:
            with self._lock:
                pending.error = exc
                self._inflight.pop(key, None)
                pending.event.set()
            raise

        with self._lock:
            self._entries[key] = value
            if len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            pending.value = value
            self._inflight.pop(key, None)
            pending.event.set()
            return value


@dataclass(frozen=True)
class ResourceReadProjection:
    """Safe readable-text or opaque-attachment projection (never generic ORM output)."""

    id: str
    name: str
    type: str
    tags: tuple[str, ...]
    metadata: dict[str, Any]
    content: str | None = None
    attachment_name: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
        }
        if self.content is not None:
            value["content"] = self.content
        else:
            value["attachment"] = {
                "name": self.attachment_name,
                "media_type": self.media_type,
                "size_bytes": self.size_bytes,
            }
        value["tags"] = list(self.tags)
        value["metadata"] = dict(self.metadata)
        return value


_TEXT_MEDIA_TYPES = {
    ".md": "text/markdown; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".yaml": "application/yaml; charset=utf-8",
    ".yml": "application/yaml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}

_ATTACHMENT_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".geojson": "application/geo+json",
}


def _safe_basename(name: str | None) -> str:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or PurePath(name).name != name
    ):
        raise FormatMaterializationError("file Resource attachment name must be a safe basename")
    return name


def _decode_text(source_bytes: bytes) -> str:
    try:
        text = source_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FormatMaterializationError("text Resource must contain valid UTF-8") from exc
    if len(text) > MAX_TEXT_CHARS:
        raise FormatMaterializationError(
            f"readable Resource exceeds the {MAX_TEXT_CHARS:,}-character limit"
        )
    return text


def _validate_structured_text(extension: str, text: str) -> None:
    if extension in {".yaml", ".yml"}:
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise FormatMaterializationError(f"Resource contains malformed YAML: {exc}") from exc
        return

    if extension == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            raise FormatMaterializationError(f"Resource contains malformed JSON: {exc}") from exc
        return

    if extension == ".csv":
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error as exc:
            raise FormatMaterializationError(f"Resource contains malformed CSV: {exc}") from exc
        widths = {len(row) for row in rows}
        if len(widths) > 1:
            raise FormatMaterializationError("CSV Resource rows must have a consistent field count")


def _validate_attachment_signature(extension: str, source_bytes: bytes) -> None:
    if extension == ".pdf" and not source_bytes.startswith(b"%PDF-"):
        raise FormatMaterializationError("PDF Resource does not have a recognised PDF signature")
    if extension == ".png" and not source_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FormatMaterializationError("PNG Resource does not have a recognised PNG signature")
    if extension in {".jpg", ".jpeg"} and not source_bytes.startswith(b"\xff\xd8\xff"):
        raise FormatMaterializationError("JPEG Resource does not have a recognised JPEG signature")


def materialize_resource(
    *, source_kind: Literal["inline", "file"], source_bytes: bytes, attachment_name: str | None
) -> ResourceMaterialization:
    """Validate and materialise one immutable Resource byte snapshot.

    Required textual formats preserve their original decoded text after validation. Optional
    formats expose only safe attachment metadata; their bytes remain in the T-045 snapshot and are
    not semantically parsed. Dispatch never consults scenario-defined Resource type or metadata.
    """
    if len(source_bytes) > MAX_RESOURCE_BYTES:
        raise FormatMaterializationError("Resource exceeds the 2 MiB source-byte limit")

    if source_kind == "inline":
        if attachment_name is not None:
            raise FormatMaterializationError("inline Resource must not have an attachment name")
        return ResourceMaterialization(
            representation_kind="text",
            media_type="text/plain; charset=utf-8",
            text_content=_decode_text(source_bytes),
            attachment_name=None,
            size_bytes=len(source_bytes),
        )

    if source_kind != "file":
        raise FormatMaterializationError(f"unsupported Resource source kind: {source_kind!r}")

    safe_name = _safe_basename(attachment_name)
    extension = PurePath(safe_name).suffix.lower()
    if not extension:
        raise FormatMaterializationError("file Resource requires a supported file extension")

    if extension in _TEXT_MEDIA_TYPES:
        text = _decode_text(source_bytes)
        _validate_structured_text(extension, text)
        return ResourceMaterialization(
            representation_kind="text",
            media_type=_TEXT_MEDIA_TYPES[extension],
            text_content=text,
            attachment_name=safe_name,
            size_bytes=len(source_bytes),
        )

    if extension in _ATTACHMENT_MEDIA_TYPES:
        _validate_attachment_signature(extension, source_bytes)
        return ResourceMaterialization(
            representation_kind="attachment",
            media_type=_ATTACHMENT_MEDIA_TYPES[extension],
            text_content=None,
            attachment_name=safe_name,
            size_bytes=len(source_bytes),
        )

    raise FormatMaterializationError(f"unsupported Resource file extension: {extension}")


def _collapsed(text: str) -> str:
    """Collapse every whitespace run to one ASCII space for stable compact snippets."""
    return " ".join(text.split())


def _snippet(text: str, folded_query: str) -> str:
    """Return at most 160 chars, centered around the first folded substring match."""
    compact = _collapsed(text)
    if len(compact) <= MAX_SNIPPET_CHARS:
        return compact
    match_at = compact.casefold().find(folded_query)
    if match_at < 0:
        match_at = 0
    content_budget = MAX_SNIPPET_CHARS - 2
    start = max(0, match_at - content_budget // 2)
    end = min(len(compact), start + content_budget)
    if end - start < content_budget:
        start = max(0, end - content_budget)
    value = compact[start:end]
    if start > 0:
        value = "…" + value
    if end < len(compact):
        value += "…"
    return value


def normalize_search_query(query: str) -> tuple[str, str]:
    """Return the public trimmed echo and the mechanical trim+casefold cache/search key."""
    normalized_query = query.strip()
    if not normalized_query:
        raise InvalidResourceQuery("Resource search query must not be blank.")
    return normalized_query, normalized_query.casefold()


def validate_search_contract(contract: str) -> str:
    """Return one known contract name; never silently reinterpret an unknown version."""
    if contract not in SEARCH_CONTRACTS:
        raise InvalidSearchContract(
            f"search contract must be one of {SEARCH_CONTRACTS}, got {contract!r}"
        )
    return contract


def _search_v1(
    candidates: list[Resource], folded_query: str
) -> list[tuple[tuple[bool, bool, bool, bool, bool, str], ResourceSearchResult]]:
    """The released whole-query implementation, isolated byte-for-byte from D-084's v2."""
    matched: list[tuple[tuple[bool, bool, bool, bool, bool, str], ResourceSearchResult]] = []

    for resource in candidates:
        folded_name = resource.name.casefold()
        folded_tags = [tag.casefold() for tag in resource.tags]
        exact_name = folded_name == folded_query
        name_match = folded_query in folded_name
        exact_tag = any(tag == folded_query for tag in folded_tags)
        tag_match = any(folded_query in tag for tag in folded_tags)
        content_match = (
            resource.representation_kind == "text"
            and resource.text_content is not None
            and folded_query in resource.text_content.casefold()
        )
        if not (name_match or tag_match or content_match):
            continue

        if name_match:
            snippet_source = resource.name
        elif tag_match:
            snippet_source = next(
                tag for tag in resource.tags if folded_query in tag.casefold()
            )
        else:
            snippet_source = resource.text_content or ""

        # Descending match classes, then ascending Resource ID. ``False < True``, so negate
        # booleans to use Python's normal ascending tuple sort.
        rank = (
            not exact_name,
            not name_match,
            not exact_tag,
            not tag_match,
            not content_match,
            resource.id,
        )
        matched.append(
            (
                rank,
                ResourceSearchResult(
                    id=resource.id,
                    name=resource.name,
                    type=resource.type,
                    tags=tuple(resource.tags),
                    snippet=_snippet(snippet_source, folded_query),
                ),
            )
        )
    return matched


def _search_tokens(value: str) -> tuple[str, ...]:
    """Casefold, split into Unicode word tokens and deduplicate in first-seen order."""
    return tuple(dict.fromkeys(re.findall(r"\w+", value.casefold(), flags=re.UNICODE)))


def _first_matching_token(source: str, query_tokens: tuple[str, ...]) -> str:
    """Choose the first query token present in ``source`` for deterministic snippet placement."""
    source_tokens = frozenset(_search_tokens(source))
    return next(token for token in query_tokens if token in source_tokens)


def _search_v2(
    candidates: list[Resource], folded_query: str
) -> list[tuple[tuple[int, str], ResourceSearchResult]]:
    """D-084's frozen token-coverage ranking over an already-authorised candidate list."""
    query_tokens = _search_tokens(folded_query)
    # A nonblank query can contain punctuation only. It is valid but matches nothing because v2
    # requires at least one exact word-token match.
    if not query_tokens:
        return []

    matched: list[tuple[tuple[int, str], ResourceSearchResult]] = []
    for resource in candidates:
        folded_name = resource.name.casefold()
        folded_tags = tuple(tag.casefold() for tag in resource.tags)
        folded_text = (
            (resource.text_content or "").casefold()
            if resource.representation_kind == "text"
            else ""
        )
        name_tokens = frozenset(_search_tokens(folded_name))
        tag_tokens = tuple(frozenset(_search_tokens(tag)) for tag in folded_tags)
        all_tag_tokens = frozenset().union(*tag_tokens) if tag_tokens else frozenset()
        text_tokens = frozenset(_search_tokens(folded_text))

        score = 0
        covered = 0
        for token in query_tokens:
            if token in name_tokens:
                score += SEARCH_V2_NAME_TOKEN_WEIGHT
            elif token in all_tag_tokens:
                score += SEARCH_V2_TAG_TOKEN_WEIGHT
            elif token in text_tokens:
                score += SEARCH_V2_TEXT_TOKEN_WEIGHT
            else:
                continue
            covered += 1
        if covered == 0:
            continue

        if folded_name == folded_query:
            score += SEARCH_V2_EXACT_NAME_BONUS
        if folded_query in folded_name:
            score += SEARCH_V2_NAME_PHRASE_BONUS
        if any(folded_query in tag for tag in folded_tags):
            score += SEARCH_V2_TAG_PHRASE_BONUS
        if folded_text and folded_query in folded_text:
            score += SEARCH_V2_TEXT_PHRASE_BONUS

        # Snippet source follows the highest per-token field weight, independent of bonuses.
        if name_tokens.intersection(query_tokens):
            snippet_source = resource.name
        else:
            matching_tag = next(
                (
                    tag
                    for tag in resource.tags
                    if set(_search_tokens(tag)).intersection(query_tokens)
                ),
                None,
            )
            snippet_source = (
                matching_tag if matching_tag is not None else (resource.text_content or "")
            )
        snippet_needle = (
            folded_query
            if folded_query in snippet_source.casefold()
            else _first_matching_token(snippet_source, query_tokens)
        )
        matched.append(
            (
                (-score, resource.id),
                ResourceSearchResult(
                    id=resource.id,
                    name=resource.name,
                    type=resource.type,
                    tags=tuple(resource.tags),
                    snippet=_snippet(snippet_source, snippet_needle),
                ),
            )
        )
    return matched


def search_resources(
    candidates: list[Resource],
    query: str,
    *,
    contract: str = SEARCH_CONTRACT_DEFAULT,
) -> tuple[str, list[ResourceSearchResult]]:
    """Search an *already authorised* Resource list under one deterministic contract.

    This function has no database/session parameter by design: it cannot obtain hidden rows.
    Searchable fields are only name, individual tags and readable text content.
    """
    normalized_query, folded_query = normalize_search_query(query)
    validate_search_contract(contract)
    matched = (
        _search_v1(candidates, folded_query)
        if contract == SEARCH_CONTRACT_V1
        else _search_v2(candidates, folded_query)
    )
    matched.sort(key=lambda item: item[0])
    return normalized_query, [item[1] for item in matched[:MAX_SEARCH_RESULTS]]


def resource_read_projection(resource: Resource) -> ResourceReadProjection:
    """Build the explicit safe projection for one already-authorised Resource."""
    common = {
        "id": resource.id,
        "name": resource.name,
        "type": resource.type,
        "tags": tuple(resource.tags),
        "metadata": dict(resource.resource_metadata),
    }
    if resource.representation_kind == "text" and resource.text_content is not None:
        return ResourceReadProjection(**common, content=resource.text_content)
    return ResourceReadProjection(
        **common,
        attachment_name=resource.attachment_name,
        media_type=resource.media_type,
        size_bytes=resource.size_bytes,
    )
