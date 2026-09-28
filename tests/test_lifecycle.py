"""T-040 acceptance: the agent lifecycle loop (`agents/lifecycle.py`, agents.md §3).

`observe → (Python decides if input is required) → invoke the model only if so → allow bounded
tool use → re-observe → repeat`. The key discipline: **Python**, from the server-provided
``requires_input``/``available_actions``, decides whether to spend a model call — the model is
never asked "do I need to act?". The loop reacts to authoritative server state and never
reimplements the state machine, so one agent participates correctly across every phase without
being told which phase it is in.

Offline, no key: real :class:`pydantic_ai.Agent`s driven by deterministic fake models.
"""

from __future__ import annotations


from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import Engine

from agents.lifecycle import CycleOutcome, LifecycleRunner
from agents.player import PlayerConfig, ReferencePlayer
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from app import game_service
from tests.agent_fakes import _response_index, default_policy, model_from_policy
from tests.api_helpers import (
    ACTORS,
    client,
    seed,
    start_to_argument,
    start_to_cons,
    start_to_diplomacy,
)


def _runner(
    engine, creation, actor, *, model, sleep=None, max_activations=200, guardrails=None
) -> LifecycleRunner:
    tools = PlayerTools(
        client(engine),
        game_id=str(creation.game_id),
        token=creation.seat_tokens[actor],
    )
    name = tools.observe().data["you"]["name"]
    from agents.player import GuardrailConfig

    player = ReferencePlayer(
        tools,
        actor_id=actor,
        actor_name=name,
        model=model,
        config=PlayerConfig(
            model="fake/deterministic", activation_mode="tool_loop",
            guardrails=guardrails or GuardrailConfig(),
        ),
    )
    return LifecycleRunner(player, sleep=sleep, max_activations=max_activations)


# --- Python gates the model: invoke only when input is required ---------------------------


