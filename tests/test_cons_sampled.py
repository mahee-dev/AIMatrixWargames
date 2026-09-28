"""I-055: explicit, persisted sampled-sealed Con opposition experimental condition (D-053)."""

from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from fastapi.testclient import TestClient

from agents.autonomous import AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import PlayerConfig, ReferencePlayer
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from agents.umpire import FakeUmpire
from app import context, database, game_service, persistence, scenarios
from app.export import EXPORT_FORMAT_VERSION
from app.api import create_app
from app.config import Settings
from app.summary import build_game_summary, format_game_summary
from app.database import session_scope
from app.models import (
    Actor,
    AdjudicationMode,
    Argument,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
)
from app.umpire import AdjudicationProposal
from tests.agent_fakes import default_policy, model_from_policy
from tests.api_helpers import (
    FIXTURE_DIR,
    bearer,
    client,
    seed,
    start_to_argument,
    start_to_cons,
)
from tests.benchmark_helpers import SHORT_TURNS

ADMIN_TOKEN = "admin-secret-token"
SEED = 20260916


def _factory(policy=default_policy):
    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model_from_policy(policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
        )
        return LifecycleRunner(player)

    return player_factory


def _sampled_game(engine: Engine, *, challengers: int = 1, seed: int | None = None):
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    return scenarios.create_game_from_scenario(
        engine,
        scenario,
        seed=seed,
        con_mode="sampled_sealed",
        con_challengers_per_argument=challengers,
    )


def test_i055_002_sampled_sealed_selects_k_challengers_at_argument_acceptance(
    db_engine: Engine,
) -> None:
    creation = _sampled_game(db_engine, challengers=1)
    start_to_argument(db_engine, creation)

    argument = game_service.make_argument(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        action="Begin mobilisation of reserve forces.",
        desired_effect="Reserve formations begin deploying toward the frontier.",
        pros=["Mobilisation plans already exist."],
    )

    with Session(db_engine) as session:
        row = session.get(Argument, argument.id)
        assert row is not None
        assert row.challenger_actor_ids is not None
        assert len(row.challenger_actor_ids) == 1
        assert row.challenger_actor_ids[0] in {"belvar", "cestia"}
        types = [
            event.type
            for event in session.exec(
                select(GameEvent)
                .where(GameEvent.game_id == creation.game_id)
                .order_by(GameEvent.id)
            ).all()
        ]
    submitted = types.index("argument_submitted")
    assert types[submitted + 1] == "con_challengers_selected"
    assert types[submitted + 2] == "phase_started"


# --- helpers ------------------------------------------------------------------------------

_OK = AdjudicationProposal(
    mode=AdjudicationMode.automatic_success, rationale="clear", success_effects=["It works."]
)
_FIVE = ["a1", "a2", "a3", "a4", "a5"]


def _running_sampled_game(
    engine: Engine, *, actors: list[str] = _FIVE, challengers: int = 2, seed: int = 7,
    max_turns: int = 4,
) -> uuid.UUID:
    """A running 5-Actor sampled_sealed game in ARGUMENT, active on the first Actor."""
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="i055",
                turn=1,
                max_turns=max_turns,
                phase=GamePhase.argument,
                status=GameStatus.running,
                turn_order=list(actors),
                active_actor_id=actors[0],
                seed=seed,
                primary_arguments_per_actor=1,
                max_cons_per_actor=3,
                con_policy_version="con-policy-v1",
                con_mode="sampled_sealed",
                con_challengers_per_argument=challengers,
                con_challenger_cursor=seed % len(actors),
            ),
        )
        for actor_id in actors:
            persistence.create(
                session,
                Actor(
                    game_id=game.id, id=actor_id, name=actor_id, public_description="p",
                    private_brief="b",
                ),
            )
        return game.id


def _game(engine: Engine, game_id: uuid.UUID) -> Game:
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        assert game is not None
        session.expunge(game)
        return game


def _argument(engine: Engine, argument_id: uuid.UUID) -> Argument:
    with session_scope(engine) as session:
        row = session.get(Argument, argument_id)
        assert row is not None
        session.expunge(row)
        return row


def _submit(engine: Engine, game_id: uuid.UUID, key: str | None = None):
    game = _game(engine, game_id)
    if game.phase is GamePhase.diplomacy:  # a new turn: everyone readies, first Actor argues
        for actor_id in game.turn_order:
            game_service.ready(engine, game_id, actor_id)
        game = _game(engine, game_id)
    owner = game.active_actor_id
    assert owner is not None
    return game_service.make_argument(
        engine, game_id, actor_id=owner, action="Act", desired_effect="Effect",
        pros=["Because"], idempotency_key=key,
    )


