"""T-041 acceptance: agent guardrails and stall recovery (`agents/player.py`, `agents/lifecycle.py`).

The autonomous player must be safe to run unattended (agents.md §18 / TRD §35): bounded in model
iterations, tool calls and Messages; text/size limited; resilient to one transient provider
failure; and — the exit condition — unable to hang a game. A deliberately failing or stalling
player is stopped, recovered where the server permits (auto-`ready` in diplomacy, auto-`pass_con`
in Cons), and has its failure recorded — never silently swallowed, never a fabricated Argument.

Offline, no key: real :class:`pydantic_ai.Agent`s driven by deliberately misbehaving
:class:`FunctionModel` policies.
"""

from __future__ import annotations

import threading

import pytest
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine
from sqlmodel import select

from agents.lifecycle import FAIL_STALL, AgentFailure, LifecycleRunner
from agents.player import (
    ActivationFailed,
    GuardrailConfig,
    PlayerConfig,
    ReferencePlayer,
    check_argument,
    check_message,
    check_note,
    check_payload,
)
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from app import game_service
from app.database import session_scope
from app.models import ResourceAccess
from tests.agent_fakes import default_policy, extract_packet, model_from_policy
from tests.api_helpers import (
    ACTORS,
    client,
    seed,
    start_to_argument,
    start_to_cons,
    start_to_diplomacy,
)


def _player(engine, creation, actor, *, model, guardrails=None) -> ReferencePlayer:
    tools = PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor]
    )
    name = tools.observe().data["you"]["name"]
    config = PlayerConfig(
        model="fake/deterministic", activation_mode="tool_loop",
        guardrails=guardrails or GuardrailConfig(),
    )
    return ReferencePlayer(
        tools, actor_id=actor, actor_name=name, model=model, config=config
    )


def _runner(engine, creation, actor, *, model, guardrails=None, sleep=None) -> LifecycleRunner:
    player = _player(engine, creation, actor, model=model, guardrails=guardrails)
    return LifecycleRunner(player, sleep=sleep)


def _memory(text: str = "") -> dict:
    return {
        "commitments_made": [],
        "commitments_or_offers_received": [],
        "outstanding_offers_and_negotiations": [],
        "red_lines_and_constraints": [],
        "uncertain_or_disputed_claims": [],
        "relationships_and_likely_coalitions": [],
        "strategic_assessment": text,
        "intended_next_action": "",
    }


# --- 1. per-activation ceilings: a looping activation is stopped ---------------------------


def test_per_activation_tool_call_ceiling_stops_a_looping_activation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def loop(packet, tool_names, idx):
        # Always call a real tool, never finish — an intentionally unbounded single activation.
        return [ToolCallPart(tool_name="update_strategic_memory", args=_memory("again"))]

    p = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(loop),
        guardrails=GuardrailConfig(max_tool_calls=3, max_llm_iterations=50),
    )
    with pytest.raises(ActivationFailed) as ei:
        p.activate()
    assert ei.value.category == "guardrail"


def test_per_activation_iteration_ceiling_stops_a_looping_activation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def loop(packet, tool_names, idx):
        return [ToolCallPart(tool_name="update_strategic_memory", args=_memory("again"))]

    p = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(loop),
        guardrails=GuardrailConfig(max_llm_iterations=2, max_tool_calls=50),
    )
    with pytest.raises(ActivationFailed) as ei:
        p.activate()
    assert ei.value.category == "guardrail"


# --- 2. text/size limits: structured tool errors, not exceptions ---------------------------


def test_text_and_size_validators_reject_over_limit_inputs() -> None:
    g = GuardrailConfig(
        max_message_chars=10,
        max_note_chars=10,
        max_pros=2,
        max_argument_action_chars=10,
        max_desired_effect_chars=10,
        max_tool_payload_bytes=40,
    )
    # Under-limit → no violation.
    assert check_message("hello", g) is None
    assert check_note("hello", g) is None
    assert check_argument("go", "effect", ["a", "b"], g) is None

    # Message length.
    err = check_message("x" * 11, g)
    assert err["ok"] is False and err["error"]["code"] == "guardrail_message_too_long"
    # Note length.
    assert check_note("y" * 11, g)["error"]["code"] == "guardrail_note_too_long"
    # Pros count.
    assert (
        check_argument("go", "eff", ["a", "b", "c"], g)["error"]["code"]
        == "guardrail_too_many_pros"
    )
    # Argument action length.
    assert (
        check_argument("x" * 11, "eff", ["a"], g)["error"]["code"]
        == "guardrail_argument_action_too_long"
    )
    # Payload size.
    big = check_payload({"text": "z" * 100}, g)
    assert big["error"]["code"] == "guardrail_payload_too_large"


