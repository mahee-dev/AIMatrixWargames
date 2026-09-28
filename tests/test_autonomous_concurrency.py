"""I-028/I-029: bounded concurrent diplomacy rounds and Con sweeps.

Cons are read only by the Umpire (no player-facing Cons read exists — `GET /context` omits them,
`con_submitted` carries only ids), so the opponents of one Argument judge independently whether
they are activated one after another or at the same time. The coordinator may therefore run the
Cons sweep concurrently; the server's guarded transactions (state-machine §25, INV-GAME-005) keep
the phase close single. Diplomacy additionally freezes round-start reads and defers readiness so
private Messages deterministically reactivate recipients. The tests cover equivalent game shape,
real overlap, bounded workers, both readiness races, provenance and failure cleanup.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart
from pydantic_ai.models.wrapper import WrapperModel
from sqlalchemy import Engine
from sqlmodel import Session, select

from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import PlayerConfig, ReferencePlayer
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from app.api import create_app
from app.config import Settings
from app.export import EXPORT_FORMAT_VERSION, build_game_export
from app.models import Game, GameEvent, GamePhase
from tests.agent_fakes import (
    current_memory,
    decision_call,
    default_decision_policy,
    default_policy,
    model_from_decision_policy,
    model_from_policy,
)
from tests.cli_helpers import capture_run_cli, invoke_run
from tests.api_helpers import FIXTURE_DIR
from tests.benchmark_helpers import SHORT_TURNS

ACTORS = ("arkania", "belvar", "cestia")
ADMIN_TOKEN = "admin-secret-token"
SEED = 20260913


def _client(db_engine: Engine) -> TestClient:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    return TestClient(app, raise_server_exceptions=False)


def _factory(policy=default_policy, runner_cls=LifecycleRunner):
    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model_from_policy(policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
        )
        return runner_cls(player)

    return player_factory


def _decision_factory(policy=default_decision_policy, runner_cls=LifecycleRunner, captured=None):
    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model_from_decision_policy(policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="single_shot"),
        )
        runner = runner_cls(player)
        if captured is not None:
            captured[actor_id] = runner
        return runner

    return player_factory


def _diplomacy_decision(packet, info, *, messages=None, ready=True):
    return decision_call(info, {
        "messages": messages or [],
        "strategic_memory": current_memory(packet),
        "ready": ready,
    })


def _run(
    client: TestClient, scenario: Path, *, cons_concurrency: int, diplomacy_concurrency: int = 4,
    player_factory=None, max_cycles=1000, diplomacy_mode="open_diplomacy",
    diplomacy_rounds_per_turn=None,
):
    runner = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=scenario,
        player_factory=player_factory or _factory(),
        seed=SEED,
        max_cycles=max_cycles,
        cons_concurrency=cons_concurrency,
        diplomacy_concurrency=diplomacy_concurrency,
        diplomacy_mode=diplomacy_mode,
        diplomacy_rounds_per_turn=diplomacy_rounds_per_turn,
    )
    return runner, runner.run()


@pytest.mark.parametrize("diplomacy_concurrency", [1, 3])
def test_i053_002_bounded_one_runs_one_complete_round_per_turn(
    db_engine: Engine, diplomacy_concurrency: int, short_game: Path,
) -> None:
    def never_ready(packet, idx, info):
        if packet.get("phase") == "diplomacy":
            return [_diplomacy_decision(packet, info, ready=False)]
        return default_decision_policy(packet, idx, info)

    _, result = _run(
        _client(db_engine),
        short_game,
        cons_concurrency=3,
        diplomacy_concurrency=diplomacy_concurrency,
        player_factory=_decision_factory(never_ready),
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=1,
    )

    diplomacy = [run for run in result.export["agent_runs"] if run["phase"] == "diplomacy"]
    assert len(diplomacy) == len(ACTORS) * SHORT_TURNS
    assert result.export["export_format_version"] == EXPORT_FORMAT_VERSION
    assert result.export["game"]["diplomacy_mode"] == "bounded_rounds"
    assert result.export["game"]["diplomacy_rounds_per_turn"] == 1
    assert result.export["run_manifest"]["game_loop"] == {
        "diplomacy_policy_version": "diplomacy-policy-v1",
        "diplomacy_mode": "bounded_rounds",
        "diplomacy_rounds_per_turn": 1,
        "con_policy_version": "con-policy-v1",
        "con_mode": "open_cons",
        "con_challengers_per_argument": None,
        "con_reply_policy_version": "con-reply-policy-v1",
        "con_reply_mode": "sealed_cons",
        "reply_max_points": None,
    }
    assert all(
        run["model_parameters"]["diplomacy_mode"] == "bounded_rounds"
        and run["model_parameters"]["diplomacy_rounds_per_turn"] == 1
        for run in diplomacy
    )
    condition = result.export["run_summary"]["diplomacy_condition"]
    assert condition["mode"] == "bounded_rounds"
    assert condition["rounds_per_turn"] == 1
    assert [turn["rounds_completed"] for turn in condition["turns"]] == [1] * SHORT_TURNS
    assert all(turn["actor_activations"] == len(ACTORS) for turn in condition["turns"])
    closures = [
        event for event in result.export["events"] if event["type"] == "diplomacy_closed"
    ]
    assert [(event["turn"], event["payload"]["reason"]) for event in closures] == [
        (turn, "round_bound_reached") for turn in range(1, SHORT_TURNS + 1)
    ]


@pytest.mark.slow
def test_i053_003_bounded_n_never_exceeds_complete_round_limit(db_engine: Engine, short_game: Path) -> None:
    def never_ready(packet, idx, info):
        if packet.get("phase") == "diplomacy":
            return [_diplomacy_decision(packet, info, ready=False)]
        return default_decision_policy(packet, idx, info)

    _, result = _run(
        _client(db_engine),
        short_game,
        cons_concurrency=3,
        player_factory=_decision_factory(never_ready),
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=2,
    )

    rounds = [
        event for event in result.export["events"]
        if event["type"] == "diplomacy_round_completed"
    ]
    assert [(event["turn"], event["payload"]["round"]) for event in rounds] == [
        (turn, round_number)
        for turn in range(1, SHORT_TURNS + 1)
        for round_number in (1, 2)
    ]


@pytest.mark.slow
def test_i053_004_all_ready_closes_naturally_before_bound(db_engine: Engine, short_game: Path) -> None:
    _, result = _run(
        _client(db_engine),
        short_game,
        cons_concurrency=3,
        player_factory=_decision_factory(),
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=3,
    )

    closures = [
        event for event in result.export["events"] if event["type"] == "diplomacy_closed"
    ]
    assert len(closures) == SHORT_TURNS
    assert {event["payload"]["reason"] for event in closures} == {"all_ready"}


def test_i053_005_final_round_private_message_is_kept_without_reactivation(
    db_engine: Engine, short_game: Path,
) -> None:
    calls = {actor: 0 for actor in ACTORS}

    def final_message(packet, idx, info):
        if packet.get("phase") != "diplomacy":
            return default_decision_policy(packet, idx, info)
        actor = packet["you"]["actor_id"]
        calls[actor] += 1
        messages = []
        if actor == "arkania":
            messages = [{"recipients": ["belvar"], "public": False, "text": "final offer"}]
        return [_diplomacy_decision(packet, info, messages=messages, ready=True)]

    _, result = _run(
        _client(db_engine),
        short_game,
        cons_concurrency=3,
        player_factory=_decision_factory(final_message),
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=1,
    )

    assert calls == {actor: SHORT_TURNS for actor in ACTORS}
    assert len([m for m in result.export["messages"] if m["text"] == "final offer"]) == SHORT_TURNS
    assert all(
        event["payload"]["reason"] == "round_bound_reached"
        for event in result.export["events"]
        if event["type"] == "diplomacy_closed"
    )


@pytest.mark.slow
def test_i053_008_bounded_condition_round_trips_from_configuration_to_summary(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_game: Path,
) -> None:
    """TEST-I053-008: one bounded condition reaches every record that reports it, unchanged.

    Configuration (``AMAW_DIPLOMACY_*``) resolves through ``amaw run`` to the runner; the runner
    persists it on the Game, the manifest and every Diplomacy AgentRun; the export rebuilt from the
    database and the run summary report the same condition (testing.md §26).
    """
    captured = capture_run_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("AMAW_DIPLOMACY_MODE", "bounded_rounds")
    monkeypatch.setenv("AMAW_DIPLOMACY_ROUNDS_PER_TURN", "2")
    invoked = invoke_run(FIXTURE_DIR)
    assert invoked.exit_code == 0, invoked.output
    options = captured["runner_options"]
    assert (options["diplomacy_mode"], options["diplomacy_rounds_per_turn"]) == ("bounded_rounds", 2)

    def never_ready(packet, idx, info):
        if packet.get("phase") == "diplomacy":
            return [_diplomacy_decision(packet, info, ready=False)]
        return default_decision_policy(packet, idx, info)

    runner, result = _run(
        _client(db_engine),
        short_game,
        cons_concurrency=3,
        player_factory=_decision_factory(never_ready),
        diplomacy_mode=options["diplomacy_mode"],
        diplomacy_rounds_per_turn=options["diplomacy_rounds_per_turn"],
    )
    rebuilt = build_game_export(db_engine, uuid.UUID(runner.last_game_id))
    for export in (result.export, rebuilt):
        assert export["game"]["diplomacy_mode"] == "bounded_rounds"
        assert export["game"]["diplomacy_rounds_per_turn"] == 2
        loop = export["run_manifest"]["game_loop"]
        assert (loop["diplomacy_mode"], loop["diplomacy_rounds_per_turn"]) == ("bounded_rounds", 2)
        diplomacy = [run for run in export["agent_runs"] if run["phase"] == "diplomacy"]
        assert diplomacy and all(
            run["model_parameters"]["diplomacy_mode"] == "bounded_rounds"
            and run["model_parameters"]["diplomacy_rounds_per_turn"] == 2
            for run in diplomacy
        )
        condition = export["run_summary"]["diplomacy_condition"]
        assert (condition["mode"], condition["rounds_per_turn"]) == ("bounded_rounds", 2)
        assert [turn["rounds_completed"] for turn in condition["turns"]] == [2] * SHORT_TURNS
    assert rebuilt["run_manifest"] == result.export["run_manifest"]
    assert rebuilt["run_summary"]["diplomacy_condition"] == result.export["run_summary"]["diplomacy_condition"]


class _FailsInDiplomacy(LifecycleRunner):
    """belvar's Diplomacy activation raises outright: a failed, unsettled activation."""

    def step(self, context=None):  # noqa: ANN001, ANN201
        if self.player.actor_id == "belvar" and (context or {}).get("phase") == "diplomacy":
            raise RuntimeError("injected Diplomacy activation failure")
        return super().step(context)


