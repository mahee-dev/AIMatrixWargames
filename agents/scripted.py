"""Deterministic ScriptedPlayers and the offline game driver (T-025, WBS 2.9.1–2.9.2).

Three deterministic Actor policies plus an engine-neutral driver that runs the finished offline
engine (``LOBBY → … → COMPLETE``) with no external model, so the canonical fixture and the T-026
integration test complete with no ``OPENROUTER_API_KEY`` (testing.md §3). This layer lives under
``agents/`` and — like the whole package — imports **no** agent framework (AGENTS.md §6 / TRD §4):
only ``app`` and the standard library. It is deliberately separate from ``agents.player``, which
is reserved for the later PydanticAI reference player (T-041).

**Division of authority (the non-negotiable rule).** A :class:`ScriptedPlayer` decides only its
*own* action content — whether/what to message, its Argument's ``action``/``desired_effect``/
``pros``, and its Con reasons — as a pure function of its own authorised
:class:`~app.visibility.ActorView`. It never chooses the next Actor, phase, turn, completion, the
RNG roll or the adjudication outcome. The server owns all of that (INV-RNG-001, INV-GAME-004/005):
:func:`run_deterministic_game` reads authoritative state to know *whose turn it is*, then drives
the game exclusively through :mod:`app.game_service` (``resolve_adjudication`` +
``advance_after_resolution`` are the server calls, not player calls). No second state machine.

**Deterministic adjudication configuration.** :func:`deterministic_umpire` returns a
:class:`~agents.umpire.FakeUmpire` whose responder maps each ``(turn, Actor)`` to a fixed
probabilistic proposal. Every adjudication is probabilistic, so the server draws exactly one roll
per Argument (18 in a full game) — this is what makes the "RNG sequence matches fixture"
assertion meaningful rather than vacuous. A single deterministic supersession at the final turn
exercises INV-EFFECT-004 (deactivate, never delete). The responder and the golden-schedule
helpers here (:func:`effect_description`, :data:`SUPERSEDE_AT`, :data:`SUPERSEDE_TARGET`) are the
fixture's expected outputs, consumed by ``tests/test_integration_six_turn.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import Engine

from agents.consequence import FakeConsequenceResolver
from agents.umpire import FakeUmpire
from app import game_service, visibility
from app.consequences import ConsequenceContext, ConsequenceProposal, ProposedConsequence
from app.database import session_scope
from app.models import AdjudicationMode, Game, GamePhase, GameStatus
from app.umpire import AdjudicationContext, AdjudicationProposal
from app.visibility import ActorView


# --- Player decision values -------------------------------------------------------------


@dataclass(frozen=True)
class OutgoingMessage:
    """One Message a player chooses to send (content only; the server derives the turn)."""

    text: str
    public: bool
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class ArgumentSpec:
    """The content of a primary Argument a player chooses to submit."""

    action: str
    desired_effect: str
    pros: tuple[str, ...]


# --- ScriptedPlayer ---------------------------------------------------------------------


class ScriptedPlayer:
    """A deterministic per-Actor policy over the Actor's own authorised view.

    Constructed with only its ``actor_id`` and the game's ``turn_order`` (used solely to pick a
    fixed private-Message recipient — the next Actor in the ring). Every decision is a pure
    function of the :class:`~app.visibility.ActorView` passed in, so the same view always yields
    the same decision (no RNG, no wall-clock, no hidden mutable state).
    """

    def __init__(self, actor_id: str, turn_order: Sequence[str]) -> None:
        self.actor_id = actor_id
        self._turn_order = tuple(turn_order)

    def _next_actor(self) -> str:
        index = self._turn_order.index(self.actor_id)
        return self._turn_order[(index + 1) % len(self._turn_order)]

    def diplomacy_messages(self, view: ActorView) -> list[OutgoingMessage]:
        """One private Message to the next Actor, referencing this Actor's private information.

        Satisfies two §18 requirements at once: "send at least one Message" and "use at least one
        private information element". The secret is read from the Actor's *own* authorised view —
        choosing to disclose it to one recipient is legitimate play, and the Message stays private
        to sender + recipient, so a third Actor never sees it (verified in T-026).
        """
        secret = view.private_information[0] if view.private_information else "no briefing"
        recipient = self._next_actor()
        text = f"{view.name} confides to {recipient}: {secret}"
        return [OutgoingMessage(text=text, public=False, recipients=(recipient,))]

    def is_ready(self, view: ActorView) -> bool:  # noqa: ARG002 — signature parity with peers
        """Always ready to close diplomacy — the scripted game has no negotiation to prolong."""
        return True

    def argument(self, view: ActorView) -> ArgumentSpec:
        """A structurally valid primary Argument grounded in the Actor's own objective."""
        objective = view.objectives[0] if view.objectives else "advance our position"
        return ArgumentSpec(
            action=f"{view.name} acts to advance its objectives",
            desired_effect=f"{view.actor_id} advances its position",
            pros=(objective,),
        )

    def con_reason(self, view: ActorView, owner_id: str) -> str | None:
        """A deterministic Con reason against another Actor's Argument (never one's own).

        Returning a string makes the driver submit one Con and then pass; returning ``None`` would
        make it pass without a Con. This policy always contests, so both ``con_submitted`` and
        ``con_passed`` are exercised (§18 "submit/pass Cons").
        """
        return f"{view.actor_id} questions the wisdom of {owner_id}'s move"