def test_oversized_tool_call_returns_structured_error_visible_to_model(db_engine: Engine) -> None:
    """Overlong strategic memory is a structured error the model sees (agents.md §18.1)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def oversize_then_inspect(packet, tool_names, idx):
        if idx == 0:
            return [ToolCallPart(
                tool_name="update_strategic_memory", args=_memory("N" * 100)
            )]
        # The tool return from idx 0 is now in the packet's message history; capture it.
        return [TextPart(content="corrected")]

    # Wrap so we can inspect the tool-return the model received (a real ModelMessage stream).
    tool_returns: list[str] = []

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for m in messages:
            for part in getattr(m, "parts", []):
                content = getattr(part, "content", None)
                # A tool return's content is the returned dict; stringify to inspect it.
                if content is not None and "guardrail_note_too_long" in str(content):
                    tool_returns.append(str(content))
        packet = extract_packet(messages)
        idx = sum(1 for x in messages if isinstance(x, ModelResponse))
        return ModelResponse(parts=oversize_then_inspect(packet, [], idx))

    p = _player(
        db_engine,
        creation,
        "arkania",
        model=FunctionModel(run),
        guardrails=GuardrailConfig(max_note_chars=10),
    )
    result = p.activate()  # does not raise — the violation is data, not an exception
    assert result is not None
    assert tool_returns, "the model saw the structured guardrail error and could self-correct"
    # The refused staged update did not replace the one server-backed memory.
    assert p.tools.begin_activation().data["strategic_memory"]["strategic_assessment"] == ""


# --- 3. one retry on transient, none on deterministic --------------------------------------


def test_exactly_one_retry_on_transient_model_failure(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def flaky(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModelHTTPError(status_code=503, model_name="m", body="overloaded")
        return ModelResponse(parts=[TextPart(content="ok")])

    p = _player(db_engine, creation, "arkania", model=FunctionModel(flaky))
    result = p.activate()  # transient → retried once → succeeds
    assert result is not None
    assert calls["n"] == 2  # exactly one retry


def test_unexpected_model_output_gets_the_single_retry(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def malformed_once(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise UnexpectedModelBehavior("provider returned unusable tool output")
        return ModelResponse(parts=[TextPart(content="ok")])

    p = _player(db_engine, creation, "arkania", model=FunctionModel(malformed_once))
    assert p.activate() is not None
    assert calls["n"] == 2


def test_no_retry_on_deterministic_model_failure(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def dead(messages, info) -> ModelResponse:
        calls["n"] += 1
        raise ModelHTTPError(status_code=401, model_name="m", body="bad key")

    p = _player(db_engine, creation, "arkania", model=FunctionModel(dead))
    with pytest.raises(ActivationFailed) as ei:
        p.activate()
    assert ei.value.category == "deterministic"
    assert calls["n"] == 1  # no retry for a deterministic failure


# --- 4. per-phase message cap tracked across activations -----------------------------------


def test_per_phase_message_cap_stops_further_sends_across_activations(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def send_one(packet, tool_names, idx):
        # One public Message per activation, then finish — never ready (stays in diplomacy).
        if idx == 0 and "send_message" in packet.get("available_actions", []):
            return [
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": [], "public": True, "text": "hello"},
                )
            ]
        return [TextPart(content="done")]

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(send_one),
        guardrails=GuardrailConfig(max_messages_per_phase=2, max_activations_per_opportunity=50),
    )
    for _ in range(4):
        runner.step()

    # Only the cap's worth of Messages actually reached the server, despite 4 activations trying.
    msgs = runner.player.tools.read_messages().data["messages"]
    mine = [m for m in msgs if m.get("sender_actor_id") == "arkania"]
    assert len(mine) == 2


def test_message_cap_holds_when_one_response_emits_two_sends(db_engine: Engine) -> None:
    """Two send_message calls in ONE model response must not both pass a cap of 1 (I-021).

    PydanticAI executes the tool calls of a single response concurrently, so a non-atomic
    check→send→count lets both through. Pre-fix this fails (2 Messages reach the server) on
    practically every run; post-fix it passes deterministically because the lock serialises
    the whole send.
    """
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def two_sends(packet, tool_names, idx):
        if idx == 0:
            return [
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": [], "public": True, "text": "first"},
                ),
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": [], "public": True, "text": "second"},
                ),
            ]
        return [TextPart(content="done")]

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(two_sends),
        guardrails=GuardrailConfig(max_messages_per_phase=1, max_activations_per_opportunity=50),
    )
    outcome = runner.step()

    msgs = runner.player.tools.read_messages().data["messages"]
    mine = [m for m in msgs if m.get("sender_actor_id") == "arkania"]
    assert len(mine) == 1
    assert outcome.activation is not None and outcome.activation.messages_sent == 1


def test_remaining_message_budget_in_packet_tracks_runner(db_engine: Engine) -> None:
    """Across activations the packet's remaining budget counts down with the runner (I-015)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen: list[int] = []

    def send_one(packet, tool_names, idx):
        if idx == 0:
            seen.append(packet["limits"]["messages_remaining_this_phase"])
            if "send_message" in packet.get("available_actions", []):
                return [
                    ToolCallPart(
                        tool_name="send_message",
                        args={"recipients": [], "public": True, "text": "hello"},
                    )
                ]
        return [TextPart(content="done")]

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(send_one),
        guardrails=GuardrailConfig(max_messages_per_phase=2, max_activations_per_opportunity=50),
    )
    for _ in range(4):
        runner.step()

    assert seen == [2, 1, 0, 0]