def _resolve_active(engine: Engine, game_id: uuid.UUID) -> list[str]:
    """Argument → sealed challengers pass → adjudicate → advance. Returns the sealed set."""
    argument = _submit(engine, game_id)
    challengers = _argument(engine, argument.id).challenger_actor_ids
    assert challengers is not None
    for challenger in challengers:
        game_service.pass_con(engine, game_id, argument_id=argument.id, actor_id=challenger)
    assert _game(engine, game_id).phase is GamePhase.adjudication
    game_service.resolve_adjudication(engine, game_id, FakeUmpire(_OK))
    game_service.advance_after_resolution(engine, game_id)
    return list(challengers)


# --- TEST-I055-001 — open_cons default is unrestricted ----------------------------------------


def test_i055_001_open_cons_default_keeps_unrestricted_opposition(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)

    game = _game(db_engine, creation.game_id)
    assert game.con_policy_version == "con-policy-v1"
    assert game.con_mode == "open_cons"
    assert game.con_challengers_per_argument is None
    assert game.con_challenger_cursor is None
    assert _argument(db_engine, argument.id).challenger_actor_ids is None
    with session_scope(db_engine) as session:
        assert not session.exec(
            select(GameEvent).where(GameEvent.type == "con_challengers_selected")
        ).all()
    # Both opponents are awaited: one pass does not close the phase.
    game_service.pass_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="belvar")
    assert _game(db_engine, creation.game_id).phase is GamePhase.cons
    game_service.pass_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="cestia")
    assert _game(db_engine, creation.game_id).phase is GamePhase.adjudication


# --- TEST-I055-003 — deterministic, rotating, balanced schedule ---------------------------------


def test_i055_003_schedule_is_seed_deterministic_and_balanced(db_engine: Engine) -> None:
    first = _running_sampled_game(db_engine, seed=7)
    second = _running_sampled_game(db_engine, seed=7)
    third = _running_sampled_game(db_engine, seed=8)

    exposure: Counter[str] = Counter()
    schedule_first: list[list[str]] = []
    schedule_second: list[list[str]] = []
    schedule_third: list[list[str]] = []
    for _ in range(2 * len(_FIVE)):  # two complete turns, one Argument per Actor
        schedule_first.append(_resolve_active(db_engine, first))
        schedule_second.append(_resolve_active(db_engine, second))
        schedule_third.append(_resolve_active(db_engine, third))
    for chosen in schedule_first:
        exposure.update(chosen)

    assert schedule_first == schedule_second  # same seed, same schedule
    assert schedule_first != schedule_third  # the seeded start rotates across repetitions
    assert all(len(set(chosen)) == 2 for chosen in schedule_first)
    assert set(exposure) == set(_FIVE)
    assert max(exposure.values()) - min(exposure.values()) <= 2
    assert _game(db_engine, first).turn == 3


# --- TEST-I055-004 — non-selected opponents are not eligible ------------------------------------