@pytest.mark.parametrize("diplomacy_concurrency", [1, 3])
def test_i053_012_failed_activation_does_not_complete_or_close_a_bounded_round(
    db_engine: Engine, diplomacy_concurrency: int, short_game: Path,
) -> None:
    """TEST-I053-012: a round with a failed activation is never charged against the bound.

    The run stops on the failure before the round settles (serially the error propagates as
    raised; concurrently it is wrapped in ``AutonomousGameError`` naming the Actor), so no ``diplomacy_round_completed``
    event is written, the completed-round counter stays at zero and Diplomacy stays open
    (testing.md §26).
    """
    client = _client(db_engine)
    with pytest.raises(RuntimeError, match="belvar|injected"):  # AutonomousGameError is one
        _run(
            client,
            short_game,
            cons_concurrency=3,
            diplomacy_concurrency=diplomacy_concurrency,
            player_factory=_decision_factory(runner_cls=_FailsInDiplomacy),
            diplomacy_mode="bounded_rounds",
            diplomacy_rounds_per_turn=1,
        )

    with Session(db_engine) as session:
        games = session.exec(select(Game)).all()
        assert len(games) == 1
        game = games[0]
        assert game.phase is GamePhase.diplomacy and game.turn == 1
        assert game.diplomacy_rounds_completed == 0
        event_types = {
            event.type for event in session.exec(
                select(GameEvent).where(GameEvent.game_id == game.id)
            )
        }
    assert "diplomacy_round_completed" not in event_types
    assert "diplomacy_closed" not in event_types


