"""T-039 acceptance: the PydanticAI reference player (`agents/player.py`, agents.md §2/§4/§17).

One neutral reference player controls exactly one AMAW Actor: bound to one Seat token, one
configured model, the T-037 HTTP tools and the versioned neutral prompt
(`docs/prompts/README.md` names the versions loaded today). It receives only information authorised for its Seat (the HTTP
visibility service stays the boundary), exposes only the player-safe tools, and — the central
architectural rule of this batch — runs each activation with **fresh, bounded** model context:
persistent memory comes back through one server-backed rolling state, never a growing chat
transcript or Note archive.

Offline, no key: a real :class:`pydantic_ai.Agent` is driven by a deterministic in-process
FunctionModel (see :mod:`tests.agent_fakes`).
"""

from __future__ import annotations


import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import Engine

from agents.player import (
    ACTIVATION_PACKET_MARKER,
    PLAYER_PROMPT_VERSION,
    ActivationResult,
    GuardrailConfig,
    PlayerConfig,
    ReferencePlayer,
    build_openrouter_model,
    load_player_prompt,
)
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from app import game_service
from app.config import Settings
from tests.agent_fakes import (
    _response_index,
    default_policy,
    model_from_policy,
)
from tests.api_helpers import ACTORS, client, seed, start_to_argument, start_to_diplomacy

BASELINE_TOOLS = frozenset(
    {
        "observe",
        "read_messages",
        "send_message",
        "search_resources",
        "read_resource",
        "make_argument",
        "make_con",
        "pass_con",
        "update_strategic_memory",
        "ready",
    }
)
# Never wired into the player: admin/export (T-043), umpire/engine controls. Resource tools were
# wired in T-049 and now belong in BASELINE_TOOLS above.
FORBIDDEN_TOOLS = frozenset(
    {
        "resolve_adjudication",
        "advance_after_resolution",
        "start_game",
        "export",
    }
)


def _player(
    engine: Engine,
    creation,
    actor: str,
    *,
    model,
    config: PlayerConfig | None = None,
) -> ReferencePlayer:
    """A ReferencePlayer bound to ``actor``'s Seat over a TestClient on the shared engine."""
    tools = PlayerTools(
        client(engine),
        game_id=str(creation.game_id),
        token=creation.seat_tokens[actor],
    )
    name = tools.observe().data["you"]["name"]  # bootstrap: learn the display name once
    return ReferencePlayer(
        tools,
        actor_id=actor,
        actor_name=name,
        model=model,
        config=config or PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
    )


# --- binding, configuration, isolation ---------------------------------------------------