def test_i055_004_non_selected_opponent_cannot_con_or_pass(db_engine: Engine) -> None:
    creation = _sampled_game(db_engine, challengers=1, seed=1)
    start_to_argument(db_engine, creation)
    argument = _submit(db_engine, creation.game_id)
    (challenger,) = _argument(db_engine, argument.id).challenger_actor_ids or []
    bystander = ({"belvar", "cestia"} - {challenger}).pop()

    with pytest.raises(game_service.ConChallengerNotSelected):
        game_service.make_con(
            db_engine, creation.game_id, argument_id=argument.id, actor_id=bystander,
            reason="Not my turn to object.",
        )
    with pytest.raises(game_service.ConChallengerNotSelected):
        game_service.pass_con(
            db_engine, creation.game_id, argument_id=argument.id, actor_id=bystander
        )
    with pytest.raises(game_service.SelfConForbidden):
        game_service.pass_con(
            db_engine, creation.game_id, argument_id=argument.id, actor_id="arkania"
        )

    bystander_context = context.actor_context(db_engine, creation.game_id, bystander)
    assert "make_con" not in bystander_context["available_actions"]
    assert "pass_con" not in bystander_context["available_actions"]
    assert bystander_context["requires_input"] is False
    assert bystander_context["con_slots_remaining"] is None
    challenger_context = context.actor_context(db_engine, creation.game_id, challenger)
    assert {"make_con", "pass_con"} <= set(challenger_context["available_actions"])
    assert challenger_context["requires_input"] is True

    api = client(db_engine)
    resp = api.post(
        f"/games/{creation.game_id}/arguments/{argument.id}/cons",
        headers=bearer(creation.seat_tokens[bystander]),
        json={"reason": "Objection from the gallery."},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    resp = api.post(
        f"/games/{creation.game_id}/arguments/{argument.id}/pass",
        headers=bearer(creation.seat_tokens[bystander]),
        json={},
    )
    assert resp.status_code == 403


# --- TEST-I055-005 — closure waits only for the sealed set --------------------------------------


def test_i055_005_cons_close_when_sealed_challengers_complete(db_engine: Engine) -> None:
    game_id = _running_sampled_game(db_engine, challengers=2)
    argument = _submit(db_engine, game_id)
    challengers = _argument(db_engine, argument.id).challenger_actor_ids
    assert challengers is not None and len(challengers) == 2

    game_service.make_con(
        db_engine, game_id, argument_id=argument.id, actor_id=challengers[0],
        reason="First objection.",
    )
    game_service.pass_con(db_engine, game_id, argument_id=argument.id, actor_id=challengers[0])
    assert _game(db_engine, game_id).phase is GamePhase.cons
    result = game_service.pass_con(
        db_engine, game_id, argument_id=argument.id, actor_id=challengers[1]
    )

    assert result.cons_closed is True
    assert _game(db_engine, game_id).phase is GamePhase.adjudication


# --- TEST-I055-006 — no adjudication draw consumed; replay returns the sealed selection ---------


def test_i055_006_selection_consumes_no_draw_and_replays_identically(
    db_engine: Engine,
) -> None:
    game_id = _running_sampled_game(db_engine, challengers=2, seed=3)
    before = _game(db_engine, game_id)

    first = _submit(db_engine, game_id, key="arg-1")
    sealed = _argument(db_engine, first.id).challenger_actor_ids
    after = _game(db_engine, game_id)
    replay = _submit_replay(db_engine, game_id, owner=before.active_actor_id, key="arg-1")

    assert replay.id == first.id
    assert after.rng_draw_index == before.rng_draw_index == 0
    assert after.con_challenger_cursor != before.con_challenger_cursor
    assert _argument(db_engine, first.id).challenger_actor_ids == sealed
    assert _game(db_engine, game_id).con_challenger_cursor == after.con_challenger_cursor
    with session_scope(db_engine) as session:
        payloads = [
            dict(event.payload)
            for event in session.exec(
                select(GameEvent).where(
                    GameEvent.game_id == game_id, GameEvent.type == "con_challengers_selected"
                )
            ).all()
        ]
    assert len(payloads) == 1
    assert payloads[0]["challenger_actor_ids"] == sealed
    assert payloads[0]["cursor_before"] == before.con_challenger_cursor
    assert payloads[0]["cursor_after"] == after.con_challenger_cursor


def _submit_replay(engine: Engine, game_id: uuid.UUID, *, owner: str | None, key: str):
    assert owner is not None
    return game_service.make_argument(
        engine, game_id, actor_id=owner, action="Act", desired_effect="Effect",
        pros=["Because"], idempotency_key=key,
    )


# --- TEST-I055-007 — the cursor is durable across restart ---------------------------------------


def test_i055_007_cursor_survives_restart(db_engine: Engine, db_path) -> None:
    game_id = _running_sampled_game(db_engine, challengers=2, seed=7)
    reference = _running_sampled_game(db_engine, challengers=2, seed=7)
    expected = [_resolve_active(db_engine, reference) for _ in range(3)]
    observed = [_resolve_active(db_engine, game_id)]
    cursor = _game(db_engine, game_id).con_challenger_cursor
    db_engine.dispose()

    reopened = database.engine_for_path(db_path)
    try:
        database.initialize_database(reopened)
        assert _game(reopened, game_id).con_challenger_cursor == cursor
        observed.append(_resolve_active(reopened, game_id))
        observed.append(_resolve_active(reopened, game_id))
    finally:
        reopened.dispose()

    assert observed == expected


# --- TEST-I055-008 — invalid configuration fails explicitly -------------------------------------


def test_i055_008_invalid_configuration_fails_explicitly(db_engine: Engine) -> None:
    scenario = scenarios.load_scenario(FIXTURE_DIR)  # three Actors → at most two challengers
    for kwargs in (
        {"con_mode": "sampled_sealed"},
        {"con_mode": "sampled_sealed", "con_challengers_per_argument": 0},
        {"con_mode": "sampled_sealed", "con_challengers_per_argument": 3},
        {"con_mode": "open_cons", "con_challengers_per_argument": 1},
        {"con_mode": "first_come"},
    ):
        with pytest.raises(scenarios.GameCreationError):
            scenarios.create_game_from_scenario(db_engine, scenario, seed=1, **kwargs)
    assert scenarios.create_game_from_scenario(
        db_engine, scenario, seed=1, con_mode="sampled_sealed", con_challengers_per_argument=2
    )

    with pytest.raises(ValueError):
        Settings(AMAW_CON_MODE="sampled_sealed", _env_file=None)
    with pytest.raises(ValueError):
        Settings(AMAW_CON_CHALLENGERS_PER_ARGUMENT="2", _env_file=None)
    settings = Settings(
        AMAW_CON_MODE="sampled_sealed", AMAW_CON_CHALLENGERS_PER_ARGUMENT="2", _env_file=None
    )
    assert (settings.con_mode, settings.con_challengers_per_argument) == ("sampled_sealed", 2)
    assert Settings(_env_file=None).con_mode == "open_cons"

    with pytest.raises(ValueError):
        AutonomousGameRunner(
            client(db_engine), admin_token="x", scenario=FIXTURE_DIR, player_factory=_factory(),
            con_mode="sampled_sealed",
        )
    with pytest.raises(ValueError):
        AutonomousGameRunner(
            client(db_engine), admin_token="x", scenario=FIXTURE_DIR, player_factory=_factory(),
            con_challengers_per_argument=1,
        )


# --- TEST-I055-009/010 — provenance round-trip; runner pays no bystander activation -------------


def test_i055_010_runner_activates_only_sealed_challengers(
    db_engine: Engine, short_game: Path
) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    api = TestClient(app, raise_server_exceptions=False)
    runner = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=short_game, player_factory=_factory(),
        seed=SEED, cons_concurrency=3, con_mode="sampled_sealed",
        con_challengers_per_argument=1,
    )
    result = runner.run()

    export = result.export
    arguments = export["arguments"]
    assert len(arguments) == 3 * SHORT_TURNS
    assert all(len(argument["challenger_actor_ids"]) == 1 for argument in arguments)
    con_runs = [run for run in export["agent_runs"] if run["phase"] == "cons"]
    assert len(con_runs) == len(arguments)  # one activation per sealed challenger, never N-1
    for turn in range(1, SHORT_TURNS + 1):
        sealed = Counter(
            actor for argument in arguments if argument["turn"] == turn
            for actor in argument["challenger_actor_ids"]
        )
        activated = Counter(run["actor_id"] for run in con_runs if run["turn"] == turn)
        assert activated == sealed
    assert all(
        argument["actor_id"] not in argument["challenger_actor_ids"] for argument in arguments
    )

    # TEST-I055-009: the condition is visible everywhere provenance is read.
    assert export["export_format_version"] == EXPORT_FORMAT_VERSION
    assert export["game"]["con_policy_version"] == "con-policy-v1"
    assert export["game"]["con_mode"] == "sampled_sealed"
    assert export["game"]["con_challengers_per_argument"] == 1
    assert isinstance(export["game"]["con_challenger_cursor"], int)
    assert export["run_manifest"]["game_loop"]["con_policy_version"] == "con-policy-v1"
    assert export["run_manifest"]["game_loop"]["con_mode"] == "sampled_sealed"
    assert export["run_manifest"]["game_loop"]["con_challengers_per_argument"] == 1
    assert export["run_manifest"]["resume"]["coordinator"]["con_mode"] == "sampled_sealed"
    assert export["run_manifest"]["resume"]["coordinator"]["con_challengers_per_argument"] == 1
    assert all(
        run["model_parameters"]["con_mode"] == "sampled_sealed"
        and run["model_parameters"]["con_challengers_per_argument"] == 1
        for run in export["agent_runs"] if run["role"] == "player"
    )
    condition = export["run_summary"]["con_condition"]
    assert condition["policy_version"] == "con-policy-v1"
    assert condition["mode"] == "sampled_sealed"
    assert condition["challengers_per_argument"] == 1
    assert condition["opponents_per_argument"] == 2
    assert len(condition["arguments"]) == len(arguments)
    assert all(entry["eligible"] == 1 and entry["completed"] == 1 for entry in condition["arguments"])
    assert condition["totals"]["arguments"] == len(arguments)
    assert condition["totals"]["eligible"] == len(arguments)
    assert condition["totals"]["cons"] + condition["totals"]["passes"] >= len(arguments)
    assert "Con condition sampled_sealed (1 of 2 challengers/Argument)" in result.summary_text
    exposure = Counter(
        actor for argument in arguments for actor in argument["challenger_actor_ids"]
    )
    assert max(exposure.values()) - min(exposure.values()) <= 2