def _shape(export: dict[str, Any]) -> dict[str, Any]:
    """The behaviour-relevant shape of a completed game, independent of ids and timestamps."""
    return {
        "status": export["game"]["status"],
        "turn": export["game"]["turn"],
        "rng_draw_index": export["game"]["rng_draw_index"],
        "arguments": len(export["arguments"]),
        "cons_per_argument": sorted(
            sorted(c["actor_id"] for c in export["cons"] if c["argument_id"] == a["id"])
            for a in export["arguments"]
        ),
        "participations": len(export["con_participation"]),
        "adjudications": sorted(
            (a["mode"], a["roll"], a["success"]) for a in export["adjudications"]
        ),
        "effects": len(export["effects"]),
        "agent_runs": len(export["agent_runs"]),
    }


@pytest.mark.slow
def test_cons_concurrency_completes_game_with_same_shape_as_serial(db_engine: Engine, short_game: Path) -> None:
    client = _client(db_engine)
    _, serial = _run(client, short_game, cons_concurrency=1)
    _, concurrent = _run(client, short_game, cons_concurrency=3)

    assert serial.export["game"]["status"] == "complete"
    assert concurrent.export["game"]["status"] == "complete"
    assert concurrent.failures == []
    assert _shape(concurrent.export) == _shape(serial.export)
    # Every opponent of every Argument completed its participation exactly once.
    for argument in concurrent.export["arguments"]:
        opponents = {
            p["actor_id"]
            for p in concurrent.export["con_participation"]
            if p["argument_id"] == argument["id"]
        }
        assert opponents == set(ACTORS) - {argument["actor_id"]}
    # Provenance is recorded once per activation in both modes.
    assert len(concurrent.export["agent_runs"]) == len(concurrent.activations)


