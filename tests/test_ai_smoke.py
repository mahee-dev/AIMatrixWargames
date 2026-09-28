"""T-044 acceptance: autonomous six-turn AI smoke test (testing.md §20).

The default suite proves the full orchestration offline with real ``ReferencePlayer`` /
``LifecycleRunner`` instances backed by deterministic FunctionModels.  The marked test replaces
those fakes with the configured OpenRouter model for all three Actors and the fixed AI Umpire.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import Engine

from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import (
    PLAYER_PROMPT_VERSION,
    GuardrailConfig,
    PlayerConfig,
    ReferencePlayer,
)
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from agents.umpire import AIUmpire
from app.api import create_app
from app.config import Settings
from tests.agent_fakes import default_policy, model_from_policy
from tests.api_helpers import FIXTURE_DIR

# The live-play scenario: same world and rules as the fixture, triangular zero-sum objectives so
# the Actors cannot all be satisfied by one forum (dev/plans/2026-09-12-northern-strait-…).
SMOKE_SCENARIO = Path(__file__).parents[1] / "scenarios" / "northern-strait"
ACTORS = ("arkania", "belvar", "cestia")
ADMIN_TOKEN = "admin-secret-token"
SMOKE_MODEL = "deepseek/deepseek-v4-flash-0731"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _offline_player_factory(
    tools: PlayerTools, actor_id: str, actor_name: str
) -> LifecycleRunner:
    player = ReferencePlayer(
        tools,
        actor_id=actor_id,
        actor_name=actor_name,
        model=model_from_policy(default_policy),
        config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
    )
    return LifecycleRunner(player)


def _assert_no_private_data_leaks(
    client: TestClient, game_id: str, seat_tokens: dict[str, str], export: dict
) -> None:
    actors = {actor["id"]: actor for actor in export["actors"]}
    messages = export["messages"]
    notes = export["notes"]

    for viewer in ACTORS:
        auth = _headers(seat_tokens[viewer])
        context_response = client.get(f"/games/{game_id}/context", headers=auth)
        assert context_response.status_code == 200, context_response.text
        context_blob = json.dumps(context_response.json())

        for other in set(ACTORS) - {viewer}:
            private = actors[other]
            assert private["private_brief"] not in context_blob
            for objective in private["objectives"]:
                assert objective not in context_blob
            for fact in private["private_information"]:
                assert fact not in context_blob

        visible_messages = client.get(f"/games/{game_id}/messages", headers=auth)
        assert visible_messages.status_code == 200, visible_messages.text
        visible_message_ids = {m["id"] for m in visible_messages.json()["messages"]}
        expected_message_ids = {
            m["id"]
            for m in messages
            if m["public"] or viewer == m["sender_actor_id"] or viewer in m["recipients"]
        }
        assert visible_message_ids == expected_message_ids

        visible_notes = client.get(f"/games/{game_id}/notes", headers=auth)
        assert visible_notes.status_code == 200, visible_notes.text
        visible_note_ids = {n["id"] for n in visible_notes.json()["notes"]}
        assert visible_note_ids == {n["id"] for n in notes if n["actor_id"] == viewer}

    # D-029: the Umpire sees hidden facts but its Effects and rationales are Actor-visible, so a
    # hidden fact must never be restated verbatim in either.
    # Verbatim-only: a paraphrased leak is not caught.
    hidden_texts = {
        fact for actor in actors.values() for fact in actor["private_information"]
    } | {e["description"] for e in export["effects"] if e["visibility"] == "private"}
    umpire_text = " ".join(
        [e["description"] for e in export["effects"] if e["visibility"] == "public"]
        + [a.get("rationale") or "" for a in export["adjudications"]]
        + [t for a in export["adjudications"] for t in a.get("success_effects") or []]
        + [t for a in export["adjudications"] for t in a.get("failure_effects") or []]
    )
    for hidden in hidden_texts:
        assert hidden not in umpire_text, hidden


def _write_partial_export(
    client: TestClient, game_id: str | None, run: dict, failure: str
) -> Path | None:
    """On a failed run, keep the admin export of the game as far as it got (I-020/I-023)."""
    target = os.environ.get("AMAW_SMOKE_EXPORT_DIR")
    if not target or game_id is None:
        return None
    run_dir = Path(target) / game_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps({**run, "failure": failure}, indent=2, default=str)
    )
    response = client.get(f"/admin/games/{game_id}/export", headers=_headers(ADMIN_TOKEN))
    if response.status_code == 200:
        (run_dir / "export.json").write_text(json.dumps(response.json(), indent=2))
    return run_dir


def _write_run_provenance(result, run: dict | None = None) -> Path | None:
    """Persist a run's export and ephemeral provenance for post-run analysis (I-020).

    Only when ``AMAW_SMOKE_EXPORT_DIR`` is set. Seat tokens and the admin token are never
    written: the export is the T-043 administrative document (which carries no tokens) and the
    traces are dataclasses with no credential fields. ``run`` is the configuration that makes
    files from different runs comparable or not (prompt versions, models, caps — D-027/D-028
    say v1.1 and v1.2 runs are not comparable), written as ``run.json`` when given.
    """
    target = os.environ.get("AMAW_SMOKE_EXPORT_DIR")
    if not target:
        return None
    run_dir = Path(target) / result.game_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if run is not None:
        (run_dir / "run.json").write_text(json.dumps(run, indent=2, default=str))
    (run_dir / "export.json").write_text(json.dumps(result.export, indent=2, default=str))
    (run_dir / "activations.json").write_text(
        json.dumps([dataclasses.asdict(t) for t in result.activations], indent=2, default=str)
    )
    (run_dir / "failures.json").write_text(
        json.dumps([dataclasses.asdict(f) for f in result.failures], indent=2, default=str)
    )
    return run_dir


@pytest.mark.slow
def test_autonomous_coordinator_completes_six_turns_offline(
    db_engine: Engine, caplog
) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings,
                     umpire=deterministic_umpire())
    client = TestClient(app, raise_server_exceptions=False)
    runner = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=_offline_player_factory,
        stream_game_details=True,
    )

    with caplog.at_level(logging.INFO, logger="agents.autonomous"):
        result = runner.run()

    assert result.export["game"]["status"] == "complete"
    assert result.export["game"]["turn"] == 6
    assert len(result.export["arguments"]) == 18
    assert len(result.export["adjudications"]) == 18
    assert set(result.seat_tokens) == set(ACTORS)
    assert all(result.activation_counts[actor] > 0 for actor in ACTORS)
    assert result.failures == []
    assert {trace.model_requested for trace in result.activations} == {"fake/deterministic"}
    assert result.export["run_manifest"]["manifest_sha256"]
    assert result.export["model_assignments"] == {
        actor: "fake/deterministic" for actor in ACTORS
    }
    assert len(result.export["agent_runs"]) == len(result.activations)
    assert result.summary == result.export["run_summary"]
    assert result.summary["player_model_calls"] == sum(
        int((trace.usage or {}).get("requests") or 1) for trace in result.activations
    )
    assert "Total runtime " in result.summary_text
    assert "Provider wait " in result.summary_text
    assert "By phase:" in result.summary_text
    assert "Estimated cost " in result.summary_text
    audit = "\n".join(record.getMessage() for record in caplog.records)
    # The per-activation log names tool *calls* (a repeated name is a repeated call) and the tool
    # *grant* is logged once per Actor, so the two can never be confused again.
    assert " tools=" not in audit
    assert "tool_calls=" in audit
    for actor in ACTORS:
        offered = re.search(rf"autonomous actor={actor} tools_offered=(\S+)", audit)
        assert offered is not None, actor
        assert set(offered.group(1).split(",")) == {
            "observe", "read_messages", "send_message", "search_resources", "read_resource",
            "make_argument", "make_con", "pass_con", "ready", "update_strategic_memory",
        }
    # Sweep/result handling rotates by turn (I-016) and stays deterministic even though the
    # independent diplomacy writes may commit in worker-completion order (I-028).
    order_by_turn: dict[int, list[str]] = {}
    for trace in result.activations:
        if trace.phase == "diplomacy" and trace.turn is not None:
            order_by_turn.setdefault(trace.turn, []).append(trace.actor_id)
    assert order_by_turn == {
        1: ["arkania", "belvar", "cestia"],
        2: ["belvar", "cestia", "arkania"],
        3: ["cestia", "arkania", "belvar"],
        4: ["arkania", "belvar", "cestia"],
        5: ["belvar", "cestia", "arkania"],
        6: ["cestia", "arkania", "belvar"],
    }
    assert "sweep=belvar,cestia,arkania" in audit
    identity_fields = {
        "messages": "id",
        "arguments": "id",
        "cons": "id",
        "adjudications": "argument_id",
        "effects": "id",
        "notes": "id",
        "events": "id",
        "resource_access": "id",
        "agent_runs": "id",
    }
    for collection, identity_field in identity_fields.items():
        for row in result.export[collection]:
            assert str(row[identity_field]) in audit
    assert ADMIN_TOKEN not in audit
    assert all(token not in audit for token in result.seat_tokens.values())
    _assert_no_private_data_leaks(
        client, result.game_id, result.seat_tokens, result.export
    )


@pytest.mark.slow
def test_northern_strait_scenario_completes_six_turns_offline(db_engine: Engine) -> None:
    """The live-play package is a valid, completable scenario with the intended conflict."""
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    client = TestClient(app, raise_server_exceptions=False)
    result = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=SMOKE_SCENARIO,
        player_factory=_offline_player_factory,
    ).run()

    assert result.export["game"]["status"] == "complete"
    assert result.export["game"]["turn"] == 6
    assert result.export["game"]["scenario_id"] == "northern-strait"
    assert result.failures == []
    objectives = {a["id"]: a["objectives"] for a in result.export["actors"]}
    # The triangle: each pair of Actors has one dispute (design note, decision 2).
    assert any("Arkanian authority over transit scheduling" in o for o in objectives["arkania"])
    assert any("Prevent any Arkanian authority" in o for o in objectives["belvar"])
    assert any("naval access agreement at a Cestian port" in o for o in objectives["belvar"])
    assert any("free of any foreign military presence" in o for o in objectives["cestia"])
    assert any("transit fees" in o for o in objectives["arkania"])
    assert any("toll-free passage" in o for o in objectives["cestia"])
    _assert_no_private_data_leaks(client, result.game_id, result.seat_tokens, result.export)


@pytest.mark.slow
def test_smoke_provenance_is_written_when_export_dir_is_set(
    db_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    client = TestClient(app, raise_server_exceptions=False)
    result = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=_offline_player_factory,
    ).run()

    monkeypatch.setenv("AMAW_SMOKE_EXPORT_DIR", str(tmp_path))
    written = _write_run_provenance(
        result, run={"player_prompt_version": PLAYER_PROMPT_VERSION, "cap": 10}
    )

    assert written == tmp_path / result.game_id
    assert json.loads((written / "run.json").read_text()) == {
        "player_prompt_version": PLAYER_PROMPT_VERSION,
        "cap": 10,
    }
    export = json.loads((written / "export.json").read_text())
    assert export["game"]["status"] == "complete"
    activations = json.loads((written / "activations.json").read_text())
    assert len(activations) == len(result.activations)
    assert activations[0]["tool_call_names"] == result.activations[0].tool_call_names
    assert json.loads((written / "failures.json").read_text()) == []
    # Never write credentials.
    blob = "".join(p.read_text() for p in written.iterdir())
    assert ADMIN_TOKEN not in blob
    assert all(token not in blob for token in result.seat_tokens.values())

    monkeypatch.delenv("AMAW_SMOKE_EXPORT_DIR")
    assert _write_run_provenance(result) is None


def test_unrecoverable_player_failure_reports_safe_diagnostics(db_engine: Engine) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    client = TestClient(app, raise_server_exceptions=False)

    def stalled_factory(
        tools: PlayerTools, actor_id: str, actor_name: str
    ) -> LifecycleRunner:
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=FunctionModel(
                lambda messages, info: ModelResponse(parts=[TextPart(content="done")])
            ),
            config=PlayerConfig(
                model="fake/stalled",
                activation_mode="tool_loop",
                guardrails=GuardrailConfig(max_activations_per_opportunity=1),
            ),
        )
        return LifecycleRunner(player)

    runner = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=stalled_factory,
    )
    with pytest.raises(
        AutonomousGameError,
        match=r"category='stall'.*no progress after 1 activations.*counts=\{'activations': 1\}",
    ):
        runner.run()
    # The game the failed run created stays addressable so its export can still be pulled.
    assert runner.last_game_id is not None
    assert client.get(
        f"/admin/games/{runner.last_game_id}/export", headers=_headers(ADMIN_TOKEN)
    ).status_code == 200


@pytest.mark.llm
@pytest.mark.parametrize("activation_mode", ["single_shot", "tool_loop"])
def test_ai_smoke_three_players_six_turns_no_intervention(
    db_engine: Engine, activation_mode: str
) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN)
    if settings.openrouter_api_key is None:
        pytest.skip("no OPENROUTER_API_KEY")

    assert settings.player_model == SMOKE_MODEL
    assert settings.umpire_model == SMOKE_MODEL

    umpire = AIUmpire.for_openrouter(
        settings,
        model=settings.umpire_model,
        temperature=0.0,
        # `low` reasoning is the speed lever (fewer reasoning tokens, faster return) and it also
        # protects the completion budget: this model's reasoning tokens share `max_tokens` with the
        # final JSON. `medium` at 2000 truncated the JSON once the context grew (run 1, turn 2);
        # `low` at 4000 still truncated on the second adjudication of run 4 (`finish_reason=
        # length`). `max_tokens` caps only the completion, not the growing input, so 8000 is
        # headroom, and I-026 gives a truncated completion one retry before the game pauses.
        reasoning_effort="low",
        # Generous on purpose: every turn-4 pause so far was throughput, not play — a truncated
        # completion (run 4) or a ReadTimeout on the 60 s client default (run 5) as the Umpire's
        # reasoning grew with the context. The smoke measures autonomous play; provider speed is
        # a deployment concern. A genuinely stuck call still fails through the single retry.
        max_tokens=16000,
        timeout=600.0,
    )
    app = create_app(engine=db_engine, settings=settings, umpire=umpire)
    client = TestClient(app, raise_server_exceptions=False)

    # A smoke run proves autonomous completion, not unconstrained diplomacy. Permit two
    # authoritative Messages per Actor per phase: one to state a position and one to answer a
    # Message that re-opened the Actor's ready state (TEST-PHASE-003), while preventing the
    # unbounded chatter seen in the first live attempt (27 Messages before one Argument).
    # The cap is atomic (I-021) and the remaining budget is stated in every packet (I-015).
    guardrails = GuardrailConfig(
        max_llm_iterations=8,
        max_tool_calls=12,
        max_messages_per_phase=2,
        max_activations_per_opportunity=3,
    )
    player_config = PlayerConfig(
        model=settings.player_model,
        temperature=0.0,
        reasoning_effort="low",
        max_tokens=4000,
        timeout=600.0,
        activation_mode=activation_mode,
        guardrails=guardrails,
    )

    def live_player_factory(
        tools: PlayerTools, actor_id: str, actor_name: str
    ) -> LifecycleRunner:
        player = ReferencePlayer.for_openrouter(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            config=player_config,
            settings=settings,
        )
        return LifecycleRunner(player)

    run_config = {
        "scenario": SMOKE_SCENARIO.name,
        "player_model": player_config.model,
        "player_prompt_version": player_config.prompt_version,
        "player_activation_mode": player_config.activation_mode,
        "player_temperature": player_config.temperature,
        "player_reasoning_effort": player_config.reasoning_effort,
        "guardrails": dataclasses.asdict(guardrails),
        "umpire_model": umpire.model,
        "umpire_prompt_version": umpire.prompt_version,
    }
    runner = AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario=SMOKE_SCENARIO,
        player_factory=live_player_factory,
        stream_game_details=True,
    )
    try:
        result = runner.run()
    except AutonomousGameError as exc:
        # A paused/stalled game is exactly the run worth analysing: keep its export (I-020/I-023).
        _write_partial_export(client, runner.last_game_id, run_config, str(exc))
        pytest.fail(f"{exc}; umpire_failure={umpire.last_failure}")
    _write_run_provenance(result, run=run_config)

    assert result.export["game"]["status"] == "complete"
    assert result.export["game"]["turn"] == 6
    assert len(result.export["arguments"]) == 18
    assert all(
        any(argument["actor_id"] == actor for argument in result.export["arguments"])
        for actor in ACTORS
    )
    assert {trace.model_requested for trace in result.activations} == {SMOKE_MODEL}
    assert umpire.model == SMOKE_MODEL
    assert all(
        adjudication["roll"] is not None
        for adjudication in result.export["adjudications"]
        if adjudication["mode"] == "probabilistic"
    )
    probabilities = sorted(
        a["probability"]
        for a in result.export["adjudications"]
        if a["mode"] == "probabilistic" and a["probability"] is not None
    )
    modes = sorted(a["mode"] for a in result.export["adjudications"])
    logging.getLogger("agents.autonomous").info(
        "smoke adjudication summary modes=%s probabilities=%s", modes, probabilities
    )
    assert isinstance(result.failures, list)
    _assert_no_private_data_leaks(
        client, result.game_id, result.seat_tokens, result.export
    )