def test_i055_009_open_cons_runner_provenance_and_legacy_export(
    db_engine: Engine, short_game: Path
) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    api = TestClient(app, raise_server_exceptions=False)
    runner = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=short_game, player_factory=_factory(),
        seed=SEED, cons_concurrency=3,
    )
    result = runner.run()
    export = result.export

    assert export["game"]["con_mode"] == "open_cons"
    assert export["game"]["con_challengers_per_argument"] is None
    assert all(argument["challenger_actor_ids"] is None for argument in export["arguments"])
    assert export["run_manifest"]["game_loop"]["con_mode"] == "open_cons"
    condition = export["run_summary"]["con_condition"]
    assert condition["mode"] == "open_cons"
    assert condition["opponents_per_argument"] == 2
    assert all(entry["eligible"] == 2 for entry in condition["arguments"])
    assert "Con condition open_cons" in result.summary_text

    # Legacy rows: a pre-I-055 game carries no condition and the summary must not invent one.
    with session_scope(db_engine) as session:
        game = session.get(Game, uuid.UUID(result.game_id))
        assert game is not None
        game.con_policy_version = None
        game.con_mode = None
        session.add(game)
    legacy = build_game_summary(db_engine, uuid.UUID(result.game_id))["con_condition"]
    assert legacy["policy_version"] is None and legacy["mode"] is None
    assert legacy["opponents_per_argument"] == 2
    assert format_game_summary(build_game_summary(db_engine, uuid.UUID(result.game_id)))


