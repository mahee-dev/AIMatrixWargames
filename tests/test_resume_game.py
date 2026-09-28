"""I-048 interruption-safe autonomous game resumption."""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import select

from app import scenarios
from app.api import create_app
from app.config import Settings
from app.database import session_scope
from app.models import ActorStrategicState, Game, GameStatus, RunManifest, Seat
from app.security import hash_token
from agents.guardrails import GuardrailConfig
from tests.api_helpers import FIXTURE_DIR, bearer
from tests.benchmark_helpers import SHORT_TURNS


ADMIN = "resume-admin"


def _created(engine: Engine):
    return scenarios.create_game_from_scenario(engine, scenarios.load_scenario(FIXTURE_DIR))


def _manifest(engine: Engine, game_id: uuid.UUID, *, recipe: dict | None = None) -> None:
    manifest = {
        "game_id": str(game_id),
        "scenario": {"id": "deterministic-game"},
        "seed": 424242,
        "player": {},
        "umpire": {},
        "code_version": "test-code",
        "benchmark": {},
    }
    if recipe is not None:
        manifest["resume"] = recipe
    with session_scope(engine) as session:
        session.add(RunManifest(game_id=game_id, manifest=manifest, manifest_sha256="a" * 64))


def _recipe() -> dict:
    return {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {
                "arkania": "model/p", "belvar": "model/p", "cestia": "model/p"
            },
            "temperature": 0,
            "max_tokens": 1000,
            "timeout": 30,
            "reasoning_effort": None,
            "activation_mode": "single_shot",
            "prompt_version": "player-v1.6",
            "guardrails": dataclasses.asdict(GuardrailConfig()),
        },
        "umpire": {
            "model": "model/u",
            "temperature": 0,
            "max_tokens": 1000,
            "timeout": 30,
            "reasoning_effort": None,
        },
        "coordinator": {
            "max_cycles": 1000,
            "cons_concurrency": 2,
            "diplomacy_concurrency": 2,
        },
    }


def test_i048_001_admin_rotation_is_atomic_hash_only_and_revokes_old_tokens(
    db_engine: Engine,
) -> None:
    creation = _created(db_engine)
    _manifest(db_engine, creation.game_id, recipe=_recipe())
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        game.status = GameStatus.paused
        session.add(game)

    client = TestClient(
        create_app(
            engine=db_engine,
            settings=Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None),
        ),
        raise_server_exceptions=False,
    )
    assert client.post(
        f"/admin/games/{creation.game_id}/seat-tokens/rotate",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).status_code == 403
    response = client.post(
        f"/admin/games/{creation.game_id}/seat-tokens/rotate", headers=bearer(ADMIN)
    )

    assert response.status_code == 200, response.text
    replacements = response.json()["seat_tokens"]
    assert set(replacements) == set(creation.seat_tokens)
    assert all(replacements[a] != creation.seat_tokens[a] for a in replacements)
    with session_scope(db_engine) as session:
        seats = list(session.exec(select(Seat).where(Seat.game_id == creation.game_id)).all())
        assert {seat.token_hash for seat in seats} == {
            hash_token(token) for token in replacements.values()
        }
        assert not ({seat.token_hash for seat in seats} & {
            hash_token(token) for token in creation.seat_tokens.values()
        })
        assert all(token not in repr(seats) for token in replacements.values())
        assert session.get(Game, creation.game_id).status == GameStatus.running

    for old in creation.seat_tokens.values():
        assert client.get(f"/games/{creation.game_id}/context", headers=bearer(old)).status_code == 401
    for new in replacements.values():
        assert client.get(f"/games/{creation.game_id}/context", headers=bearer(new)).status_code == 200