def build_scripted_players(turn_order: Sequence[str]) -> dict[str, ScriptedPlayer]:
    """Build one :class:`ScriptedPlayer` per Actor id in ``turn_order``."""
    return {actor_id: ScriptedPlayer(actor_id, turn_order) for actor_id in turn_order}


# --- Deterministic adjudication configuration (the fixture's "known FakeUmpire results") --

# A full game adjudicates in (turn, turn-order position) order. The final adjudication supersedes
# the first-turn Effect of the same Actor, exercising deactivate-never-delete (INV-EFFECT-004).
# Both constants read as ``(turn, actor_id)``.
SUPERSEDE_AT: tuple[int, str] = (6, "cestia")
SUPERSEDE_TARGET: tuple[int, str] = (1, "cestia")


def effect_description(actor_id: str, turn: int) -> str:
    """The deterministic success-Effect description created when ``actor_id`` resolves on ``turn``.

    Unique per ``(actor_id, turn)`` and disjoint from the fixture's starting-Effect descriptions,
    so the golden active-Effect set is unambiguous.
    """
    return f"{actor_id} advances its position (turn {turn})"


def superseded_effect_description() -> str:
    """The description of the one Effect deterministically superseded during a full game."""
    target_turn, target_actor = SUPERSEDE_TARGET
    return effect_description(target_actor, target_turn)


def _responder(context: AdjudicationContext) -> AdjudicationProposal:
    """Map one adjudication context to its fixed proposal (probabilistic, always succeeds).

    ``probability = 1.0`` means the server's roll (``roll <= probability``) always yields success,
    yet a real RNG draw is still consumed and recorded (INV-RNG-001/002) — the outcome is
    deterministic *and* the draw sequence is exercised. The Umpire proposes description strings
    only; it never draws, decides the outcome, or writes Effects (agents.md §26).
    """
    actor_id = context.argument["actor_id"]
    turn = context.turn
    supersedes: list[uuid.UUID] = []
    if (turn, actor_id) == SUPERSEDE_AT:
        target = superseded_effect_description()
        supersedes = [
            uuid.UUID(effect["id"])
            for effect in context.relevant_effects
            if effect["description"] == target
        ]
    return AdjudicationProposal(
        mode=AdjudicationMode.probabilistic,
        probability=1.0,
        rationale=f"{actor_id} presses its case on turn {turn}",
        success_effects=[effect_description(actor_id, turn)],
        failure_effects=[f"{actor_id} is rebuffed on turn {turn}"],
        success_supersedes_effect_ids=supersedes,
    )


def deterministic_umpire() -> FakeUmpire:
    """The deterministic in-process Umpire for the canonical fixture (offline, framework-free)."""
    return FakeUmpire(responder=_responder)


def _aggregate_consequence(context: ConsequenceContext) -> ConsequenceProposal:
    """One public aggregate Effect per turn, citing every outcome of the turn (T-074, D-091)."""
    if not context.outcomes:
        return ConsequenceProposal(effects=[])
    actors = ", ".join(outcome["actor_id"] for outcome in context.outcomes)
    return ConsequenceProposal(
        effects=[
            ProposedConsequence(
                description=f"Turn {context.turn}: the combined moves of {actors} raise regional tension.",
                rationale="Every Actor acted this turn; together the moves shift the situation.",
                evidence=[{"kind": "event", "id": o["event_id"]} for o in context.outcomes],
            )
        ]
    )


def deterministic_consequence_resolver() -> FakeConsequenceResolver:
    """The deterministic, offline consequence resolver for ``bounded_effects_v1`` fixture games."""
    return FakeConsequenceResolver(responder=_aggregate_consequence)


# --- The driver -------------------------------------------------------------------------


def _read_game(engine: Engine, game_id: uuid.UUID) -> Game:
    """Read authoritative game state (a detached snapshot) to decide what to drive next."""
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise game_service.GameNotFound(f"game {game_id!r} does not exist")
        session.expunge(game)
        return game


def _actor_view(engine: Engine, game_id: uuid.UUID, actor_id: str) -> ActorView:
    with session_scope(engine) as session:
        return visibility.get_actor_view(session, game_id, actor_id)


def _run_diplomacy(
    engine: Engine,
    game_id: uuid.UUID,
    players: Mapping[str, ScriptedPlayer],
    turn_order: Sequence[str],
) -> None:
    """Send every player's Messages, then ready every player so the server closes diplomacy.

    All Messages are sent *before* any readiness so the private-Message readiness-reset rule
    (§16 / TEST-PHASE-003) never fires against a player that has just become ready — this driver
    stays deterministic without depending on that interaction. The final ``ready`` closes the
    phase; the server (not the driver) sets the phase and the first active Actor.
    """
    for actor_id in turn_order:
        view = _actor_view(engine, game_id, actor_id)
        player = players[actor_id]
        for message in player.diplomacy_messages(view):
            game_service.send_message(
                engine,
                game_id,
                sender_actor_id=actor_id,
                text=message.text,
                public=message.public,
                recipients=list(message.recipients),
            )
    for actor_id in turn_order:
        if players[actor_id].is_ready(_actor_view(engine, game_id, actor_id)):
            game_service.ready(engine, game_id, actor_id)