# --- TEST-I055-007 (resume) — resume keeps the sealed schedule, refuses a different condition --


@pytest.mark.slow
def test_i055_007_resume_continues_sealed_schedule_and_refuses_mismatch(
    db_engine: Engine, short_game: Path,
) -> None:
    from agents.autonomous import AutonomousGameError

    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    api = TestClient(app, raise_server_exceptions=False)
    interrupted = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=short_game, player_factory=_factory(),
        seed=SEED, con_mode="sampled_sealed", con_challengers_per_argument=1,
    )
    original_sweep = interrupted._sweep
    sweeps = 0

    def lose_midway(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal sweeps
        sweeps += 1
        if sweeps == 8:
            raise AutonomousGameError("simulated loss")
        return original_sweep(*args, **kwargs)

    interrupted._sweep = lose_midway  # type: ignore[method-assign]
    with pytest.raises(AutonomousGameError, match="simulated loss"):
        interrupted.run()
    game_id = interrupted.last_game_id
    assert game_id is not None
    descriptor = api.get(
        f"/admin/games/{game_id}/resume", headers=bearer(ADMIN_TOKEN)
    ).json()
    recipe = descriptor["manifest"]["resume"]
    assert recipe["coordinator"]["con_mode"] == "sampled_sealed"
    assert recipe["coordinator"]["con_challengers_per_argument"] == 1

    with pytest.raises(AutonomousGameError, match="Con condition"):
        AutonomousGameRunner(
            api, admin_token=ADMIN_TOKEN, scenario="unused", player_factory=_factory(),
            run_configuration={"resume": recipe},
        ).resume(game_id)

    resumed = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario="unused", player_factory=_factory(),
        con_mode="sampled_sealed", con_challengers_per_argument=1,
        run_configuration={"resume": recipe},
    ).resume(game_id)

    reference = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=short_game, player_factory=_factory(),
        seed=SEED, con_mode="sampled_sealed", con_challengers_per_argument=1,
    ).run()
    schedule = lambda export: sorted(  # noqa: E731
        (argument["turn"], argument["actor_id"], argument["challenger_actor_ids"])
        for argument in export["arguments"]
    )
    assert schedule(resumed.export) == schedule(reference.export)
    assert len(resumed.export["arguments"]) == 3 * SHORT_TURNS