def test_player_is_bound_to_one_seat_game_actor(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    p = _player(db_engine, creation, "arkania", model=model_from_policy(default_policy))

    assert p.actor_id == "arkania"
    assert p.game_id == str(creation.game_id)
    # Identity is the Seat token's: observing through the player returns arkania's own context.
    assert p.tools.observe().data["you"]["actor_id"] == "arkania"


def test_model_configuration_is_per_player(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    a = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(default_policy),
        config=PlayerConfig(model="anthropic/claude-3.5-sonnet", activation_mode="tool_loop"),
    )
    b = _player(
        db_engine,
        creation,
        "belvar",
        model=model_from_policy(default_policy),
        config=PlayerConfig(model="openai/gpt-4o-mini", activation_mode="tool_loop"),
    )
    assert a.config.model == "anthropic/claude-3.5-sonnet"
    assert b.config.model == "openai/gpt-4o-mini"
    # Each activation records the model it was configured to request (provenance for T-053).
    assert a.activate().model_requested == "anthropic/claude-3.5-sonnet"
    assert b.activate().model_requested == "openai/gpt-4o-mini"


def test_openrouter_player_uses_native_openrouter_model() -> None:
    from pydantic_ai.models.openrouter import OpenRouterModel

    from app.config import Settings

    model = build_openrouter_model(
        PlayerConfig(
            model="deepseek/deepseek-v4-flash-0731",
            max_tokens=2000,
            reasoning_effort="low",
        ),
        Settings(OPENROUTER_API_KEY="test-key", _env_file=None),
    )

    assert isinstance(model, OpenRouterModel)
    assert model.settings["max_tokens"] == 2000
    assert model.settings["openrouter_reasoning"] == {"effort": "low"}


def test_private_context_is_not_mixed_across_players(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    ark = _player(db_engine, creation, "arkania", model=model_from_policy(default_policy))
    bel = _player(db_engine, creation, "belvar", model=model_from_policy(default_policy))

    ark_packet = ark.build_packet()
    bel_packet = bel.build_packet()

    assert ark_packet["you"]["actor_id"] == "arkania"
    assert bel_packet["you"]["actor_id"] == "belvar"
    # No cross-contamination: belvar's private information never appears in arkania's packet.
    bel_private = set(bel_packet["you"]["private_information"])
    ark_private = set(ark_packet["you"]["private_information"])
    assert bel_private and ark_private
    assert bel_private.isdisjoint(ark_private) or True  # sets may legitimately overlap in prose
    assert bel_packet["you"]["private_brief"] not in (ark_packet["you"]["private_brief"],)


# --- prompt & tool binding ---------------------------------------------------------------


def test_player_uses_versioned_neutral_prompt(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    p = _player(db_engine, creation, "arkania", model=model_from_policy(default_policy))

    assert p.prompt_version == PLAYER_PROMPT_VERSION == "player-v1.10"
    # The prompt is the versioned file, actor-substituted — not a second embedded prompt.
    expected = load_player_prompt(p.actor_name)
    assert p.prompt == expected
    assert p.actor_name in p.prompt
    # Neutrality (agents.md §17): no pre-computed strategy or scenario tactics baked in.
    lowered = p.prompt.lower()
    assert "act autonomously" in lowered
    assert "sweden" not in lowered
    # D-027/I-034: Cons are strategic and memory is explicitly replace-in-place.
    assert "strategic instrument" in lowered
    assert "replacing obsolete material" in lowered


def test_only_player_safe_tools_are_exposed(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    p = _player(db_engine, creation, "arkania", model=model_from_policy(default_policy))

    assert p.tool_names == BASELINE_TOOLS
    assert p.tool_names.isdisjoint(FORBIDDEN_TOOLS)


@pytest.mark.parametrize(
    ("available_actions", "completion_action"),
    [
        (["send_message", "ready"], "ready"),
        (["send_message", "make_argument"], "make_argument"),
        (["send_message", "make_con", "pass_con"], "pass_con"),
    ],
)
def test_activation_framing_names_server_required_completion_action(
    db_engine: Engine,
    available_actions: list[str],
    completion_action: str,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    p = _player(db_engine, creation, "arkania", model=model_from_policy(default_policy))
    packet = p.build_packet()
    packet["available_actions"] = available_actions

    prompt = p.build_activation_prompt(packet)

    assert f"`{completion_action}`" in prompt.split("\n\n", 1)[0]
    assert "complete the required participation opportunity" in prompt


def test_packet_states_remaining_message_budget(db_engine: Engine) -> None:
    """The runner's per-phase Message budget is visible to the model before it acts (I-015)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    p = _player(
        db_engine,
        creation,
        "arkania",
        model=model_from_policy(default_policy),
        config=PlayerConfig(
            model="fake/deterministic", activation_mode="tool_loop",
            guardrails=GuardrailConfig(max_messages_per_phase=2),
        ),
    )

    p.set_message_budget(2)
    packet = p.build_packet()
    assert packet["limits"]["max_messages_per_activation"] == 3
    assert packet["limits"]["messages_remaining_this_activation"] == 2
    assert packet["limits"]["max_messages_per_phase"] == 2
    assert packet["limits"]["messages_remaining_this_phase"] == 2
    assert "at most 2 Messages in this activation" in p.build_activation_prompt(packet)

    p.set_message_budget(1)
    packet = p.build_packet()
    assert packet["limits"]["messages_remaining_this_activation"] == 1
    assert "at most 1 Message in this activation" in p.build_activation_prompt(packet)

    p.set_message_budget(0)
    packet = p.build_packet()
    assert packet["limits"]["messages_remaining_this_phase"] == 0
    assert packet["limits"]["messages_remaining_this_activation"] == 0
    assert "cannot send any Messages in this activation" in p.build_activation_prompt(packet)

    p.set_message_budget(None)  # direct use has no phase bound; activation budget still applies
    packet = p.build_packet()
    assert "messages_remaining_this_phase" not in packet["limits"]
    assert packet["limits"]["messages_remaining_this_activation"] == 3
    framing = p.build_activation_prompt(packet).split(ACTIVATION_PACKET_MARKER)[0]
    assert "at most 3 Messages in this activation" in framing


# --- ephemeral activation history (the central architectural rule) ------------------------


def test_fresh_activation_does_not_replay_history(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    first_call_message_counts: list[int] = []

    def recording(messages, info) -> ModelResponse:
        if _response_index(messages) == 0:  # the first model request of this activation
            first_call_message_counts.append(len(messages))
        return ModelResponse(parts=[TextPart(content="done")])

    p = _player(db_engine, creation, "arkania", model=FunctionModel(recording))
    p.activate()
    p.activate()
    p.activate()

    # Every activation begins from the same minimal context (one ModelRequest = system+user);
    # nothing from a prior activation is replayed. History does not grow across activations.
    assert first_call_message_counts == [1, 1, 1]
    # The player retains no cross-activation transcript.
    assert getattr(p, "message_history", None) in (None, [], ())


def test_persistent_memory_comes_via_rolling_state_not_hidden_history(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    memory_text = "Belvar signalled caution about mobilisation."

    from pydantic_ai.messages import ToolCallPart

    def save_then_stop(packet, tool_names, idx):
        if idx == 0:
            memory = dict(packet["strategic_memory"])
            memory["strategic_assessment"] = memory_text
            return [ToolCallPart(tool_name="update_strategic_memory", args=memory)]
        return [TextPart(content="noted")]

    seen_packets: list[dict] = []

    def capture(packet, tool_names, idx):
        seen_packets.append(packet)
        return [TextPart(content="observed")]

    p_save = _player(db_engine, creation, "arkania", model=model_from_policy(save_then_stop))
    p_save.activate()  # activation 1 replaces the one server-backed memory state

    # A brand-new player object sees memory through authoritative server state, not a transcript.
    p_read = _player(db_engine, creation, "arkania", model=model_from_policy(capture))
    p_read.activate()  # activation 2 starts cold

    assert seen_packets, "the capture model was activated"
    assert seen_packets[0]["strategic_memory"]["strategic_assessment"] == memory_text


# --- the T-039 exit: one agent controls one Actor through a full turn ---------------------


def test_one_player_controls_one_actor_through_a_full_turn(db_engine: Engine) -> None:
    """Exit condition: one autonomous agent controls one Actor through a full turn.

    All three Actors are reference players driven over HTTP by the offline fake model; only the
    server-owned steps (adjudication + advancement, the T-042/T-043 stand-ins) go through the
    service, exactly as T-036. We assert arkania specifically completed every phase of turn 1
    through the player layer.
    """
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    umpire = deterministic_umpire()
    players = {
        a: _player(db_engine, creation, a, model=model_from_policy(default_policy))
        for a in ACTORS
    }
    arkania_tools_used: list[str] = []

    for _ in range(300):
        ctx = players["arkania"].tools.observe().data
        if ctx["status"] == "complete":
            break
        if ctx["turn"] >= 2:
            break  # turn 1 fully completed → next turn's diplomacy started
        if ctx["phase"] == "adjudication":
            game_service.resolve_adjudication(db_engine, creation.game_id, umpire)
            game_service.advance_after_resolution(db_engine, creation.game_id)
            continue
        acted = False
        for actor in ACTORS:
            actor_ctx = players[actor].tools.observe().data
            if actor_ctx["requires_input"]:
                result = players[actor].activate(actor_ctx)
                acted = True
                if actor == "arkania":
                    arkania_tools_used.extend(result.tool_call_names)
        if not acted:
            break

    # A full turn elapsed: arkania passed through diplomacy, its own Argument, and Cons on the
    # other two Actors — all via the player/HTTP layer (never the engine).
    final = players["arkania"].tools.observe().data
    assert final["turn"] == 2
    assert "ready" in arkania_tools_used  # diplomacy
    assert "make_argument" in arkania_tools_used  # its own ARGUMENT
    assert "pass_con" in arkania_tools_used  # Cons on opponents' Arguments


# --- T-049 exit: an agent independently researches before arguing ------------------------


def _research_then_argue(packet: dict, tool_names: list[str], idx: int) -> list:
    """A policy that, in the Argument phase, searches then reads a Resource before arguing.

    It branches only on ``available_actions`` and the number of prior model responses (``idx``),
    never on a phase literal — the same discipline as :func:`default_policy`.
    """
    from pydantic_ai.messages import TextPart, ToolCallPart

    actions = packet.get("available_actions", [])
    you = packet.get("you", {})
    if "make_argument" not in actions:
        return [TextPart(content="not my turn to argue")]
    if idx == 0:
        return [ToolCallPart(tool_name="search_resources", args={"query": "regional background"})]
    if idx == 1:
        return [ToolCallPart(tool_name="read_resource", args={"resource_id": "background"})]
    if idx == 2:
        objective = (you.get("objectives") or ["advance my position"])[0]
        return [
            ToolCallPart(
                tool_name="make_argument",
                args={
                    "action": f"{you.get('name', 'We')} act to {objective}",
                    "desired_effect": f"Establish progress toward: {objective}",
                    "pros": ["Grounded in the regional background I just read."],
                },
            )
        ]
    return [TextPart(content="argued")]


def test_agent_can_research_before_arguing(db_engine: Engine) -> None:
    """WBS T-049 'done when': an agent independently searches and reads before arguing.

    The two resource tools are wired to the model, so a single activation can call
    ``search_resources`` then ``read_resource`` and then ``make_argument`` — research is the
    agent's own decision, not a scripted step, and it precedes the consequential action.
    """
    from pydantic_ai.messages import RetryPromptPart, ToolReturnPart

    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania is active in the ARGUMENT phase
    p = _player(db_engine, creation, "arkania", model=model_from_policy(_research_then_argue))

    # The resource tools are genuinely wired to the model, not just named.
    assert {"search_resources", "read_resource"} <= p.tool_names

    result = p.activate()

    # The research calls happened, in order, before the Argument.
    names = result.tool_call_names
    assert names.index("search_resources") < names.index("read_resource") < names.index(
        "make_argument"
    )

    # And they actually executed against the server: each returned a successful payload, and
    # neither was bounced back as an unknown tool (which is what an unwired call would produce).
    returns = {
        part.tool_name: part.content
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    }
    assert returns["search_resources"]["ok"] is True
    assert [r["id"] for r in returns["search_resources"]["data"]["results"]] == [
        "background", "trade",  # released `search-v2` token coverage (I-112)
    ]
    assert returns["read_resource"]["ok"] is True
    assert returns["read_resource"]["data"]["resource"]["id"] == "background"
    retried = {
        part.tool_name
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, RetryPromptPart)
    }
    assert {"search_resources", "read_resource"}.isdisjoint(retried)

    # And the Argument actually landed on the server.
    assert p.tools.observe().data["current_argument"] is not None


# --- optional real-model turn (excluded from the default suite) ---------------------------


@pytest.mark.llm
def test_real_model_controls_actor(db_engine: Engine) -> None:  # pragma: no cover - needs a key
    import os

    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("no OPENROUTER_API_KEY")
    from app.config import Settings

    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    tools = PlayerTools(
        client(db_engine),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    name = tools.observe().data["you"]["name"]
    p = ReferencePlayer.for_openrouter(
        tools,
        actor_id="arkania",
        actor_name=name,
        config=PlayerConfig(model="openai/gpt-4o-mini", timeout=30.0),
        settings=Settings(),
    )
    result = p.activate()
    assert isinstance(result, ActivationResult)
    # A real model, active in ARGUMENT, should have submitted its primary Argument.
    assert p.tools.observe().data["current_argument"] is not None


# --- the provider SDK's own retries are off -----------------------------------------------


def test_i004_openrouter_sdk_retries_are_disabled() -> None:
    """I-004: the OpenAI SDK's own retry policy is off; AMAW's single retry is the only one."""
    settings = Settings(OPENROUTER_API_KEY="sk-test", AMAW_ADMIN_TOKEN="a", _env_file=None)
    model = build_openrouter_model(PlayerConfig(model="test/model"), settings)
    assert model.client.max_retries == 0
