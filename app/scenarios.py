"""Scenario manifest loading, validation and game creation (T-009…T-011).

The scenario→game pipeline, in one cohesive module (TRD §5, do not split prematurely):

1. :func:`load_scenario` reads exact package bytes, parses ``scenario.yaml`` with a
   duplicate-mapping-key guard, validates its schema/rules and returns one immutable loaded-package
   snapshot containing validated Resource bytes and SHA-256 provenance (T-045).

2. :func:`create_game_from_scenario` turns a validated :class:`Scenario` into a persisted
   Game + Actors + Seats + starting Effects that exist in **LOBBY** (TRD §7.1,
   state-machine §14). It runs in one transaction (INV-GAME-004): a failure leaves no partial
   state. Game **start** — LOBBY→DIPLOMACY, ``turn = 1`` — is T-013, not here.

This is a small YAML manifest loader, not a generic scenario framework. T-046 validates Resource
transport formats and derives stable text-or-attachment projections here; search/read behaviour
remains T-047/T-048, and the full run manifest remains T-053.

**Strict-field policy** (scenario-format §4, one policy consistently): unknown *structured*
fields are rejected (``extra="forbid"``); the free-form ``metadata`` mappings still accept
arbitrary contents. ``app/`` imports no agent framework (AGENTS.md §6).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import Engine

from app import persistence
from app.database import session_scope
from app.game_loop import (
    CON_MODE_SAMPLED_SEALED,
    CON_POLICY_VERSION,
    CON_REPLY_POLICY_VERSION,
    CONSEQUENCE_POLICY_VERSION,
    DIPLOMACY_POLICY_VERSION,
    MAX_PRIMARY_ARGUMENTS_PER_ACTOR,
    GameLoopConditionLayer,
    InvalidGameLoopCondition,
    resolve_game_loop_condition,
)
from app.models import Actor, Effect, Game, GamePhase, GameStatus, Resource, Seat
from app.resources import MAX_RESOURCE_BYTES, FormatMaterializationError, materialize_resource
from app.security import hash_token, new_seat_token

Visibility = Literal["public", "private"]

_MANIFEST_NAME = "scenario.yaml"
_UNRESOLVED_EFFECT_MARKER = re.compile(
    r"(?i)(?<!\w)(?:TODO|TBD|FIXME)(?!\w)|(?<!\w)FYLL_I[A-Z0-9_]*|<\s*placeholder\s*>"
)


# --- Errors: parse vs validation vs creation --------------------------------------------


class ScenarioError(Exception):
    """Base for every scenario-pipeline failure (missing manifest, parse, validation)."""


class ScenarioParseError(ScenarioError):
    """The YAML could not be parsed — syntax error or a duplicate mapping key."""


class ScenarioValidationError(ScenarioError):
    """The manifest parsed but violates the schema or a scenario-format rule.

    Carries the individual failures in :attr:`errors` so callers (and tests) can inspect them
    without parsing a message string.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class GameCreationError(ScenarioError):
    """A validated scenario could not be turned into a game (e.g. no seed supplied)."""


# --- Manifest schema (Pydantic v2) ------------------------------------------------------


class ScenarioMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: int | str
    name: str = Field(min_length=1)
    description: str | None = None


class GameConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_turns: int = Field(ge=1)  # scenario-format §6: max_turns >= 1
    turn_order: list[str] = Field(min_length=1)  # §6: turn_order required, non-empty
    # §6 default 1, budget 1–3. D-094 widens D-069 (I-079): a budget above 1 runs round-robin
    # Argument rounds (state-machine §17/§20); anything above the cap is refused, never clamped.
    primary_arguments_per_actor: int = Field(default=1, ge=1)
    max_cons_per_actor: int = Field(default=3, ge=0)  # §6 default 3, Con limit >= 0
    in_world_turn_length: str | None = None
    diplomacy_timeout_seconds: int | None = None
    cons_timeout_seconds: int | None = None
    # scenario-format §6 does not enumerate a seed; testing.md §4 requires the deterministic
    # fixture to carry a fixed RNG seed. Optional here; benchmark supplies per-game seeds (D-009).
    seed: int | None = None
    # scenario-format §6 / D-091: the scenario's default Consequence condition. An explicit
    # creation-time value overrides it; ``none`` is the released loop (state-machine §21 Option A).
    consequence_mode: Literal["none", "bounded_effects_v1"] = "none"
    # scenario-format §6 / D-099 (I-118): the scenario's recommended Diplomacy and Con condition.
    # Absent means the released default. An explicit creation-time value overrides each field,
    # and the values obey the same pairing rules as the API body (one validator, I-060).
    diplomacy_mode: str | None = None
    diplomacy_rounds_per_turn: int | None = None
    con_mode: str | None = None
    con_challengers_per_argument: int | None = None

    @model_validator(mode="after")
    def _declared_condition_is_legal(self) -> GameConfig:
        # A declaration must be legal on its own, resolved over the released defaults: a bound
        # without its mode, or a mode without its bound, is refused here rather than at creation.
        resolve_game_loop_condition(self.condition_defaults()).validate()
        return self

    def condition_defaults(self) -> GameLoopConditionLayer:
        """The condition this scenario declares, as the scenario source for the resolver."""
        return GameLoopConditionLayer(
            diplomacy_mode=self.diplomacy_mode,
            diplomacy_rounds_per_turn=self.diplomacy_rounds_per_turn,
            con_mode=self.con_mode,
            con_challengers_per_argument=self.con_challengers_per_argument,
            consequence_mode=self.consequence_mode,
        )


class ActorDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    public_description: str = Field(min_length=1)
    private_brief: str = Field(min_length=1)
    objectives: list[str] = Field(min_length=1)  # §19: "Actor has no objectives" is an error
    private_information: list[str] = Field(default_factory=list)
    organisation_id: str | None = None
    role: str | None = None
    metadata: dict = Field(default_factory=dict)  # mapped to Actor.actor_metadata (D-007)


class StartingEffectDef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    visibility: Visibility  # §11: only public | private
    visible_to: list[str] = Field(default_factory=list)
    expires_turn: int | None = None

    @field_validator("description")
    @classmethod
    def _reject_unresolved_authoring_marker(cls, value: str) -> str:
        """TEST-SCN-006: placeholders cannot become authoritative starting state."""
        marker = _UNRESOLVED_EFFECT_MARKER.search(value)
        if marker is not None:
            raise ValueError(
                f"unresolved authoring marker {marker.group(0)!r} in starting Effect"
            )
        return value


# D-090 (scenario-format §11.1): the bounds of the semantic scenario-rule declaration.
MAX_SCENARIO_RULES = 32
MAX_SCENARIO_RULE_TEXT_CHARS = 1000


class RuleDef(BaseModel):
    """One public, non-executable scenario rule (scenario-format §11.1, D-090).

    ``id`` + ``text`` and nothing else: no visibility (rules are public in v1), no condition, no
    formula. The engine never interprets ``text``; only the Umpire weighs it (agents.md §21/§23).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    text: str = Field(max_length=MAX_SCENARIO_RULE_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def _authored_rule_text(cls, value: str) -> str:
        """§11.1: non-empty, and no unresolved marker (the §11 rule — rules are authoritative)."""
        if not value.strip():
            raise ValueError("rule text must not be empty")
        marker = _UNRESOLVED_EFFECT_MARKER.search(value)
        if marker is not None:
            raise ValueError(f"unresolved authoring marker {marker.group(0)!r} in rule text")
        return value


# D-095 (scenario-format §11.2): the bounds of the scheduled-inject declaration.
MAX_SCENARIO_INJECTS = 32
MAX_INJECT_DESCRIPTION_CHARS = 1000


class InjectDef(BaseModel):
    """One scheduled inject: an authored Effect released at the start of ``turn`` (D-095).

    Unconditional by design — there is no condition field and the engine never reads
    ``description`` (TRD §41: no rules engine). Turn and visibility checks that need the rest of
    the scenario run in :meth:`Scenario._cross_field_rules`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    turn: int
    description: str = Field(max_length=MAX_INJECT_DESCRIPTION_CHARS)
    visibility: Visibility
    visible_to: list[str] = Field(default_factory=list)
    expires_turn: int | None = None

    @field_validator("description")
    @classmethod
    def _authored_description(cls, value: str) -> str:
        """§11.2: non-empty, and no unresolved marker (it becomes authoritative state, §11)."""
        if not value.strip():
            raise ValueError("inject description must not be empty")
        marker = _UNRESOLVED_EFFECT_MARKER.search(value)
        if marker is not None:
            raise ValueError(
                f"unresolved authoring marker {marker.group(0)!r} in inject description"
            )
        return value