@pytest.mark.slow
def test_i028_006_diplomacy_concurrency_completes_with_same_shape_as_serial(
    db_engine: Engine, short_game: Path,
) -> None:
    client = _client(db_engine)
    _, serial = _run(client, short_game, cons_concurrency=3, diplomacy_concurrency=1)
    _, concurrent = _run(client, short_game, cons_concurrency=3, diplomacy_concurrency=3)

    assert concurrent.export["game"]["status"] == "complete"
    assert concurrent.failures == []
    assert _shape(concurrent.export) == _shape(serial.export)


def test_i042_actor_model_keeps_one_event_loop_across_cross_phase_activations(
    db_engine: Engine, short_game: Path,
) -> None:
    loops: dict[str, asyncio.AbstractEventLoop] = {}
    closed_on: dict[str, asyncio.AbstractEventLoop] = {}

    class CloseProbe:
        def __init__(self, actor_id: str):
            self.actor_id = actor_id

        async def close(self) -> None:
            closed_on[self.actor_id] = asyncio.get_running_loop()

    class LoopAffineModel(WrapperModel):
        def __init__(self, wrapped, actor_id: str):
            super().__init__(wrapped)
            self.actor_id = actor_id
            self.client = CloseProbe(actor_id)

        async def request(self, messages, model_settings, model_request_parameters):  # noqa: ANN001
            loop = asyncio.get_running_loop()
            previous = loops.setdefault(self.actor_id, loop)
            if loop is not previous:
                raise RuntimeError(
                    f"model client for {self.actor_id} moved to a different event loop"
                )
            return await self.wrapped.request(
                messages, model_settings, model_request_parameters
            )

    def factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        model = LoopAffineModel(model_from_decision_policy(default_decision_policy), actor_id)
        return LifecycleRunner(ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model,
            config=PlayerConfig(model="fake/loop-affine", activation_mode="single_shot"),
            provider="openrouter",
        ))

    _, result = _run(
        _client(db_engine),
        short_game,
        cons_concurrency=3,
        diplomacy_concurrency=3,
        player_factory=factory,
    )

    assert result.failures == []
    assert set(loops) == set(ACTORS)
    assert closed_on == loops
    assert all(loop.is_closed() for loop in loops.values())
    assert not any(t.name.startswith("amaw-actor-") for t in threading.enumerate())