def test_model_invoked_only_when_input_is_required(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def spy(messages, info) -> ModelResponse:
        calls["n"] += 1
        return ModelResponse(parts=[TextPart(content="done")])

    runner = _runner(db_engine, creation, "arkania", model=FunctionModel(spy))

    outcome = runner.step()  # diplomacy: arkania must ready → requires_input True
    assert isinstance(outcome, CycleOutcome)
    assert outcome.requires_input is True
    assert outcome.acted is True
    assert calls["n"] >= 1


def test_waiting_actor_causes_no_model_invocation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania active in ARGUMENT; belvar is waiting
    calls = {"n": 0}
    waited: list[bool] = []

    def spy(messages, info) -> ModelResponse:
        calls["n"] += 1
        return ModelResponse(parts=[TextPart(content="done")])

    runner = _runner(
        db_engine, creation, "belvar", model=FunctionModel(spy), sleep=lambda: waited.append(True)
    )
    outcome = runner.step()

    assert outcome.requires_input is False
    assert outcome.acted is False
    assert outcome.activation is None
    assert calls["n"] == 0  # not one wasted model call while waiting
    assert waited == [True]  # it polled instead


# --- correct participation in each phase (no phase instruction supplied) ------------------


def test_active_argument_actor_is_invoked_and_submits(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    runner = _runner(db_engine, creation, "arkania", model=model_from_policy(default_policy))

    outcome = runner.step()
    assert outcome.acted is True
    assert "make_argument" in outcome.activation.tool_call_names

    ctx = runner.observe()
    assert ctx["current_argument"] is not None
    assert ctx["current_argument"]["actor_id"] == "arkania"


def test_eligible_cons_actor_completes_via_con_or_pass(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_cons(db_engine, creation)  # arkania's Argument open_for_cons; belvar eligible
    runner = _runner(db_engine, creation, "belvar", model=model_from_policy(default_policy))

    outcome = runner.step()
    assert outcome.acted is True
    assert "pass_con" in outcome.activation.tool_call_names  # explicit pass, never inactivity

    # belvar has completed its Con participation → the server no longer awaits its input.
    ctx = runner.observe()
    assert ctx["requires_input"] is False
    assert "make_con" not in ctx["available_actions"]


def test_diplomacy_actor_can_act_and_ready(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    runner = _runner(db_engine, creation, "arkania", model=model_from_policy(default_policy))

    outcome = runner.step()
    assert outcome.acted is True
    assert "ready" in outcome.activation.tool_call_names

    ctx = runner.observe()
    assert ctx["requires_input"] is False
    assert "ready" not in ctx["available_actions"]  # already ready


def test_lifecycle_follows_phase_changes_by_reobserving(db_engine: Engine) -> None:
    """One runner, one policy: it does the diplomacy thing, then — after the phase changes —
    the ARGUMENT thing, purely because it re-observed authoritative state."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    runner = _runner(db_engine, creation, "arkania", model=model_from_policy(default_policy))

    first = runner.step()  # diplomacy
    assert "ready" in first.activation.tool_call_names

    # The other Actors ready (server stand-in) → diplomacy closes → ARGUMENT, arkania active.
    for other in ("belvar", "cestia"):
        game_service.ready(db_engine, creation.game_id, other)

    second = runner.step()  # same runner, re-observes: now it must argue
    assert "make_argument" in second.activation.tool_call_names


def test_no_external_phase_instruction_is_hardcoded(db_engine: Engine) -> None:
    """The player is never handed a "you are now in phase X, do Y" instruction.

    The activation prompt carries the phase only as *data* inside the decision packet; the same
    neutral policy chose ready / argue / pass above from ``available_actions`` alone.
    """
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    runner = _runner(db_engine, creation, "arkania", model=model_from_policy(default_policy))

    ctx = runner.observe()
    prompt = runner.player.build_activation_prompt(runner.player.build_packet(ctx))
    lowered = prompt.lower()
    assert "now perform" not in lowered
    assert "current phase instruction" not in lowered
    # activate() takes no phase argument — the loop supplies context, not a phase directive.
    import inspect

    params = set(inspect.signature(runner.player.activate).parameters) - {"self"}
    assert "phase" not in params


def test_temporary_history_discarded_before_next_activation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    first_counts: list[int] = []

    def recording(messages, info) -> ModelResponse:
        if _response_index(messages) == 0:
            first_counts.append(len(messages))
        return ModelResponse(parts=[TextPart(content="done")])

    runner = _runner(db_engine, creation, "arkania", model=FunctionModel(recording))
    runner.step()
    runner.step()
    runner.step()

    assert first_counts == [1, 1, 1]  # each activation is independent; no accumulation


# --- concurrency: stale/recoverable rejection returns to observation ----------------------


def test_stale_recoverable_rejection_returns_to_observation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)

    belvar = _runner(db_engine, creation, "belvar", model=model_from_policy(default_policy))
    stale_ctx = belvar.observe()  # believes it may Con arg (phase CONS)
    assert "make_con" in stale_ctx["available_actions"]

    # The world moves on: both opponents pass (server stand-in) → CONS closes → ADJUDICATION.
    for opponent in ("belvar", "cestia"):
        game_service.pass_con(db_engine, creation.game_id, argument_id=arg.id, actor_id=opponent)

    # Activating on the STALE context: the tool call is rejected with a structured error the
    # model can see; it does not raise and does not corrupt state.
    result = belvar.player.activate(stale_ctx)
    assert result is not None  # completed normally despite the rejection

    # Re-observing shows the authoritative phase; nothing was advanced or corrupted by the player.
    fresh = belvar.observe()
    assert fresh["phase"] == "adjudication"
    outcome = belvar.step()  # deterministic Python: no input required now → no model call
    assert outcome.acted is False


# --- three agents through a representative multi-phase cycle ------------------------------


def test_three_players_orchestrated_through_a_full_turn(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    umpire = deterministic_umpire()
    runners = {
        a: _runner(db_engine, creation, a, model=model_from_policy(default_policy))
        for a in ACTORS
    }
    acted_by = {a: 0 for a in ACTORS}

    for _ in range(300):
        ctx = runners["arkania"].observe()
        if ctx["status"] == "complete" or ctx["turn"] >= 2:
            break
        if ctx["phase"] == "adjudication":
            game_service.resolve_adjudication(db_engine, creation.game_id, umpire)
            game_service.advance_after_resolution(db_engine, creation.game_id)
            continue
        progressed = False
        for actor in ACTORS:
            outcome = runners[actor].step()
            if outcome.acted:
                acted_by[actor] += 1
                progressed = True
        if not progressed:
            break

    assert runners["arkania"].observe()["turn"] == 2  # a full turn elapsed
    assert all(acted_by[a] > 0 for a in ACTORS)  # every agent participated


# --- the converted stall seam: a no-progress loop is recovered, not bare-raised (T-041) ----


def test_no_progress_stall_is_recovered_not_raised(db_engine: Engine) -> None:
    """The old ``max_activations`` bare-raise is now a no-progress stall detector feeding recovery.

    A player that always "acts" (saves a Note) but never readies used to hit an
    ``ActivationLimitExceeded`` raise. It now hits the per-opportunity stall bound, the runner
    records the failure and takes the legal minimal completion (auto-``ready``), and the game
    keeps moving — one player can no longer hang it (agents.md §18)."""
    from agents.player import GuardrailConfig

    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def never_ready(packet, tool_names, idx):
        return (
            [ToolCallPart(tool_name="save_note", args={"text": "still going"})]
            if idx == 0
            else [TextPart(content="loop")]
        )

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(never_ready),
        guardrails=GuardrailConfig(max_activations_per_opportunity=3),
        sleep=lambda: None,
    )
    outcomes = runner.run(max_cycles=20)  # must terminate, never raise

    assert runner.failures, "the stall was recorded, not swallowed"
    assert runner.failures[-1].recovery_action == "ready"
    assert any(o.failure is not None for o in outcomes)
    # arkania was auto-readied; the phase no longer waits on it.
    assert "ready" not in runner.observe()["available_actions"]