class ResourceDef(BaseModel):
    """A frozen generic declaration whose meaning remains scenario-defined (INV-RES-001)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)  # open string; the engine never branches on it (§13)
    visibility: Visibility
    content: str | None = None
    path: str | None = None
    visible_to: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_content_source(self) -> ResourceDef:
        if (self.content is None) == (self.path is None):
            raise ValueError(f"resource {self.id!r} requires exactly one of 'content' or 'path'")
        return self


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario: ScenarioMeta
    game: GameConfig
    actors: dict[str, ActorDef] = Field(min_length=1)
    rules: list[RuleDef] = Field(default_factory=list, max_length=MAX_SCENARIO_RULES)
    starting_effects: list[StartingEffectDef] = Field(default_factory=list)
    injects: list[InjectDef] = Field(default_factory=list, max_length=MAX_SCENARIO_INJECTS)
    resources: list[ResourceDef] = Field(default_factory=list)
    benchmark: dict | None = None
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cross_field_rules(self) -> Scenario:
        """Rules that span sections (scenario-format §6, §11, §19)."""
        errors: list[str] = []
        actor_ids = set(self.actors)

        if self.game.primary_arguments_per_actor > MAX_PRIMARY_ARGUMENTS_PER_ACTOR:
            errors.append(  # §6 / D-094 (I-109): round-robin rounds are capped
                f"primary_arguments_per_actor must be 1–{MAX_PRIMARY_ARGUMENTS_PER_ACTOR}: a "
                "budget above 1 runs one round-robin Argument round per slot, got "
                f"{self.game.primary_arguments_per_actor}"
            )
        challengers = self.game.con_challengers_per_argument
        if challengers is not None and challengers > len(actor_ids) - 1:
            errors.append(  # §6 / D-099: the declared sampled condition must be seatable (D-053)
                f"con_challengers_per_argument {challengers} exceeds the {len(actor_ids) - 1} "
                "opponents per Argument"
            )
        if len(actor_ids) < 2:
            errors.append(  # §7 / D-069 (I-079): every Argument needs an eligible opponent (§18)
                "a scenario needs at least two Actors: every Argument must have an opponent "
                f"eligible to Con it, got {len(actor_ids)}"
            )

        seen: set[str] = set()
        for actor_id in self.game.turn_order:
            if actor_id not in actor_ids:
                errors.append(f"unknown Actor {actor_id!r} in turn_order")  # §19, TEST-SCN-002
            if actor_id in seen:
                errors.append(f"duplicate Actor {actor_id!r} in turn_order")  # §6
            seen.add(actor_id)

        for i, effect in enumerate(self.starting_effects):
            if effect.visibility == "private":
                if not effect.visible_to:
                    errors.append(
                        f"starting_effects[{i}]: private Effect requires visible_to"  # §11
                    )
                for ref in effect.visible_to:
                    if ref not in actor_ids:
                        errors.append(
                            f"starting_effects[{i}]: visible_to references unknown Actor {ref!r}"
                        )
            elif effect.visible_to:
                errors.append(
                    f"starting_effects[{i}]: public Effect must not set visible_to"  # §11
                )

        inject_ids: set[str] = set()
        for i, inject in enumerate(self.injects):  # §11.2 / D-095
            where = f"injects[{i}] ({inject.id})"
            if inject.id in inject_ids:
                errors.append(f"{where}: duplicate inject id {inject.id!r}")
            inject_ids.add(inject.id)
            if not 2 <= inject.turn <= self.game.max_turns:
                errors.append(
                    f"{where}: inject turn must be between 2 and max_turns "
                    f"({self.game.max_turns}), got {inject.turn}; turn-0 facts are starting Effects"
                )
            if inject.expires_turn is not None and inject.expires_turn <= inject.turn:
                errors.append(f"{where}: inject expires_turn must be after its turn")
            if inject.visibility == "private":
                if not inject.visible_to:
                    errors.append(f"{where}: private inject requires visible_to")
                for ref in inject.visible_to:
                    if ref not in actor_ids:
                        errors.append(
                            f"{where}: inject visible_to references unknown Actor {ref!r}"
                        )
            elif inject.visible_to:
                errors.append(f"{where}: public inject must not set visible_to")

        rule_ids: set[str] = set()
        for i, rule in enumerate(self.rules):
            if rule.id in rule_ids:
                errors.append(f"rules[{i}]: duplicate rule id {rule.id!r}")  # §11.1 / D-090
            rule_ids.add(rule.id)

        resource_ids: set[str] = set()
        for i, resource in enumerate(self.resources):
            if resource.id in resource_ids:
                errors.append(f"resources[{i}]: duplicate Resource ID {resource.id!r}")
            resource_ids.add(resource.id)
            if resource.visibility == "private":
                if not resource.visible_to:
                    errors.append(f"resources[{i}]: private Resource requires visible_to")
                for ref in resource.visible_to:
                    if ref not in actor_ids:
                        errors.append(
                            f"resources[{i}]: visible_to references unknown Actor {ref!r}"
                        )
            elif resource.visible_to:
                errors.append(f"resources[{i}]: public Resource must not set visible_to")

        if errors:
            raise ValueError(" | ".join(errors))
        return self


# --- Loading ----------------------------------------------------------------------------


class _NoDuplicateKeysLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently keeping the last.

    Ordinary YAML parsing collapses duplicate keys, which would erase a duplicate Actor ID
    before any validator could see it. Detecting it here — at the parse boundary — is the
    deliberate solution the task calls for.
    """