def test_i028_001_eligible_diplomacy_activations_overlap(db_engine: Engine, short_game: Path) -> None:
    barrier = threading.Barrier(len(ACTORS), timeout=10)
    overlapped = threading.Event()

    def policy(packet, idx, info):
        if packet.get("phase") == "diplomacy" and packet.get("turn") == 1:
            barrier.wait()
            overlapped.set()
        return default_decision_policy(packet, idx, info)

    _, result = _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=3,
        player_factory=_decision_factory(policy),
    )
    assert result.export["game"]["status"] == "complete"
    assert overlapped.is_set()


def test_i028_002_diplomacy_concurrency_respects_bound(db_engine: Engine, short_game: Path) -> None:
    lock = threading.Lock()
    pair = threading.Barrier(2, timeout=10)
    # Only the first two activations meet; a later one waiting on the barrier would find no
    # partner and stall the test for the whole timeout.
    paired = threading.Event()
    active = 0
    maximum = 0

    def policy(packet, idx, info):
        nonlocal active, maximum
        if packet.get("phase") == "diplomacy" and packet.get("turn") == 1:
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                if active <= 2 and not paired.is_set():
                    pair.wait()
                    paired.set()
                time.sleep(0.01)
            finally:
                with lock:
                    active -= 1
        return default_decision_policy(packet, idx, info)

    _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=2,
        player_factory=_decision_factory(policy),
    )
    assert maximum == 2


def test_i028_003_same_round_reads_use_round_start_snapshot(db_engine: Engine, short_game: Path) -> None:
    message_committed = threading.Event()
    seen_by_belvar: list[str] = []
    delivered_to_belvar: list[str] = []
    belvar_packet_captured = False
    snapshot_checked = False

    class SnapshotRunner(LifecycleRunner):
        def step(self, context=None):
            nonlocal snapshot_checked
            if (
                not snapshot_checked
                and self.player.actor_id == "belvar"
                and (context or {}).get("phase") == "diplomacy"
            ):
                assert message_committed.wait(10)
                result = self.player.tools.read_messages()
                seen_by_belvar.extend(m["text"] for m in result.data["messages"])
                snapshot_checked = True
            return super().step(context)

    captured = {}

    def policy(packet, idx, info):
        nonlocal belvar_packet_captured
        if packet.get("phase") == "diplomacy":
            messages = []
            if packet["you"]["actor_id"] == "belvar" and not belvar_packet_captured:
                delivered_to_belvar.extend(
                    message["text"] for message in packet.get("messages_received", [])
                )
                belvar_packet_captured = True
            if packet["you"]["actor_id"] == "arkania" and packet.get("turn") == 1:
                messages = [{"recipients": ["belvar"], "public": False, "text": "ROUND-WRITE"}]
            return [_diplomacy_decision(packet, info, messages=messages)]
        return default_decision_policy(packet, idx, info)

    factory = _decision_factory(policy, runner_cls=SnapshotRunner, captured=captured)

    def wrapping_factory(tools, actor_id, actor_name):
        runner = factory(tools, actor_id, actor_name)
        if actor_id == "arkania":
            original = tools.send_message

            def send_message(**kwargs):
                result = original(**kwargs)
                if result.ok and not kwargs["public"]:
                    message_committed.set()
                return result

            tools.send_message = send_message
        return runner

    _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=3,
        player_factory=wrapping_factory,
    )
    assert "ROUND-WRITE" not in seen_by_belvar
    assert "ROUND-WRITE" not in delivered_to_belvar