def test_messages_sent_before_failed_activation_count_against_phase_cap(
    db_engine: Engine,
) -> None:
    """A committed Message survives a later ceiling and must consume budget (I-022)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    remaining: list[int] = []

    def send_then_loop(packet, tool_names, idx):
        if idx == 0:
            remaining.append(packet["limits"]["messages_remaining_this_phase"])
            return [
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": [], "public": True, "text": "count me"},
                )
            ]
        return [ToolCallPart(tool_name="update_strategic_memory", args=_memory("loop"))]

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(send_then_loop),
        guardrails=GuardrailConfig(
            max_messages_per_phase=1,
            max_llm_iterations=3,
            max_tool_calls=20,
            max_activations_per_opportunity=50,
        ),
    )
    first = runner.step()
    assert first.failure is not None and first.failure.category == "guardrail"
    assert first.failure.counts["messages_sent"] == 1

    # The legal recovery readied Arkania. A later private Message reopens its diplomacy
    # opportunity in the same turn/phase, where the already-committed Message must still count.
    game_service.send_message(
        db_engine,
        creation.game_id,
        sender_actor_id="belvar",
        recipients=["arkania"],
        public=False,
        text="Please reconsider.",
    )
    runner.step()

    assert remaining == [1, 0]
    messages = runner.player.tools.read_messages().data["messages"]
    mine = [m for m in messages if m.get("sender_actor_id") == "arkania"]
    assert len(mine) == 1


def test_activation_measures_model_iterations_provider_wait_and_wall_time(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def one_tool_then_stop(packet, tool_names, idx):
        if idx == 0:
            return [ToolCallPart(
                tool_name="update_strategic_memory", args=_memory("measured")
            )]
        return [TextPart(content="done")]

    player = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(one_tool_then_stop),
    )
    result = player.activate()

    assert result.model_iterations == 2
    assert result.tool_call_names == ["update_strategic_memory"]
    assert result.provider_wait_seconds >= 0
    assert result.activation_wall_seconds >= result.provider_wait_seconds


# --- 5. stall / failure recovery: auto-ready (diplomacy), auto-pass (Cons) ------------------


def test_ceiling_after_completing_the_opportunity_is_not_a_stall(db_engine: Engine) -> None:
    """A failed activation that already cleared its opportunity needs no recovery (I-025).

    Seen live: the model called `ready`, diplomacy closed, it kept working and hit the
    iteration ceiling. Recovery judged from the pre-activation snapshot then called `ready`
    again (409) and declared a stall on a game that had legally moved on. Recovery must be
    judged from the server's current state.
    """
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    for other in ("belvar", "cestia"):
        game_service.ready(db_engine, creation.game_id, other)  # arkania is the last to ready

    def ready_then_loop(packet, tool_names, idx):
        if idx == 0:
            return [ToolCallPart(tool_name="ready", args={})]
        return [ToolCallPart(
            tool_name="update_strategic_memory", args=_memory("still thinking")
        )]

    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(ready_then_loop),
        guardrails=GuardrailConfig(max_llm_iterations=4),
    )
    outcome = runner.step()

    assert outcome.failure is not None and outcome.failure.category == "guardrail"
    # The diplomacy opportunity was completed by the activation itself: diplomacy closed and the
    # game is now in ARGUMENT with arkania active — a *new* opportunity. No recovery tool was
    # needed, nothing is stuck, and the outcome reports the current state, not the stale one.
    assert outcome.failure.recovery_action is None
    assert outcome.stuck is False
    assert outcome.phase == "argument" and outcome.requires_input is True
    assert "make_argument" in outcome.context["available_actions"]
    assert runner._unrecoverable_key is None
    # The single-Actor loop does not terminate on it either.
    assert runner.run(max_cycles=1)[0].context["phase"] == "argument"


def test_stall_in_diplomacy_is_recovered_by_auto_ready(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def never_ready(packet, tool_names, idx):
        # Legal-but-non-terminating: update memory forever, never ready → no-progress stall.
        return (
            [ToolCallPart(tool_name="update_strategic_memory", args=_memory("busy"))]
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
    runner.run(max_cycles=20)

    assert runner.failures, "the stall was recorded"
    stall = runner.failures[-1]
    assert stall.category == FAIL_STALL
    assert stall.recovery_action == "ready"
    # The phase is no longer waiting on arkania — recovery auto-readied it.
    ctx = runner.observe()
    assert "ready" not in ctx["available_actions"]


def test_stall_in_cons_is_recovered_by_auto_pass(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_cons(db_engine, creation)  # arkania's Argument open_for_cons; belvar eligible

    def never_pass(packet, tool_names, idx):
        return (
            [ToolCallPart(tool_name="update_strategic_memory", args=_memory("stalling"))]
            if idx == 0
            else [TextPart(content="loop")]
        )

    runner = _runner(
        db_engine,
        creation,
        "belvar",
        model=model_from_policy(never_pass),
        guardrails=GuardrailConfig(max_activations_per_opportunity=3),
        sleep=lambda: None,
    )
    runner.run(max_cycles=20)

    assert runner.failures
    failure = runner.failures[-1]
    assert failure.recovery_action == "pass_con"  # Cons is closed with pass_con, never ready
    ctx = runner.observe()
    assert ctx["requires_input"] is False


def test_failed_active_arguer_is_recorded_and_yields_without_fabrication(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania active in ARGUMENT

    def dead(messages, info) -> ModelResponse:
        raise ModelHTTPError(status_code=401, model_name="m", body="bad key")

    runner = _runner(db_engine, creation, "arkania", model=FunctionModel(dead))
    outcome = runner.step()

    # Recorded, not swallowed; and NO legal minimal completion in ARGUMENT (ready/pass_con absent),
    # so the runner yields — it must never fabricate an Argument to "do something".
    assert outcome.failure is not None
    assert outcome.failure.category == "deterministic"
    assert outcome.failure.recovery_action is None
    assert runner.observe()["current_argument"] is None  # nothing fabricated


def test_i069_004_guardrail_failure_retries_required_argument_activation(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(default_policy),
        guardrails=GuardrailConfig(max_activations_per_opportunity=2),
    )
    real_activate = runner.player.activate
    attempts = {"n": 0}

    def fail_once(context=None):  # noqa: ANN001
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ActivationFailed(
                "synthetic research ceiling",
                category="guardrail",
                counts={"model_calls": 4, "tool_calls": 4, "messages_sent": 0},
            )
        return real_activate(context)

    runner.player.activate = fail_once  # type: ignore[method-assign]

    first = runner.step()
    second = runner.step()

    assert first.failure is not None
    assert first.failure.category == "guardrail"
    assert first.failure.recovery_action is None
    assert first.stuck is False
    assert second.acted is True
    assert runner.observe()["current_argument"] is not None
    assert attempts["n"] == 2


def test_i069_004_exhausted_required_decision_retries_stop_without_fabrication(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    runner = _runner(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(default_policy),
        guardrails=GuardrailConfig(max_activations_per_opportunity=1),
    )

    def always_fail(context=None):  # noqa: ANN001
        raise ActivationFailed(
            "synthetic research ceiling",
            category="guardrail",
            counts={"model_calls": 4, "tool_calls": 4, "messages_sent": 0},
        )

    runner.player.activate = always_fail  # type: ignore[method-assign]

    outcome = runner.step()

    assert outcome.failure is not None
    assert outcome.stuck is True
    assert runner.observe()["current_argument"] is None


# --- 6. the exit condition: a deliberately failing agent cannot hang a game ----------------


def test_deliberately_failing_agent_cannot_hang_the_game(db_engine: Engine) -> None:
    """Three players, one deliberately failing on every collective-phase obligation.

    Belvar's model raises a transient error whenever it would `ready` (diplomacy) or `pass_con`
    (Cons) — the two ways one player can deadlock a phase that needs everyone. It still makes its
    own Argument (recovery never fabricates one). Recovery auto-readies / auto-passes on each
    failure, so the turn completes (1 → 2) and every failure is recorded.
    """
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    umpire = deterministic_umpire()

    def failing(packet, tool_names, idx):
        actions = packet.get("available_actions", [])
        you = packet.get("you", {})
        if "make_argument" in actions:  # belvar still makes its own genuine Argument
            if idx == 0:
                objective = (you.get("objectives") or ["hold position"])[0]
                return [
                    ToolCallPart(
                        tool_name="make_argument",
                        args={
                            "action": f"Belvar acts to {objective}",
                            "desired_effect": f"Progress toward {objective}",
                            "pros": ["It follows from my objectives."],
                        },
                    )
                ]
            return [TextPart(content="done")]
        if "ready" in actions or "pass_con" in actions:
            raise ModelHTTPError(status_code=500, model_name="m", body="boom")  # fail the obligation
        return [TextPart(content="done")]

    runners = {}
    for a in ACTORS:
        model = model_from_policy(failing) if a == "belvar" else model_from_policy(default_policy)
        runners[a] = _runner(db_engine, creation, a, model=model, sleep=lambda: None)

    for _ in range(400):
        ctx = runners["arkania"].observe()
        if ctx["status"] == "complete" or (ctx["turn"] or 0) >= 2:
            break
        if ctx["phase"] == "adjudication":
            game_service.resolve_adjudication(db_engine, creation.game_id, umpire)
            game_service.advance_after_resolution(db_engine, creation.game_id)
            continue
        for actor in ACTORS:
            runners[actor].step()

    # The turn completed despite belvar failing every collective-phase obligation …
    assert runners["arkania"].observe()["turn"] == 2
    # … and belvar's failures were recorded, not swallowed — with legal recoveries taken.
    belvar_failures = runners["belvar"].failures
    assert belvar_failures, "belvar's failures were recorded"
    recoveries = {f.recovery_action for f in belvar_failures}
    assert "ready" in recoveries  # a diplomacy obligation was auto-completed
    assert "pass_con" in recoveries  # a Cons obligation was auto-completed
    assert all(isinstance(f, AgentFailure) for f in belvar_failures)


# --- 8. I-077: one activation-wide request, tool and research budget -----------------------
#
# The transient retry is "the same logical activation" (counters and idempotency namespace are
# not reset), so the agents.md §18 / TRD §21 per-activation ceilings must bound both attempts
# together, and an exact repeat of a successful research call must never reach the server.


def _tool_returns(result, tool_name: str) -> list:
    return [
        part
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


def _search_rows(engine, creation) -> list[str]:
    with session_scope(engine) as session:
        return [
            row.query
            for row in session.exec(
                select(ResourceAccess)
                .where(ResourceAccess.game_id == creation.game_id)
                .where(ResourceAccess.operation == "search")
                .order_by(ResourceAccess.id)
            )
        ]


def test_i077_001a_sequential_research_repeat_never_reaches_the_server(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen_tools: list[list[str]] = []

    def repeat_search(packet, tool_names, idx):
        seen_tools.append(list(tool_names))
        if idx < 2:
            return [ToolCallPart(tool_name="search_resources", args={"query": "regional"})]
        return [TextPart(content="done")]

    player = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(repeat_search),
        guardrails=GuardrailConfig(max_research_tool_calls=6),
    )
    result = player.activate()

    # The model tried twice; the server executed once.
    assert result.tool_call_names == ["search_resources", "search_resources"]
    assert _search_rows(db_engine, creation) == ["regional"]
    returns = _tool_returns(result, "search_resources")
    assert returns[0].content["ok"] is True
    assert returns[1].content["error"]["code"] == "guardrail_research_duplicate"
    # D-062 exact-repeat closure still applies …
    assert "search_resources" not in seen_tools[2]
    # … and the suppressed attempt stays visible in telemetry.
    assert result.usage["tool_calls"] == 2
    events = result.invalid_tool_call_breakdown["events"]
    assert [(e["reason"], e["error_code"]) for e in events] == [
        ("client_guardrail", "guardrail_research_duplicate")
    ]


def test_i077_001b_concurrent_identical_research_calls_execute_at_most_once(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen_tools: list[list[str]] = []

    def two_identical_then_stop(packet, tool_names, idx):
        seen_tools.append(list(tool_names))
        if idx == 0:
            # One model response, two identical research calls: PydanticAI runs sync tools of
            # one response concurrently in an executor.
            return [
                ToolCallPart(tool_name="search_resources", args={"query": "regional"}),
                ToolCallPart(tool_name="search_resources", args={"query": "regional"}),
            ]
        return [TextPart(content="done")]

    player = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(two_identical_then_stop),
        guardrails=GuardrailConfig(max_research_tool_calls=6),
    )
    # Hold every call that reaches the server long enough for its twin to be dispatched, so a
    # check-then-execute race is forced rather than left to scheduling luck. If only one call
    # reaches the server (the fixed behaviour) the barrier times out and the call proceeds.
    real_search = player._tools.search_resources
    barrier = threading.Barrier(2, timeout=0.5)

    def held_search(**kwargs):
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return real_search(**kwargs)

    player._tools.search_resources = held_search  # type: ignore[method-assign]

    result = player.activate()

    assert result.tool_call_names == ["search_resources", "search_resources"]
    assert _search_rows(db_engine, creation) == ["regional"]
    codes = sorted(
        (r.content.get("error") or {}).get("code") or "ok"
        for r in _tool_returns(result, "search_resources")
    )
    assert codes == ["guardrail_research_duplicate", "ok"]
    # An identical call that was merely in flight is not a repeat of a *successful* call: the
    # research surface stays open (D-062 closes it only on an exact successful-call repeat).
    assert "search_resources" in seen_tools[1]
    assert result.usage["tool_calls"] == 2


def _memory_call() -> ToolCallPart:
    return ToolCallPart(tool_name="update_strategic_memory", args=_memory("noted"))


def test_i077_002a_retry_gets_exactly_the_remaining_request_allowance(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def n_minus_one_then_transient(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[_memory_call()])
        if calls["n"] == 2:
            raise ModelHTTPError(status_code=503, model_name="m", body="overloaded")
        return ModelResponse(parts=[TextPart(content="done")])

    p = _player(
        db_engine,
        creation,
        "arkania",
        model=FunctionModel(n_minus_one_then_transient),
        guardrails=GuardrailConfig(max_llm_iterations=3),
    )
    result = p.activate()  # first attempt used 2 of 3; the retry may make exactly 1

    assert calls["n"] == 3
    assert result.model_iterations == 3
    assert result.usage["requests"] == 3


def test_i077_002b_retry_is_refused_when_the_request_allowance_is_spent(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def n_then_transient(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] < 3:
            return ModelResponse(parts=[_memory_call()])
        if calls["n"] == 3:
            raise ModelHTTPError(status_code=503, model_name="m", body="overloaded")
        return ModelResponse(parts=[TextPart(content="done")])

    p = _player(
        db_engine,
        creation,
        "arkania",
        model=FunctionModel(n_then_transient),
        guardrails=GuardrailConfig(max_llm_iterations=3),
    )
    with pytest.raises(ActivationFailed) as ei:
        p.activate()  # first attempt used all 3: no second provider request may happen

    assert calls["n"] == 3
    assert ei.value.category == "guardrail"
    assert ei.value.counts["model_calls"] == 3
    assert ei.value.counts["usage"]["requests"] == 3


def test_i077_002c_retry_gets_exactly_the_remaining_tool_allowance(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def two_tools_then_transient(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[_memory_call(), _memory_call()])
        if calls["n"] == 2:
            raise ModelHTTPError(status_code=503, model_name="m", body="overloaded")
        if calls["n"] == 3:
            return ModelResponse(parts=[_memory_call()])
        return ModelResponse(parts=[TextPart(content="done")])

    p = _player(
        db_engine,
        creation,
        "arkania",
        model=FunctionModel(two_tools_then_transient),
        guardrails=GuardrailConfig(max_tool_calls=3, max_llm_iterations=20),
    )
    result = p.activate()  # first attempt used 2 of 3 tool calls; the retry may make exactly 1

    assert calls["n"] == 4
    assert result.usage["tool_calls"] == 3


def test_i077_002d_retry_is_refused_when_the_tool_allowance_is_spent(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def all_tools_then_transient(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[_memory_call(), _memory_call()])
        if calls["n"] == 2:
            raise ModelHTTPError(status_code=503, model_name="m", body="overloaded")
        return ModelResponse(parts=[TextPart(content="done")])

    p = _player(
        db_engine,
        creation,
        "arkania",
        model=FunctionModel(all_tools_then_transient),
        guardrails=GuardrailConfig(max_tool_calls=2, max_llm_iterations=20),
    )
    with pytest.raises(ActivationFailed) as ei:
        p.activate()  # both tool calls spent: no second provider request may happen

    assert calls["n"] == 2
    assert ei.value.category == "guardrail"
    assert ei.value.counts["tool_calls"] == 2
    assert ei.value.counts["model_calls"] == 2


@pytest.mark.parametrize(
    "field",
    ["max_llm_iterations", "max_tool_calls", "max_activations_per_opportunity"],
)
def test_i077_003_guardrail_config_rejects_a_non_positive_ceiling(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        GuardrailConfig(**{field: 0})
    with pytest.raises(ValueError, match=field):
        GuardrailConfig(**{field: -1})


def test_i077_003_guardrail_config_rejects_a_negative_phase_message_cap() -> None:
    GuardrailConfig(max_messages_per_phase=0)  # zero is the emergency "no Messages" ceiling
    with pytest.raises(ValueError, match="max_messages_per_phase"):
        GuardrailConfig(max_messages_per_phase=-1)


# --- a retried activation does not replay a committed send --------------------------------


def test_i007_retry_after_a_committed_send_does_not_duplicate_it(db_engine: Engine) -> None:
    """I-007: a transient failure after a tool committed must not replay the side effect."""
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from tests.test_guardrails import _player

    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = {"n": 0}

    def send_then_fail_then_send(messages, info) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] in (1, 3):  # attempt 1 and attempt 2 both open with the same send
            return ModelResponse(parts=[ToolCallPart(
                tool_name="send_message",
                args={"recipients": [], "public": True, "text": "hello once"},
            )])
        if calls["n"] == 2:  # the request after the committed send fails transiently
            raise ModelHTTPError(status_code=503, model_name="m", body="overloaded")
        return ModelResponse(parts=[TextPart(content="done")])

    p = _player(db_engine, creation, "arkania", model=FunctionModel(send_then_fail_then_send))
    result = p.activate()

    mine = [m for m in p.tools.read_messages().data["messages"] if m["sender_actor_id"] == "arkania"]
    assert len(mine) == 1  # the replayed send hit the same idempotency key
    assert result.messages_sent == 1  # and the cap accounting counted it once