def _construct_mapping(loader: _NoDuplicateKeysLoader, node, deep: bool = False) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateKeysLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _validation_errors(exc: ValidationError) -> list[str]:
    """Flatten a Pydantic ValidationError into readable ``loc: msg`` strings (no traceback)."""
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        out.append(f"{loc}: {err['msg']}")
    return out


@dataclass(frozen=True)
class LoadedResource:
    """One validated Resource declaration plus its immutable source-byte snapshot."""

    declaration: ResourceDef
    source_kind: Literal["inline", "file"]
    source_bytes: bytes
    content_sha256: str
    attachment_name: str | None
    representation_kind: Literal["text", "attachment"] = "attachment"
    media_type: str = "application/octet-stream"
    text_content: str | None = None
    size_bytes: int = 0

    def __getattr__(self, name: str) -> Any:
        """Expose declaration fields for the former declaration-only caller surface."""
        return getattr(self.declaration, name)


@dataclass(frozen=True)
class LoadedScenario:
    """One package-aware, immutable loading result (D-030…D-032)."""

    definition: Scenario
    package_root: Path
    scenario_source_bytes: bytes
    scenario_content_sha256: str
    resources: tuple[LoadedResource, ...]
    resource_manifest_sha256: str

    def __getattr__(self, name: str) -> Any:
        """Delegate manifest fields so established callers remain source-compatible."""
        return getattr(self.definition, name)

    @property
    def resource_manifest(self) -> tuple[dict[str, Any], ...]:
        """Return fresh canonical entries so callers cannot mutate stored loader state."""
        return _resource_manifest_entries(self.resources)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_value(value: Any) -> Any:
    """Return a JSON-safe stable value for free-form YAML Resource metadata."""
    if isinstance(value, dict):
        ordered = sorted(value.items(), key=lambda item: str(item[0]))
        return {str(key): _canonical_value(item) for key, item in ordered}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _resource_manifest_entries(
    resources: tuple[LoadedResource, ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "attachment_name": resource.attachment_name,
            "content_sha256": resource.content_sha256,
            "id": resource.declaration.id,
            "metadata": _canonical_value(resource.declaration.metadata),
            "name": resource.declaration.name,
            "source_kind": resource.source_kind,
            "tags": sorted(resource.declaration.tags),
            "type": resource.declaration.type,
            "visibility": resource.declaration.visibility,
            "visible_to": sorted(resource.declaration.visible_to),
        }
        for resource in sorted(resources, key=lambda item: item.declaration.id)
    )