def test_i028_004_only_private_recipient_is_reactivated_next_round(db_engine: Engine, short_game: Path) -> None:
    sent = False

    def policy(packet, idx, info):
        nonlocal sent
        if packet.get("phase") == "diplomacy":
            messages = []
            if packet["you"]["actor_id"] == "arkania" and packet.get("turn") == 1 and not sent:
                sent = True
                messages = [{"recipients": ["belvar"], "public": False, "text": "new terms"}]
            return [_diplomacy_decision(packet, info, messages=messages)]
        return default_decision_policy(packet, idx, info)

    _, result = _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=3,
        player_factory=_decision_factory(policy),
    )
    counts = {
        actor: sum(
            trace.turn == 1 and trace.phase == "diplomacy" and trace.actor_id == actor
            for trace in result.activations
        )
        for actor in ACTORS
    }
    assert counts == {"arkania": 1, "belvar": 2, "cestia": 1}


@pytest.mark.parametrize("ready_first", [False, True])
def test_i028_005_private_message_ready_race_never_closes_prematurely(
    db_engine: Engine, ready_first: bool, short_game: Path,
) -> None:
    diplomacy_calls = {actor: 0 for actor in ACTORS}

    def policy(packet, idx, info):
        if packet.get("phase") != "diplomacy":
            return default_decision_policy(packet, idx, info)
        actor = packet["you"]["actor_id"]
        diplomacy_calls[actor] += 1
        messages = []
        ready = True
        if packet.get("turn") == 1 and actor == "arkania":
            if ready_first and diplomacy_calls[actor] == 1:
                ready = False
            elif (not ready_first and diplomacy_calls[actor] == 1) or (
                ready_first and diplomacy_calls[actor] == 2
            ):
                messages = [{"recipients": ["belvar"], "public": False, "text": "react"}]
        return [_diplomacy_decision(packet, info, messages=messages, ready=ready)]

    _, result = _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=3,
        player_factory=_decision_factory(policy),
    )
    expected = {"arkania": 2, "belvar": 2, "cestia": 1} if ready_first else {
        "arkania": 1, "belvar": 2, "cestia": 1
    }
    actual = {
        actor: sum(
            trace.turn == 1 and trace.phase == "diplomacy" and trace.actor_id == actor
            for trace in result.activations
        )
        for actor in ACTORS
    }
    assert actual == expected


def test_cons_activations_actually_overlap(db_engine: Engine, short_game: Path) -> None:
    """Both opponents must be inside their Con activation at the same time for the game to finish.

    The policy blocks on a two-party Barrier before submitting its Con. A serial sweep would break
    the barrier (timeout) and the policy would report it; a concurrent sweep releases both.
    """
    barrier = threading.Barrier(len(ACTORS) - 1, timeout=10)
    broken: list[str] = []
    met = threading.Event()

    def overlapping_policy(packet: dict, tool_names: list[str], idx: int) -> list[Any]:
        actions = packet.get("available_actions", [])
        if "make_con" in actions and idx == 0:
            try:
                barrier.wait()
                met.set()
            except threading.BrokenBarrierError:
                broken.append(packet.get("you", {}).get("actor_id", "?"))
                return [TextPart(content="barrier broken")]
        return default_policy(packet, tool_names, idx)

    client = _client(db_engine)
    _, result = _run(client, short_game, cons_concurrency=2, player_factory=_factory(overlapping_policy))

    assert result.export["game"]["status"] == "complete"
    assert met.is_set()
    assert broken == []
    assert result.failures == []


def test_worker_exception_surfaces_as_autonomous_error(db_engine: Engine) -> None:
    class ExplodingInCons(LifecycleRunner):
        def step(self, context=None):
            context = context or self.observe()
            if context.get("phase") == "cons" and context.get("requires_input"):
                raise RuntimeError("worker blew up")
            return super().step(context)

    client = _client(db_engine)
    runner = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=_factory(runner_cls=ExplodingInCons),
        seed=SEED,
        cons_concurrency=3,
    )
    with pytest.raises(AutonomousGameError) as excinfo:
        runner.run()
    assert "worker blew up" in str(excinfo.value)
    assert runner.last_game_id is not None


