"""Admin-authorised preparation for interruption-safe autonomous resumption (I-048)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlmodel import select

from amaw_contracts import (
    INFERENCE_PROFILE_SCHEMA,
    INFERENCE_SETTING_KEYS,
    PLAYER_GUARDRAIL_FIELDS,
    PLAYER_GUARDRAIL_OPTIONAL_FIELDS,
    PLAYER_INFERENCE_PHASES,
    REASONING_EFFORTS,
    RESUME_COORDINATOR_FIELDS,
    RESUME_PLAYER_FIELDS,
    RESUME_PLAYER_OPTIONAL_FIELDS,
    RESUME_RECIPE_SCHEMA,
    RESUME_CONSEQUENCE_FIELDS,
    RESUME_UMPIRE_FIELDS,
)
from app.database import write_session_scope
from app.game_loop import GameLoopCondition, InvalidGameLoopCondition
from app.game_service import GameNotFound
from app.models import Actor, Game, GameStatus, RunManifest, Seat
from app.security import hash_token, new_seat_token

# I-083: one vocabulary, stated in ``amaw_contracts`` and written by the agent-side builder, so
# this second line of validation cannot lag the recipe again (``app`` still imports nothing
# from the agent layer, TRD §4).
RESUME_SCHEMA = RESUME_RECIPE_SCHEMA
_PLAYER_FIELDS = frozenset(RESUME_PLAYER_FIELDS)
_UMPIRE_FIELDS = frozenset(RESUME_UMPIRE_FIELDS)
_CONSEQUENCE_FIELDS = frozenset(RESUME_CONSEQUENCE_FIELDS)
_COORDINATOR_FIELDS = frozenset(RESUME_COORDINATOR_FIELDS)
_GUARDRAIL_FIELDS = frozenset(PLAYER_GUARDRAIL_FIELDS)


def _optional_keys_well_typed(player: dict) -> bool:
    """A pre-condition recipe lacks the optional keys; a current one must carry them well-formed."""
    for key in RESUME_PLAYER_OPTIONAL_FIELDS:
        if key in player and (not isinstance(player[key], str) or not player[key].strip()):
            return False
    for key in PLAYER_GUARDRAIL_OPTIONAL_FIELDS:
        value = player["guardrails"].get(key)
        if key in player["guardrails"] and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            return False
    return True


class ResumeError(ValueError):
    """The persisted Game cannot be safely resumed."""


@dataclass(frozen=True)
class ResumeDescriptor:
    game_id: uuid.UUID
    status: GameStatus
    actors: dict[str, str]
    manifest: dict
    manifest_sha256: str


def _validate(session, game_id: uuid.UUID) -> tuple[Game, RunManifest, dict[str, str]]:  # noqa: ANN001
    game = session.get(Game, game_id)
    if game is None:
        raise GameNotFound(f"game {game_id!r} does not exist")
    if game.status == GameStatus.complete:
        raise ResumeError("a completed game cannot be resumed")
    if game.status not in {GameStatus.running, GameStatus.paused}:
        raise ResumeError(f"game status {game.status.value!r} is not resumable")
    manifest = session.get(RunManifest, game_id)
    if manifest is None:
        raise ResumeError("game has no immutable run manifest")
    recipe = manifest.manifest.get("resume")
    if not isinstance(recipe, dict) or recipe.get("schema") != RESUME_SCHEMA:
        raise ResumeError("run manifest has no compatible autonomous resume recipe")
    actors = {
        actor.id: actor.name
        for actor in session.exec(
            select(Actor).where(Actor.game_id == game_id).order_by(Actor.id)
        ).all()
    }
    if not actors:
        raise ResumeError("game has no Actors")
    player = recipe.get("player")
    umpire = recipe.get("umpire")
    coordinator = recipe.get("coordinator")
    if (
        not isinstance(player, dict)
        or not _PLAYER_FIELDS.issubset(player)
        or not isinstance(umpire, dict)
        or not _UMPIRE_FIELDS.issubset(umpire)
        or not isinstance(coordinator, dict)
        or not _COORDINATOR_FIELDS.issubset(coordinator)
        or not isinstance(player.get("assignments"), dict)
        or set(player["assignments"]) != set(actors)
        or not isinstance(player.get("guardrails"), dict)
        or not _GUARDRAIL_FIELDS.issubset(player["guardrails"])
        or not _optional_keys_well_typed(player)
    ):
        raise ResumeError("run manifest contains an incomplete autonomous resume recipe")
    # I-053 / I-055 / I-046: the recipe must restate a legal condition (one validator, I-060)
    # and, where the Game snapshot recorded one, the same condition the game was created under.
    condition = GameLoopCondition.from_mapping(coordinator)
    try:
        condition.validate()
    except InvalidGameLoopCondition as exc:
        raise ResumeError(f"resume recipe carries an invalid game-loop condition: {exc}") from exc
    if game.con_reply_mode is not None and (
        game.con_reply_mode != condition.con_reply_mode
        or game.reply_max_points != condition.reply_max_points
    ):
        raise ResumeError("resume recipe Con-reply condition conflicts with the Game snapshot")
    if game.con_mode is not None and (
        game.con_mode != condition.con_mode
        or game.con_challengers_per_argument != condition.con_challengers_per_argument
    ):
        raise ResumeError("resume recipe Con condition does not match the persisted game")
    # I-083: the Diplomacy condition is checked like the other two; a recipe that restates a
    # different round bound would drive a game under a condition its snapshot never recorded.
    if game.diplomacy_mode is not None and (
        game.diplomacy_mode != condition.diplomacy_mode
        or game.diplomacy_rounds_per_turn != condition.diplomacy_rounds_per_turn
    ):
        raise ResumeError("resume recipe Diplomacy condition does not match the persisted game")
    # T-074 / D-091: a legacy Game (null) ran the released ``none``; an absent recipe key means
    # ``none`` too. A consequence game must also carry the resolver recipe that composes it.
    if (game.consequence_mode or "none") != condition.consequence_mode:
        raise ResumeError("resume recipe Consequence condition does not match the persisted game")
    if condition.consequence_mode != "none":
        resolver = recipe.get("consequence")
        if not isinstance(resolver, dict) or not _CONSEQUENCE_FIELDS.issubset(resolver):
            raise ResumeError("resume recipe has no complete consequence-resolver section")
    # I-032: a sealed inference profile must still be a valid one. A legacy recipe without the
    # key resumes under the released flat settings.
    sealed_profile = recipe.get("inference_profile")
    if sealed_profile is not None:
        _validate_sealed_inference_profile(sealed_profile)
    return game, manifest, actors


# agents.md §19 vocabulary, shared with ``agents.player`` through the framework-neutral contracts
# module — ``app`` still imports nothing from the agent layer (TRD §4).
_INFERENCE_PROFILE_SCHEMA = INFERENCE_PROFILE_SCHEMA
_PLAYER_INFERENCE_PHASES = frozenset(PLAYER_INFERENCE_PHASES)
_INFERENCE_SETTING_KEYS = frozenset(INFERENCE_SETTING_KEYS)
_REASONING_EFFORTS = frozenset(REASONING_EFFORTS)


def _inference_entry_ok(entry: object) -> bool:
    if not isinstance(entry, dict) or not set(entry) <= _INFERENCE_SETTING_KEYS:
        return False
    effort = entry.get("reasoning_effort")
    max_tokens = entry.get("max_tokens")
    return (effort is None or effort in _REASONING_EFFORTS) and (
        max_tokens is None
        or (isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens >= 1)
    )


def _validate_sealed_inference_profile(document: object) -> None:
    """Structural twin of ``agents.inference_profile.InferenceProfile.from_document(sealed=True)``.

    ``app`` never imports the agent layer (TRD §4), so the sealed-recipe shape is restated here;
    ``tests/test_model_inference_profile.py`` holds both sides to the same documents.
    """
    if (
        not isinstance(document, dict)
        or document.get("schema") != _INFERENCE_PROFILE_SCHEMA
        or not isinstance(document.get("name"), str)
        or not document["name"].strip()
        or not set(document) <= {"schema", "name", "phases", "umpire"}
        or not isinstance(document.get("phases", {}), dict)
        or not set(document.get("phases", {})) <= _PLAYER_INFERENCE_PHASES
        or not all(_inference_entry_ok(entry) for entry in document.get("phases", {}).values())
        or not _inference_entry_ok(document.get("umpire", {}))
    ):
        raise ResumeError("resume recipe contains an invalid inference profile")
    if document["name"] == "uniform" and (
        any(v is not None for e in document.get("phases", {}).values() for v in e.values())
        or any(v is not None for v in document.get("umpire", {}).values())
    ):
        raise ResumeError("resume recipe inference profile 'uniform' is not flat")


def describe(engine: Engine, game_id: uuid.UUID) -> ResumeDescriptor:
    """Return credential-free persisted resume provenance after strict compatibility checks."""
    with write_session_scope(engine) as session:
        game, manifest, actors = _validate(session, game_id)
        return ResumeDescriptor(
            game_id=game.id,
            status=game.status,
            actors=actors,
            manifest=dict(manifest.manifest),
            manifest_sha256=manifest.manifest_sha256,
        )


def rotate_seat_tokens(engine: Engine, game_id: uuid.UUID) -> dict[str, str]:
    """Atomically replace all Seat hashes and disclose the fresh raw tokens exactly once."""
    with write_session_scope(engine) as session:
        game, _, actors = _validate(session, game_id)
        seats = list(
            session.exec(select(Seat).where(Seat.game_id == game_id).order_by(Seat.actor_id)).all()
        )
        if {seat.actor_id for seat in seats} != set(actors):
            raise ResumeError("game does not have exactly one Seat for every Actor")
        old_hashes = {seat.token_hash for seat in seats}
        raw: dict[str, str] = {}
        new_hashes: set[str] = set()
        for seat in seats:
            while True:
                token = new_seat_token()
                digest = hash_token(token)
                if digest not in old_hashes and digest not in new_hashes:
                    break
            raw[seat.actor_id] = token
            new_hashes.add(digest)
            seat.token_hash = digest
            session.add(seat)
        if game.status == GameStatus.paused:
            game.status = GameStatus.running
            session.add(game)
        session.flush()
        return raw