def canonical_resource_manifest(resources: tuple[LoadedResource, ...]) -> bytes:
    """Canonical compact UTF-8 JSON used as the deterministic Resource-manifest hash input."""
    entries = _resource_manifest_entries(resources)
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _loaded_resource(package_root: Path, declaration: ResourceDef) -> LoadedResource:
    if declaration.content is not None:
        source_bytes = declaration.content.encode("utf-8")
        try:
            materialized = materialize_resource(
                source_kind="inline", source_bytes=source_bytes, attachment_name=None
            )
        except FormatMaterializationError as exc:
            raise ScenarioValidationError([f"Resource {declaration.id!r}: {exc}"]) from exc
        return LoadedResource(
            declaration=declaration,
            source_kind="inline",
            source_bytes=source_bytes,
            content_sha256=_sha256(source_bytes),
            attachment_name=None,
            representation_kind=materialized.representation_kind,
            media_type=materialized.media_type,
            text_content=materialized.text_content,
            size_bytes=materialized.size_bytes,
        )

    assert declaration.path is not None  # ResourceDef enforces exactly one source.
    declared_path = declaration.path
    relative = Path(declared_path)
    portable_parts = declared_path.replace("\\", "/").split("/")
    if relative.is_absolute() or PureWindowsPath(declared_path).is_absolute():
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r} path must not be absolute"]
        )
    if ".." in portable_parts:
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r} path must not contain '..'"]
        )

    candidate = package_root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r} file is missing: {declared_path}"]
        ) from exc
    try:
        resolved.relative_to(package_root)
    except ValueError as exc:
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r} resolves outside the scenario package"]
        ) from exc
    if not resolved.is_file():
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r} path must resolve to a regular file"]
        )
    try:
        source_size = resolved.stat().st_size
    except OSError as exc:
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r} file metadata could not be read"]
        ) from exc
    if source_size > MAX_RESOURCE_BYTES:
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r}: Resource exceeds the 2 MiB source-byte limit"]
        )
    try:
        source_bytes = resolved.read_bytes()
    except OSError as exc:
        raise ScenarioValidationError(
            [f"Resource {declaration.id!r} file could not be read"]
        ) from exc
    try:
        materialized = materialize_resource(
            source_kind="file", source_bytes=source_bytes, attachment_name=resolved.name
        )
    except FormatMaterializationError as exc:
        raise ScenarioValidationError([f"Resource {declaration.id!r}: {exc}"]) from exc
    return LoadedResource(
        declaration=declaration,
        source_kind="file",
        source_bytes=source_bytes,
        content_sha256=_sha256(source_bytes),
        attachment_name=resolved.name,
        representation_kind=materialized.representation_kind,
        media_type=materialized.media_type,
        text_content=materialized.text_content,
        size_bytes=materialized.size_bytes,
    )


