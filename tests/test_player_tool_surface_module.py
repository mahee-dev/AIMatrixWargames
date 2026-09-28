"""I-124: the player's tool exposure and per-call accounting live in one ``ToolSurface``.

The move is behaviour-preserving. The model-facing tool definitions are the player's own
closures, registered unchanged. The exposure hooks and the counters belong to the surface, and
every counter is changed only under its one lock.
"""

from __future__ import annotations

import itertools
import threading
from types import SimpleNamespace

from pydantic_ai.models.test import TestModel

from agents.guardrails import GuardrailConfig
from agents.player import PlayerConfig, ReferencePlayer
from agents.tool_surface import MUTATING_TOOL_NAMES, ToolSurface


def _player(**config) -> ReferencePlayer:  # noqa: ANN003
    return ReferencePlayer(
        SimpleNamespace(_game_id="g"), actor_id="a", actor_name="Arkania",
        model=TestModel(), config=PlayerConfig(model="m", **config), prompt="p",
    )


def test_i124_001_registered_tools_are_exposed_names_with_surface_hooks() -> None:
    for activation, research, reply, surface in itertools.product(
        ("tool_loop", "single_shot"), ("sequential-v1", "batched-v1"),
        ("sealed_cons", "visible_reply"), ("broad", "available_only"),
    ):
        player = _player(
            activation_mode=activation, research_mode=research,
            con_reply_mode=reply, tool_surface=surface,
        )
        registered = player._agent._function_toolset.tools  # noqa: SLF001
        assert frozenset(registered) == player.tool_names
        for name, tool in registered.items():
            assert tool.function.__name__ == name  # the closure's own name, via functools.wraps
            if name in MUTATING_TOOL_NAMES:
                assert tool.prepare == player._surface.prepare_mutating_tool  # noqa: SLF001
            elif tool.prepare is not None:
                assert tool.prepare == player._surface.prepare_research_tool  # noqa: SLF001


def _surface(**guardrails) -> ToolSurface:  # noqa: ANN003
    return ToolSurface(
        GuardrailConfig(**guardrails), single_shot=False, available_only=True,
        batched_research=True, visible_reply=False,
    )


def test_i124_002_concurrent_operation_charges_never_exceed_the_budget() -> None:
    surface = _surface(max_research_operations=5)
    refusals: list[object] = []
    barrier = threading.Barrier(16)

    def charge() -> None:
        barrier.wait()
        refusals.append(surface.charge_research_operation())

    threads = [threading.Thread(target=charge) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(refusal is None for refusal in refusals) == 5
    assert surface.research_counts()["research_operations"] == 5
    assert surface.remaining_research_operations() == 0


def test_i124_003_reset_clears_counts_and_live_declaration_but_not_the_packet() -> None:
    surface = _surface()
    surface.declare(["make_argument"])
    surface.record_live_actions([])
    surface.charge_research_operation()
    tool = SimpleNamespace(name="make_argument")
    assert surface.prepare_mutating_tool(None, tool) is None  # retracted by the live refresh
    surface.reset()
    assert surface.research_counts() == {"research_tool_calls": 0, "research_operations": 0}
    assert surface.tool_calls == 0
    # The live refresh is gone; the packet's declaration stays until the next ``declare``.
    assert surface.prepare_mutating_tool(None, tool) is tool