def _run_argument_cycle(
    engine: Engine,
    game_id: uuid.UUID,
    players: Mapping[str, ScriptedPlayer],
    turn_order: Sequence[str],
    owner_id: str,
    umpire,  # noqa: ANN001 — app.umpire.Umpire
) -> None:
    """Drive one Actor's full cycle: Argument → Cons (opponents con+pass) → resolve → advance.

    ``owner_id`` is the authoritative active Actor the driver read from state — it is not chosen
    here. Each service call revalidates phase/eligibility on fresh state; the Argument id is
    captured from ``make_argument`` and used for the opponents' Cons (no re-derivation of "the
    current Argument").
    """
    view = _actor_view(engine, game_id, owner_id)
    spec = players[owner_id].argument(view)
    result = game_service.make_argument(
        engine,
        game_id,
        actor_id=owner_id,
        action=spec.action,
        desired_effect=spec.desired_effect,
        pros=list(spec.pros),
    )
    argument_id = result.id

    max_cons = _read_game(engine, game_id).max_cons_per_actor
    for opponent in [a for a in turn_order if a != owner_id]:
        opponent_view = _actor_view(engine, game_id, opponent)
        reason = players[opponent].con_reason(opponent_view, owner_id)
        if reason is not None:
            game_service.make_con(
                engine, game_id, argument_id=argument_id, actor_id=opponent, reason=reason
            )
            if max_cons == 1:
                continue  # I-030 / D-043: the final allowed Con already completed participation
        game_service.pass_con(
            engine, game_id, argument_id=argument_id, actor_id=opponent
        )

    # Server-owned resolution and advancement (never player calls).
    game_service.resolve_adjudication(engine, game_id, umpire)
    game_service.advance_after_resolution(engine, game_id)


def run_deterministic_game(
    engine: Engine,
    game_id: uuid.UUID,
    players: Mapping[str, ScriptedPlayer],
    umpire,  # noqa: ANN001 — app.umpire.Umpire
    *,
    max_iterations: int | None = None,
    consequence_resolver=None,  # noqa: ANN001 — app.consequences.ConsequenceResolver
) -> None:
    """Run a *started* game to completion with the given players and Umpire.

    Reads authoritative state each iteration and dispatches by phase: diplomacy runs the
    Message/ready sub-phase; an ``argument`` phase runs the active Actor's full cycle (which
    internally reaches adjudication and advancement). The loop therefore only ever observes
    ``diplomacy`` or ``argument`` at its top — plus, under ``consequence_mode: bounded_effects_v1`` (D-091),
    ``consequences``, where it runs the server's one pass with ``consequence_resolver`` (unless the
    turn's pass already committed) and then advances. ``max_iterations`` is a safety bound against a
    non-terminating engine bug — the deterministic fixture is bounded regardless.
    """
    game = _read_game(engine, game_id)
    if game.status is not GameStatus.running:
        raise game_service.InvalidPhase(
            f"driver requires a running game; game is {game.status.value}"
        )
    turn_order = list(game.turn_order)
    # Generous bound: each turn is one diplomacy pass + one cycle per Actor per Argument round
    # (D-094: ``primary_arguments_per_actor`` rounds).
    limit = max_iterations if max_iterations is not None else (game.max_turns + 1) * (
        len(turn_order) * game.primary_arguments_per_actor + 3
    )

    for _ in range(limit):
        game = _read_game(engine, game_id)
        if game.status is GameStatus.complete:
            return
        if game.phase is GamePhase.diplomacy:
            _run_diplomacy(engine, game_id, players, turn_order)
        elif game.phase is GamePhase.argument:
            if game.active_actor_id is None:
                raise game_service.NotActiveActor(
                    f"game {game_id!r} is in argument with no active Actor"
                )
            _run_argument_cycle(
                engine, game_id, players, turn_order, game.active_actor_id, umpire
            )
        elif game.phase is GamePhase.consequences:
            # Server-owned §21 Option B pass, then §22 completion — never a player call.
            if game_service.consequence_pending(engine, game_id):
                if consequence_resolver is None:
                    raise game_service.InvalidPhase(
                        "bounded_effects_v1 game reached consequences with no resolver"
                    )
                game_service.resolve_consequences(engine, game_id, consequence_resolver)
            game_service.advance_after_resolution(engine, game_id)
        else:
            # cons/adjudication are transient within a cycle; the loop should never observe them.
            raise game_service.InvalidPhase(
                f"driver observed unexpected top-level phase {game.phase.value}"
            )

    raise RuntimeError(
        f"deterministic game {game_id!r} did not complete within {limit} iterations"
    )