def load_scenario(directory: Path | str) -> LoadedScenario:
    """Load and validate one package into an immutable :class:`LoadedScenario` snapshot.

    Raises :class:`ScenarioError` if the manifest is missing, :class:`ScenarioParseError` on a
    YAML/duplicate-key failure, or :class:`ScenarioValidationError` on a schema/rule violation.
    """
    package_root = Path(directory).resolve()
    manifest = package_root / _MANIFEST_NAME
    if not manifest.is_file():
        raise ScenarioError(f"no {_MANIFEST_NAME} in {directory}")

    try:
        manifest_bytes = manifest.read_bytes()
        manifest_text = manifest_bytes.decode("utf-8")
        data = yaml.load(manifest_text, Loader=_NoDuplicateKeysLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ScenarioParseError(f"could not parse {_MANIFEST_NAME}: {exc}") from exc

    if not isinstance(data, dict):
        raise ScenarioValidationError([f"{_MANIFEST_NAME} must be a mapping at the top level"])

    try:
        definition = Scenario.model_validate(data)
    except ValidationError as exc:
        raise ScenarioValidationError(_validation_errors(exc)) from exc

    resources = tuple(
        _loaded_resource(package_root, declaration) for declaration in definition.resources
    )
    canonical_manifest = canonical_resource_manifest(resources)
    return LoadedScenario(
        definition=definition,
        package_root=package_root,
        scenario_source_bytes=manifest_bytes,
        scenario_content_sha256=_sha256(manifest_bytes),
        resources=resources,
        resource_manifest_sha256=_sha256(canonical_manifest),
    )


# --- Game creation ----------------------------------------------------------------------


@dataclass
class GameCreation:
    """The result of creating a game: the persisted game id and the raw Seat tokens.

    ``seat_tokens`` (actor_id → raw token) is ephemeral — it is returned to the admin/runner
    exactly once and is never persisted; only each token's hash is stored (INV-ACTOR-001).
    """

    game_id: object
    seat_tokens: dict[str, str]


def create_game_from_scenario(
    engine: Engine,
    scenario: LoadedScenario,
    *,
    seed: int | None = None,
    diplomacy_mode: str | None = None,
    diplomacy_rounds_per_turn: int | None = None,
    con_mode: str | None = None,
    con_challengers_per_argument: int | None = None,
    con_reply_mode: str | None = None,
    reply_max_points: int | None = None,
    consequence_mode: str | None = None,
    require_run_manifest: bool = False,
) -> GameCreation:
    """Persist a LOBBY game from a validated ``scenario`` in one atomic transaction.

    Snapshots the scenario's execution rules onto the Game so runtime never re-reads a file
    that could change (TRD §7.1); persists a per-game Actor snapshot, one Seat per Actor
    (hash only), and each starting Effect. A ``seed`` argument overrides ``scenario.game.seed``;
    if neither is set the operation is refused (:class:`GameCreationError`). The condition
    arguments are the explicit creation-time values: ``None`` means not supplied, and the field
    falls back to the scenario's ``game.*`` default and then the released default (D-091,
    D-099). The resolved condition is what the Game persists. Everything commits
    together or not at all (INV-GAME-004). The game is left in LOBBY — starting it is T-013.
    """
    definition = scenario.definition
    effective_seed = seed if seed is not None else definition.game.seed
    if effective_seed is None:
        raise GameCreationError(
            "game creation requires a seed: none supplied and scenario.game.seed is unset"
        )
    # api.md §14: the three mode/value pairing rules live in one place (I-060). Only the
    # scenario-specific bound stays here: I-055 / D-053 — the sampled condition must be able to
    # seat its challengers, so K is checked against the real Actor count once, at creation, and a
    # game never persists an unsatisfiable bound.
    condition = resolve_game_loop_condition(
        GameLoopConditionLayer(
            diplomacy_mode=diplomacy_mode,
            diplomacy_rounds_per_turn=diplomacy_rounds_per_turn,
            con_mode=con_mode,
            con_challengers_per_argument=con_challengers_per_argument,
            con_reply_mode=con_reply_mode,
            reply_max_points=reply_max_points,
            consequence_mode=consequence_mode,
        ),
        definition.game.condition_defaults(),
    )
    try:
        condition.validate()
    except InvalidGameLoopCondition as exc:
        raise GameCreationError(str(exc)) from exc
    opponents = len(definition.actors) - 1
    if (
        condition.con_mode == CON_MODE_SAMPLED_SEALED
        and condition.con_challengers_per_argument is not None
        and condition.con_challengers_per_argument > opponents
    ):
        raise GameCreationError(
            f"sampled_sealed cannot seat {condition.con_challengers_per_argument} challengers: "
            f"the scenario has {opponents} opponents per Argument"
        )

    cfg = definition.game
    seat_tokens: dict[str, str] = {}

    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id=definition.scenario.id,
                scenario_version=str(definition.scenario.version),
                scenario_summary=(
                    definition.scenario.description
                    or f"{definition.scenario.name}: "
                    + "; ".join(
                        actor.public_description for actor in definition.actors.values()
                    )
                ),
                scenario_source_bytes=scenario.scenario_source_bytes,
                scenario_content_sha256=scenario.scenario_content_sha256,
                resource_manifest=[dict(entry) for entry in scenario.resource_manifest],
                resource_manifest_sha256=scenario.resource_manifest_sha256,
                # D-090: the immutable rule snapshot the Umpire and the export read from.
                scenario_rules=[rule.model_dump() for rule in definition.rules],
                # D-095: the creation-time schedule; released only at each inject's turn start.
                scenario_injects=[inject.model_dump() for inject in definition.injects],
                turn=0,  # LOBBY: start (T-013) sets turn=1 (state-machine §15)
                max_turns=cfg.max_turns,
                phase=GamePhase.diplomacy,  # phase the game enters at start; meaningful post-start
                status=GameStatus.lobby,
                turn_order=list(cfg.turn_order),
                active_actor_id=None,
                seed=effective_seed,
                rng_draw_index=0,
                primary_arguments_per_actor=cfg.primary_arguments_per_actor,
                max_cons_per_actor=cfg.max_cons_per_actor,
                in_world_turn_length=cfg.in_world_turn_length,
                diplomacy_timeout_seconds=cfg.diplomacy_timeout_seconds,
                cons_timeout_seconds=cfg.cons_timeout_seconds,
                diplomacy_ready_actor_ids=[],
                diplomacy_policy_version=DIPLOMACY_POLICY_VERSION,
                diplomacy_mode=condition.diplomacy_mode,
                diplomacy_rounds_per_turn=condition.diplomacy_rounds_per_turn,
                diplomacy_rounds_completed=0,
                diplomacy_close_reason=None,
                con_policy_version=CON_POLICY_VERSION,
                con_mode=condition.con_mode,
                con_challengers_per_argument=condition.con_challengers_per_argument,
                # The rotating schedule's seeded start (D-053): reproducible from the recorded
                # seed, rotating across ``base + repetition`` seeds, and independent of the
                # adjudication draw stream (``rng_draw_index`` stays untouched).
                con_challenger_cursor=(
                    effective_seed % len(definition.actors)
                    if condition.con_mode == CON_MODE_SAMPLED_SEALED else None
                ),
                con_reply_policy_version=CON_REPLY_POLICY_VERSION,
                con_reply_mode=condition.con_reply_mode,
                reply_max_points=condition.reply_max_points,
                consequence_policy_version=CONSEQUENCE_POLICY_VERSION,
                consequence_mode=condition.consequence_mode,
                # I-078: immutable after creation; nothing else writes it.
                require_run_manifest=require_run_manifest,
            ),
        )

        for actor_id, actor_def in definition.actors.items():
            persistence.create(
                session,
                Actor(
                    game_id=game.id,
                    id=actor_id,
                    name=actor_def.name,
                    public_description=actor_def.public_description,
                    private_brief=actor_def.private_brief,
                    objectives=list(actor_def.objectives),
                    private_information=list(actor_def.private_information),
                    organisation_id=actor_def.organisation_id,
                    role=actor_def.role,
                    actor_metadata=dict(actor_def.metadata),  # YAML `metadata` (D-007)
                ),
            )
            raw = new_seat_token()
            seat_tokens[actor_id] = raw
            persistence.create(
                session,
                Seat(game_id=game.id, actor_id=actor_id, token_hash=hash_token(raw)),
            )

        for effect_def in definition.starting_effects:
            persistence.create(
                session,
                Effect(
                    game_id=game.id,
                    description=effect_def.description,
                    visibility=effect_def.visibility,
                    visible_to=list(effect_def.visible_to),
                    source_argument_id=None,
                    created_turn=0,
                    expires_turn=effect_def.expires_turn,
                    active=True,
                    supersedes=[],
                ),
            )

        for loaded_resource in scenario.resources:
            declaration = loaded_resource.declaration
            persistence.create(
                session,
                Resource(
                    game_id=game.id,
                    id=declaration.id,
                    name=declaration.name,
                    type=declaration.type,
                    visibility=declaration.visibility,
                    visible_to=list(declaration.visible_to),
                    tags=list(declaration.tags),
                    resource_metadata=dict(declaration.metadata),
                    source_kind=loaded_resource.source_kind,
                    source_bytes=loaded_resource.source_bytes,
                    attachment_name=loaded_resource.attachment_name,
                    content_sha256=loaded_resource.content_sha256,
                    representation_kind=loaded_resource.representation_kind,
                    media_type=loaded_resource.media_type,
                    text_content=loaded_resource.text_content,
                    size_bytes=loaded_resource.size_bytes,
                ),
            )

        game_id = game.id

    return GameCreation(game_id=game_id, seat_tokens=seat_tokens)