def test_i048_005_rotation_preserves_pending_strategic_memory_delivery(
    db_engine: Engine,
) -> None:
    creation = _created(db_engine)
    _manifest(db_engine, creation.game_id, recipe=_recipe())
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        game.status = GameStatus.running
        session.add(game)
    client = TestClient(
        create_app(engine=db_engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None)),
        raise_server_exceptions=False,
    )
    first = client.post(
        f"/games/{creation.game_id}/activations",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    replacements = client.post(
        f"/admin/games/{creation.game_id}/seat-tokens/rotate", headers=bearer(ADMIN)
    ).json()["seat_tokens"]
    replay = client.post(
        f"/games/{creation.game_id}/activations", headers=bearer(replacements["arkania"])
    ).json()

    assert replay["delivery_receipt"] == first["delivery_receipt"]
    with session_scope(db_engine) as session:
        state = session.get(ActorStrategicState, (creation.game_id, "arkania"))
        assert state.pending_receipt == first["delivery_receipt"]


@pytest.mark.parametrize("loss_boundary", ["actor_commit", "adjudication_commit"])
@pytest.mark.slow
def test_i048_002_003_004_resume_reobserves_existing_game_after_committed_mutations(
    db_engine: Engine, loss_boundary: str, short_game: Path,
) -> None:
    from agents.autonomous import AutonomousGameError, AutonomousGameRunner
    from agents.lifecycle import LifecycleRunner
    from agents.player import PlayerConfig, ReferencePlayer
    from agents.scripted import deterministic_umpire
    from tests.agent_fakes import default_policy, model_from_policy

    def factory(tools, actor_id: str, actor_name: str) -> LifecycleRunner:  # noqa: ANN001
        return LifecycleRunner(
            ReferencePlayer(
                tools,
                actor_id=actor_id,
                actor_name=actor_name,
                model=model_from_policy(default_policy),
                config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
            )
        )

    client = TestClient(
        create_app(
            engine=db_engine,
            settings=Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None),
            umpire=deterministic_umpire(),
        ),
        raise_server_exceptions=False,
    )
    interrupted = AutonomousGameRunner(
        client, admin_token=ADMIN, scenario=short_game, player_factory=factory
    )
    if loss_boundary == "actor_commit":
        original_sweep = interrupted._sweep

        def lose_after_actor_commit(*args, **kwargs):  # noqa: ANN002, ANN003
            original_sweep(*args, **kwargs)
            raise AutonomousGameError("simulated process loss after committed mutation")

        interrupted._sweep = lose_after_actor_commit  # type: ignore[method-assign]
    else:
        original_admin_request = interrupted._admin_request

        def lose_after_adjudication_commit(method, path, **kwargs):  # noqa: ANN001, ANN003
            response = original_admin_request(method, path, **kwargs)
            if method == "POST" and path.endswith("/advance"):
                raise AutonomousGameError("simulated process loss after committed mutation")
            return response

        interrupted._admin_request = lose_after_adjudication_commit  # type: ignore[method-assign]
    with pytest.raises(AutonomousGameError, match="simulated process loss"):
        interrupted.run()
    game_id = interrupted.last_game_id
    assert game_id is not None
    descriptor = client.get(
        f"/admin/games/{game_id}/resume", headers=bearer(ADMIN)
    ).json()
    assert descriptor["manifest"]["resume"]["schema"] == "autonomous-resume-v1"
    assert set(descriptor["manifest"]["resume"]["player"]["assignments"]) == {
        "arkania", "belvar", "cestia"
    }
    # The first process's raw tokens are deliberately unavailable here; this mirrors a real
    # restart. The one durable identity is the Game plus its credential-free manifest.
    resumed = AutonomousGameRunner(
        client,
        admin_token=ADMIN,
        scenario="unused-on-resume",
        player_factory=factory,
        run_configuration={"resume": descriptor["manifest"]["resume"]},
    ).resume(game_id)

    assert resumed.game_id == game_id
    assert resumed.export["game"]["status"] == "complete"
    assert len(resumed.export["arguments"]) == 3 * SHORT_TURNS
    with session_scope(db_engine) as session:
        assert len(list(session.exec(select(Game)).all())) == 1
        assert session.get(RunManifest, uuid.UUID(game_id)).manifest_sha256 == descriptor[
            "manifest_sha256"
        ]