def test_i028_007_diplomacy_worker_failure_waits_and_is_named(db_engine: Engine) -> None:
    barrier = threading.Barrier(len(ACTORS), timeout=10)
    finished: set[str] = set()
    lock = threading.Lock()

    class ExplodingInDiplomacy(LifecycleRunner):
        def step(self, context=None):
            if (context or {}).get("phase") == "diplomacy":
                barrier.wait()
                actor = self.player.actor_id
                try:
                    if actor == "belvar":
                        raise RuntimeError("diplomacy worker blew up")
                    time.sleep(0.02)
                    return super().step(context)
                finally:
                    with lock:
                        finished.add(actor)
            return super().step(context)

    runner = AutonomousGameRunner(
        _client(db_engine), admin_token=ADMIN_TOKEN, scenario=FIXTURE_DIR,
        player_factory=_decision_factory(runner_cls=ExplodingInDiplomacy), seed=SEED,
        diplomacy_concurrency=3, cons_concurrency=3,
        diplomacy_mode="bounded_rounds", diplomacy_rounds_per_turn=1,
    )
    with pytest.raises(AutonomousGameError, match="Actor 'belvar'.*worker blew up"):
        runner.run()
    assert finished == set(ACTORS)
    with Session(db_engine) as session:
        game = session.get(Game, uuid.UUID(runner.last_game_id))
        assert game.diplomacy_rounds_completed == 0
        assert game.phase is GamePhase.diplomacy
    assert not any(t.name.startswith("amaw-actor-") for t in threading.enumerate())


@pytest.mark.slow
def test_i028_008_diplomacy_provenance_once_in_sweep_order(db_engine: Engine, short_game: Path) -> None:
    _, result = _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=3,
        player_factory=_decision_factory(),
    )
    diplomacy = [t for t in result.activations if t.turn == 1 and t.phase == "diplomacy"]
    assert [t.actor_id for t in diplomacy] == list(ACTORS)
    assert len(result.export["agent_runs"]) == len(result.activations)
    assert result.activation_counts == {
        actor: sum(t.actor_id == actor for t in result.activations) for actor in ACTORS
    }

    failed_once = False

    def failing_policy(packet, idx, info):
        nonlocal failed_once
        if (
            not failed_once
            and packet.get("phase") == "diplomacy"
            and packet["you"]["actor_id"] == "belvar"
        ):
            failed_once = True
            raise RuntimeError("one recorded failure")
        return default_decision_policy(packet, idx, info)

    _, recovered = _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=3,
        player_factory=_decision_factory(failing_policy),
    )
    assert len(recovered.failures) == 1
    assert recovered.failures[0].actor_id == "belvar"
    assert len(recovered.export["agent_runs"]) == len(recovered.activations) + 1


def test_i042_diplomacy_concurrency_one_uses_affine_workers(db_engine: Engine, short_game: Path) -> None:
    seen_threads: set[str] = set()

    class RecordingRunner(LifecycleRunner):
        def step(self, context=None):
            outcome = super().step(context)
            if outcome.activation is not None and outcome.phase == "diplomacy":
                seen_threads.add(threading.current_thread().name)
            return outcome

    _run(
        _client(db_engine), short_game, cons_concurrency=3, diplomacy_concurrency=1,
        player_factory=_decision_factory(runner_cls=RecordingRunner),
    )
    assert seen_threads == {f"amaw-actor-{actor}_0" for actor in ACTORS}


def test_i028_010_runner_rejects_diplomacy_concurrency_below_one(db_engine: Engine) -> None:
    with pytest.raises(ValueError, match="diplomacy_concurrency"):
        AutonomousGameRunner(
            _client(db_engine), admin_token=ADMIN_TOKEN, scenario=FIXTURE_DIR,
            player_factory=_factory(), diplomacy_concurrency=0,
        )