@pytest.mark.slow
def test_i053_011_resume_does_not_reset_or_duplicate_completed_final_round(
    db_engine: Engine, short_game: Path,
) -> None:
    from agents.autonomous import AutonomousGameError, AutonomousGameRunner
    from agents.lifecycle import LifecycleRunner
    from agents.player import PlayerConfig, ReferencePlayer
    from agents.scripted import deterministic_umpire
    from tests.agent_fakes import (
        current_memory,
        decision_call,
        default_decision_policy,
        model_from_decision_policy,
    )

    def policy(packet, idx, info):  # noqa: ANN001
        if packet.get("phase") == "diplomacy":
            return [decision_call(info, {
                "messages": [], "strategic_memory": current_memory(packet), "ready": False,
            })]
        return default_decision_policy(packet, idx, info)

    def factory(tools, actor_id: str, actor_name: str) -> LifecycleRunner:  # noqa: ANN001
        return LifecycleRunner(ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model_from_decision_policy(policy),
            config=PlayerConfig(model="fake/bounded", activation_mode="single_shot"),
        ))

    client = TestClient(create_app(
        engine=db_engine,
        settings=Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None),
        umpire=deterministic_umpire(),
    ), raise_server_exceptions=False)
    interrupted = AutonomousGameRunner(
        client,
        admin_token=ADMIN,
        scenario=short_game,
        player_factory=factory,
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=1,
    )
    original_sweep = interrupted._sweep
    lost = False

    def lose_after_final_round(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal lost
        result = original_sweep(*args, **kwargs)
        if not lost:
            lost = True
            raise AutonomousGameError("simulated loss after final round checkpoint")
        return result

    interrupted._sweep = lose_after_final_round  # type: ignore[method-assign]
    with pytest.raises(AutonomousGameError, match="final round checkpoint"):
        interrupted.run()
    game_id = interrupted.last_game_id
    descriptor = client.get(
        f"/admin/games/{game_id}/resume", headers=bearer(ADMIN)
    ).json()

    resumed = AutonomousGameRunner(
        client,
        admin_token=ADMIN,
        scenario="unused-on-resume",
        player_factory=factory,
        diplomacy_mode="bounded_rounds",
        diplomacy_rounds_per_turn=1,
        run_configuration={"resume": descriptor["manifest"]["resume"]},
    ).resume(game_id)

    turn_one_rounds = [
        event for event in resumed.export["events"]
        if event["turn"] == 1 and event["type"] == "diplomacy_round_completed"
    ]
    assert len(turn_one_rounds) == 1
    assert turn_one_rounds[0]["payload"]["round"] == 1


@pytest.mark.parametrize("condition", ["complete", "missing_manifest", "legacy_manifest"])
def test_i048_006_rotation_refuses_non_resumable_games(
    db_engine: Engine, condition: str
) -> None:
    creation = _created(db_engine)
    if condition != "missing_manifest":
        _manifest(
            db_engine,
            creation.game_id,
            recipe=None if condition == "legacy_manifest" else _recipe(),
        )
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        game.status = GameStatus.complete if condition == "complete" else GameStatus.running
        session.add(game)
    before = dict(creation.seat_tokens)
    client = TestClient(
        create_app(engine=db_engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None)),
        raise_server_exceptions=False,
    )

    response = client.post(
        f"/admin/games/{creation.game_id}/seat-tokens/rotate", headers=bearer(ADMIN)
    )

    assert response.status_code == 409
    with session_scope(db_engine) as session:
        hashes = {s.token_hash for s in session.exec(select(Seat).where(Seat.game_id == creation.game_id))}
    assert hashes == {hash_token(token) for token in before.values()}