def test_i028_011_manifest_records_both_concurrency_bounds(db_engine: Engine, short_game: Path) -> None:
    client = _client(db_engine)
    _, result = _run(client, short_game, cons_concurrency=3)
    assert result.export["run_manifest"]["coordinator"] == {
        "diplomacy_concurrency": 4,
        "cons_concurrency": 3,
        "sweep": "seat-order-rotated",
    }


def test_i028_real_threads_preserve_idempotency_and_single_phase_close(db_engine: Engine) -> None:
    client = _client(db_engine)
    admin_headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    created_response = client.post(
        "/admin/games", headers=admin_headers,
        json={"scenario": str(FIXTURE_DIR), "seed": SEED},
    )
    assert created_response.status_code == 201
    created = created_response.json()
    game_id = created["game_id"]
    assert client.post(
        f"/admin/games/{game_id}/start", headers=admin_headers, json={}
    ).status_code == 200

    tools = {
        actor: PlayerTools(client, game_id=game_id, token=token)
        for actor, token in created["seat_tokens"].items()
    }
    barrier = threading.Barrier(2, timeout=10)

    def duplicate_message():
        barrier.wait()
        return tools["arkania"].send_message(
            recipients=["belvar"], public=False, text="one semantic request",
            idempotency_key="i028-same-message",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: duplicate_message(), range(2)))
    assert all(result.ok for result in results)
    assert results[0].data["message"]["id"] == results[1].data["message"]["id"]

    assert tools["arkania"].ready(idempotency_key="i028-ready-a").ok
    close_barrier = threading.Barrier(2, timeout=10)

    def final_ready(actor):
        close_barrier.wait()
        return tools[actor].ready(idempotency_key=f"i028-ready-{actor}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        close_results = list(pool.map(final_ready, ["belvar", "cestia"]))
    assert all(result.ok for result in close_results)
    exported = client.get(f"/admin/games/{game_id}/export", headers=admin_headers).json()
    assert len([m for m in exported["messages"] if m["text"] == "one semantic request"]) == 1
    argument_starts = [
        e for e in exported["events"]
        if e["type"] == "phase_started" and e["payload"].get("phase") == "argument"
    ]
    assert len(argument_starts) == 1


def test_cons_concurrency_below_one_rejected(db_engine: Engine) -> None:
    with pytest.raises(ValueError):
        AutonomousGameRunner(
            _client(db_engine),
            admin_token=ADMIN_TOKEN,
            scenario=FIXTURE_DIR,
            player_factory=_factory(),
            cons_concurrency=0,
        )


def test_serial_sweep_is_the_default_shape_for_cons_concurrency_one(db_engine: Engine, short_game: Path) -> None:
    """A serial Cons sweep still uses each persistent Actor-affine model worker."""
    seen_threads: set[str] = set()

    class RecordingRunner(LifecycleRunner):
        def step(self, context=None):
            outcome = super().step(context)
            if outcome.activation is not None and outcome.phase == "cons":
                seen_threads.add(threading.current_thread().name)
            return outcome

    client = _client(db_engine)
    _run(client, short_game, cons_concurrency=1, player_factory=_factory(runner_cls=RecordingRunner))
    assert seen_threads == {f"amaw-actor-{actor}_0" for actor in ACTORS}


def test_every_con_activation_runs_on_the_pool_when_concurrent(db_engine: Engine, short_game: Path) -> None:
    """No Con activation may slip onto the coordinator thread because the phase flipped mid-sweep."""
    seen_threads: set[str] = set()

    class RecordingRunner(LifecycleRunner):
        def step(self, context=None):
            outcome = super().step(context)
            if outcome.activation is not None and outcome.phase == "cons":
                seen_threads.add(threading.current_thread().name)
            return outcome

    client = _client(db_engine)
    _run(client, short_game, cons_concurrency=3, player_factory=_factory(runner_cls=RecordingRunner))
    assert seen_threads
    assert threading.main_thread().name not in seen_threads
