"""I-046: explicit, persisted visible-rebuttal / owner-reply Con-reply condition (D-060)."""

from __future__ import annotations

import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from fastapi.testclient import TestClient

from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.function import AgentInfo

from agents import decisions
from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.scripted import deterministic_umpire
from agents.guardrails import GuardrailConfig
from agents.inference_profile import InferenceProfile
from agents.player import (
    PLAYER_INFERENCE_PHASES,
    PlayerConfig,
    ReferencePlayer,
)
from agents.tools import PlayerTools
from agents.model_client import OpenRouterClient
from agents.umpire import AIUmpire, FakeUmpire
from app.api import create_app
from app.config import Settings
from app import context, database, game_service, scenarios
from app.export import EXPORT_FORMAT_VERSION
from app.models import AdjudicationMode
from app.umpire import AdjudicationProposal
from app.models import Argument, ArgumentReply, Game, GameEvent, GamePhase
from tests.agent_fakes import (
    current_memory,
    decision_call,
    default_decision_policy,
    default_policy,
    model_from_decision_policy,
    model_from_policy,
)
from tests.api_helpers import FIXTURE_DIR, REPO_ROOT, bearer, client, start_to_argument
from tests.benchmark_helpers import SHORT_TURNS
from tests.cli_helpers import capture_run_cli, invoke_run


ADMIN_TOKEN = "admin-secret-token"
SEED = 20260916
_OK = AdjudicationProposal(
    mode=AdjudicationMode.automatic_success, rationale="clear", success_effects=["It works."]
)


def _visible_game(engine: Engine, *, points: int = 3, seed: int | None = None, **extra):
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    return scenarios.create_game_from_scenario(
        engine,
        scenario,
        seed=seed,
        con_reply_mode="visible_reply",
        reply_max_points=points,
        **extra,
    )


def _arkania_argument(engine: Engine, creation) -> game_service.ArgumentResult:
    start_to_argument(engine, creation)
    return game_service.make_argument(
        engine,
        creation.game_id,
        actor_id="arkania",
        action="Begin mobilisation of reserve forces.",
        desired_effect="Reserve formations begin deploying toward the frontier.",
        pros=["Mobilisation plans already exist."],
    )


def _con(engine, creation, argument, actor_id: str, reason: str):
    return game_service.make_con(
        engine, creation.game_id, argument_id=argument.id, actor_id=actor_id, reason=reason
    )


def _pass(engine, creation, argument, actor_id: str):
    return game_service.pass_con(
        engine, creation.game_id, argument_id=argument.id, actor_id=actor_id
    )


def _event_types(engine: Engine, game_id) -> list[str]:
    with Session(engine) as session:
        return [
            event.type
            for event in session.exec(
                select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
            ).all()
        ]


def test_i046_002_cons_closing_with_a_con_enters_reply_and_reveals(db_engine: Engine) -> None:
    creation = _visible_game(db_engine)
    argument = _arkania_argument(db_engine, creation)

    _con(db_engine, creation, argument, "belvar", "Rail is disrupted.")
    _pass(db_engine, creation, argument, "belvar")
    _pass(db_engine, creation, argument, "cestia")

    with Session(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None and game.phase is GamePhase.reply
        row = session.get(Argument, argument.id)
        assert row is not None and row.status == "awaiting_reply"

    types = _event_types(db_engine, creation.game_id)
    revealed = types.index("cons_revealed")
    assert types[revealed - 1] == "con_passed"
    assert types[revealed + 1] == "phase_started"
    assert "argument_adjudicated" not in types


def test_i046_003_zero_cons_skip_reply_entirely(db_engine: Engine) -> None:
    creation = _visible_game(db_engine)
    argument = _arkania_argument(db_engine, creation)

    _pass(db_engine, creation, argument, "belvar")
    _pass(db_engine, creation, argument, "cestia")

    with Session(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None and game.phase is GamePhase.adjudication
        row = session.get(Argument, argument.id)
        assert row is not None and row.status == "adjudicating"
    types = _event_types(db_engine, creation.game_id)
    assert "cons_revealed" not in types


def test_i046_001_sealed_cons_default_never_enters_reply(db_engine: Engine) -> None:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(db_engine, scenario, seed=1)
    argument = _arkania_argument(db_engine, creation)

    _con(db_engine, creation, argument, "belvar", "Rail is disrupted.")
    _pass(db_engine, creation, argument, "belvar")
    _pass(db_engine, creation, argument, "cestia")

    with Session(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None
        assert game.con_reply_policy_version == "con-reply-policy-v1"
        assert game.con_reply_mode == "sealed_cons"
        assert game.reply_max_points is None
        assert game.phase is GamePhase.adjudication
        assert session.exec(select(ArgumentReply)).all() == []
    assert "cons_revealed" not in _event_types(db_engine, creation.game_id)
    sealed_view = _ctx(db_engine, creation, "belvar")
    assert "reply_max_points" not in sealed_view and "revealed_exchanges" not in sealed_view
    assert "cons" not in sealed_view["current_argument"] if sealed_view["current_argument"] else True


def _revealed(db_engine: Engine, *, points: int = 3):
    """A visible_reply game in REPLY: arkania argued, belvar and cestia each made one Con."""
    creation = _visible_game(db_engine, points=points)
    argument = _arkania_argument(db_engine, creation)
    con_b = _con(db_engine, creation, argument, "belvar", "Rail is disrupted.")
    con_c = _con(db_engine, creation, argument, "cestia", "Reserves are undertrained.")
    _pass(db_engine, creation, argument, "belvar")
    _pass(db_engine, creation, argument, "cestia")
    return creation, argument, con_b, con_c


def test_i046_007_reply_persists_once_emits_and_closes_to_adjudication(db_engine: Engine) -> None:
    creation, argument, con_b, con_c = _revealed(db_engine)

    result = game_service.submit_reply(
        db_engine,
        creation.game_id,
        argument_id=argument.id,
        actor_id="arkania",
        points=[{"con_id": str(con_b.id), "text": "Freight lines, not passenger lines."}],
        idempotency_key="reply-1",
    )
    assert result.argument_id == argument.id
    assert result.actor_id == "arkania"
    assert result.points == [{"con_id": str(con_b.id), "text": "Freight lines, not passenger lines."}]

    with Session(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None and game.phase is GamePhase.adjudication
        row = session.get(Argument, argument.id)
        assert row is not None and row.status == "adjudicating"
        replies = session.exec(select(ArgumentReply)).all()
        assert len(replies) == 1 and replies[0].argument_id == argument.id
    types = _event_types(db_engine, creation.game_id)
    submitted = types.index("owner_reply_submitted")
    assert types[submitted + 1] == "phase_started"

    # Same key + same payload → replay of the record, no second row, no second event.
    replay = game_service.submit_reply(
        db_engine,
        creation.game_id,
        argument_id=argument.id,
        actor_id="arkania",
        points=[{"con_id": str(con_b.id), "text": "Freight lines, not passenger lines."}],
        idempotency_key="reply-1",
    )
    assert replay.points == result.points
    assert _event_types(db_engine, creation.game_id).count("owner_reply_submitted") == 1

    # Any fresh attempt once the reply exists is rejected as already participated.
    with pytest.raises(game_service.ReplyAlreadySubmitted):
        game_service.submit_reply(
            db_engine,
            creation.game_id,
            argument_id=argument.id,
            actor_id="arkania",
            points=[],
        )


def test_i046_007b_empty_reply_is_the_recorded_pass(db_engine: Engine) -> None:
    creation, argument, _, _ = _revealed(db_engine)
    result = game_service.submit_reply(
        db_engine, creation.game_id, argument_id=argument.id, actor_id="arkania", points=[]
    )
    assert result.points == []
    with Session(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None and game.phase is GamePhase.adjudication
        assert len(session.exec(select(ArgumentReply)).all()) == 1


def _ctx(engine: Engine, creation, actor_id: str) -> dict:
    return context.actor_context(engine, creation.game_id, actor_id)


def test_i046_004_cons_sealed_during_cons_then_revealed_identically_in_canonical_order(
    db_engine: Engine,
) -> None:
    creation = _visible_game(db_engine)
    argument = _arkania_argument(db_engine, creation)
    # Deliberately reversed completion timing: cestia's Con commits before belvar's.
    con_c = _con(db_engine, creation, argument, "cestia", "Reserves are undertrained.")
    con_b = _con(db_engine, creation, argument, "belvar", "Rail is disrupted.")

    # INV-CON-007: while phase == cons nobody reads anybody's Con — not even their own.
    for actor_id in ("arkania", "belvar", "cestia"):
        current = _ctx(db_engine, creation, actor_id)["current_argument"]
        assert current is not None
        assert "cons" not in current and "owner_reply" not in current
        assert "revealed_exchanges" in _ctx(db_engine, creation, actor_id)

    _pass(db_engine, creation, argument, "cestia")
    _pass(db_engine, creation, argument, "belvar")

    views = {actor_id: _ctx(db_engine, creation, actor_id) for actor_id in ("arkania", "belvar", "cestia")}
    expected = [
        {"con_id": str(con_b.id), "actor_id": "belvar", "reason": "Rail is disrupted."},
        {"con_id": str(con_c.id), "actor_id": "cestia", "reason": "Reserves are undertrained."},
    ]
    for actor_id, view in views.items():
        assert view["phase"] == "reply"
        current = view["current_argument"]
        assert current["status"] == "awaiting_reply"
        assert current["cons"] == expected, actor_id  # canonical (actor_id, con_id), not commit order
        assert current["owner_reply"] is None


def test_i046_005_only_the_owner_may_reply(db_engine: Engine) -> None:
    creation, argument, con_b, _ = _revealed(db_engine)

    owner = _ctx(db_engine, creation, "arkania")
    assert "reply_to_cons" in owner["available_actions"]
    assert owner["reply_max_points"] == 3
    assert owner["requires_input"] is True
    assert "make_con" not in owner["available_actions"]
    assert "send_message" not in owner["available_actions"]

    for other in ("belvar", "cestia"):
        view = _ctx(db_engine, creation, other)
        assert view["requires_input"] is False
        assert not {"reply_to_cons", "make_con", "pass_con", "send_message"} & set(
            view["available_actions"]
        )
        with pytest.raises(game_service.NotArgumentOwner):
            game_service.submit_reply(
                db_engine, creation.game_id, argument_id=argument.id, actor_id=other, points=[]
            )
    # Cons are frozen: no Con or pass is accepted in reply.
    with pytest.raises(game_service.InvalidPhase):
        _con(db_engine, creation, argument, "belvar", "late")
    with pytest.raises(game_service.InvalidPhase):
        _pass(db_engine, creation, argument, "belvar")


@pytest.mark.parametrize(
    "points_factory, message",
    [
        (lambda b, c: [{"con_id": b, "text": "x"}] * 2, "more than once"),
        (lambda b, c: [{"con_id": str(uuid.uuid4()), "text": "x"}], "unknown Con"),
        (lambda b, c: [{"con_id": b, "text": "   "}], "non-empty"),
        (lambda b, c: [{"con_id": b, "text": "x" * 4001}], "exceeds"),
        (lambda b, c: [{"con_id": b, "text": "x", "action": "new"}], "exactly"),
        (lambda b, c: [{"con_id": b, "text": "a"}, {"con_id": c, "text": "b"}, {"con_id": b, "text": "c"}], "at most 2"),
    ],
)
def test_i046_006_invalid_reply_points_rejected_with_nothing_persisted(
    db_engine: Engine, points_factory, message
) -> None:
    creation, argument, con_b, con_c = _revealed(db_engine, points=2)
    with pytest.raises(game_service.InvalidReply, match=message):
        game_service.submit_reply(
            db_engine,
            creation.game_id,
            argument_id=argument.id,
            actor_id="arkania",
            points=points_factory(str(con_b.id), str(con_c.id)),
        )
    with Session(db_engine) as session:
        assert session.exec(select(ArgumentReply)).all() == []
        game = session.get(Game, creation.game_id)
        assert game is not None and game.phase is GamePhase.reply


def test_i046_009_umpire_context_carries_reply_only_under_visible_reply(db_engine: Engine) -> None:
    creation, argument, con_b, con_c = _revealed(db_engine)
    game_service.submit_reply(
        db_engine,
        creation.game_id,
        argument_id=argument.id,
        actor_id="arkania",
        points=[{"con_id": str(con_c.id), "text": "Training was completed last season."}],
    )
    seen: list = []
    umpire = FakeUmpire(responder=lambda ctx: seen.append(ctx) or _OK)
    game_service.resolve_adjudication(db_engine, creation.game_id, umpire)
    (ctx,) = seen
    assert ctx.cons == [
        {"con_id": str(con_b.id), "actor_id": "belvar", "reason": "Rail is disrupted."},
        {"con_id": str(con_c.id), "actor_id": "cestia", "reason": "Reserves are undertrained."},
    ]
    assert ctx.owner_reply == [
        {"con_id": str(con_c.id), "con_actor_id": "cestia", "text": "Training was completed last season."}
    ]
    # Hidden information is exactly what sealed_cons would have produced.
    assert ctx.relevant_hidden_information == _sealed_hidden(db_engine)


def _sealed_hidden(db_engine: Engine) -> list:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(db_engine, scenario, seed=3)
    argument = _arkania_argument(db_engine, creation)
    _con(db_engine, creation, argument, "belvar", "Rail is disrupted.")
    _pass(db_engine, creation, argument, "belvar")
    _pass(db_engine, creation, argument, "cestia")
    seen: list = []
    umpire = FakeUmpire(responder=lambda ctx: seen.append(ctx) or _OK)
    game_service.resolve_adjudication(db_engine, creation.game_id, umpire)
    (ctx,) = seen
    # sealed_cons: the released shape, byte-identical — no con_id, no owner_reply.
    assert ctx.cons == [{"actor_id": "belvar", "reason": "Rail is disrupted."}]
    assert ctx.owner_reply is None
    return ctx.relevant_hidden_information


def test_i046_009b_skipped_reply_phase_leaves_owner_reply_absent(db_engine: Engine) -> None:
    creation = _visible_game(db_engine)
    argument = _arkania_argument(db_engine, creation)
    _pass(db_engine, creation, argument, "belvar")
    _pass(db_engine, creation, argument, "cestia")
    seen: list = []
    game_service.resolve_adjudication(
        db_engine, creation.game_id, FakeUmpire(responder=lambda ctx: seen.append(ctx) or _OK)
    )
    assert seen[0].cons == [] and seen[0].owner_reply is None


def test_i046_010_revealed_exchanges_cover_this_turn_and_reset_next_turn(db_engine: Engine) -> None:
    creation, argument, con_b, _ = _revealed(db_engine)
    game_service.submit_reply(
        db_engine,
        creation.game_id,
        argument_id=argument.id,
        actor_id="arkania",
        points=[{"con_id": str(con_b.id), "text": "Freight only."}],
    )
    game_service.resolve_adjudication(db_engine, creation.game_id, FakeUmpire(proposal=_OK))
    game_service.advance_after_resolution(db_engine, creation.game_id)

    # belvar is now the active Actor in ARGUMENT; everyone sees arkania's exchange.
    for actor_id in ("arkania", "belvar", "cestia"):
        view = _ctx(db_engine, creation, actor_id)
        assert view["phase"] == "argument"
        (exchange,) = view["revealed_exchanges"]
        assert exchange["argument_id"] == str(argument.id)
        assert exchange["actor_id"] == "arkania"
        assert exchange["cons"][0]["con_id"] == str(con_b.id)
        assert exchange["owner_reply"] == {"points": [{"con_id": str(con_b.id), "text": "Freight only."}]}
        assert exchange["resolved"] is True

    # Drive the remaining two Actors through uncontested Arguments to the next turn.
    for actor_id in ("belvar", "cestia"):
        arg = game_service.make_argument(
            db_engine, creation.game_id, actor_id=actor_id, action="Hold.",
            desired_effect="Nothing changes.", pros=["Stability."],
        )
        for other in ("arkania", "belvar", "cestia"):
            if other != actor_id:
                _pass(db_engine, creation, arg, other)
        game_service.resolve_adjudication(db_engine, creation.game_id, FakeUmpire(proposal=_OK))
        game_service.advance_after_resolution(db_engine, creation.game_id)

    view = _ctx(db_engine, creation, "arkania")
    assert view["turn"] == 2 and view["phase"] == "diplomacy"
    assert view["revealed_exchanges"] == []


# --- API surface (api.md §9.3, §6.1, §14) ---------------------------------------------------


def test_i046_005_api_reply_is_owner_only_and_closes_the_phase(db_engine: Engine) -> None:
    creation, argument, con_b, _ = _revealed(db_engine)
    api = client(db_engine)
    url = f"/games/{creation.game_id}/arguments/{argument.id}/reply"
    body = {"points": [{"con_id": str(con_b.id), "text": "Freight only."}]}

    denied = api.post(url, json=body, headers=bearer(creation.seat_tokens["belvar"]))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"

    extra = api.post(
        url,
        json={"points": [{"con_id": str(con_b.id), "text": "x", "action": "new"}]},
        headers=bearer(creation.seat_tokens["arkania"]),
    )
    assert extra.status_code == 422

    ok = api.post(
        url, json=body, headers={**bearer(creation.seat_tokens["arkania"]), "Idempotency-Key": "r1"}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json() == {
        "reply": {
            "argument_id": str(argument.id),
            "actor_id": "arkania",
            "points": [{"con_id": str(con_b.id), "text": "Freight only."}],
        }
    }
    replay = api.post(
        url, json=body, headers={**bearer(creation.seat_tokens["arkania"]), "Idempotency-Key": "r1"}
    )
    assert replay.status_code == 200 and replay.json() == ok.json()

    again = api.post(url, json={"points": []}, headers=bearer(creation.seat_tokens["arkania"]))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_participated"

    ctx = api.get(f"/games/{creation.game_id}/context", headers=bearer(creation.seat_tokens["cestia"]))
    assert ctx.json()["phase"] == "adjudication"


def _admin_client(db_engine: Engine, umpire=None) -> TestClient:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=umpire or FakeUmpire(proposal=_OK))
    return TestClient(app, raise_server_exceptions=False)


def test_i046_011_admin_creation_accepts_and_validates_the_condition(db_engine: Engine) -> None:
    api = _admin_client(db_engine)
    admin = bearer(ADMIN_TOKEN)
    base = {"scenario": str(FIXTURE_DIR), "seed": 5}

    created = api.post(
        "/admin/games", json={**base, "con_reply_mode": "visible_reply", "reply_max_points": 2},
        headers=admin,
    )
    assert created.status_code in (200, 201), created.text
    with Session(db_engine) as session:
        game = session.get(Game, uuid.UUID(created.json()["game_id"]))
        assert game is not None
        assert (game.con_reply_mode, game.reply_max_points) == ("visible_reply", 2)

    for bad in (
        {"con_reply_mode": "visible_reply"},
        {"con_reply_mode": "visible_reply", "reply_max_points": 0},
        {"con_reply_mode": "visible_reply", "reply_max_points": 4},
        {"con_reply_mode": "sealed_cons", "reply_max_points": 2},
        {"con_reply_mode": "anonymous"},
    ):
        response = api.post("/admin/games", json={**base, **bad}, headers=admin)
        assert response.status_code in (400, 422), bad


# --- Player layer (agents.md §5, §13.1, §19, §31) ------------------------------------------


def _player(engine: Engine, creation, actor: str, model, **config) -> ReferencePlayer:
    tools = PlayerTools(client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor])
    return ReferencePlayer(
        tools,
        actor_id=actor,
        actor_name=tools.observe().data["you"]["name"],
        model=model,
        config=PlayerConfig(model="fake/deterministic", **config),
    )


def test_i046_011_player_config_selects_reply_prompts_and_schemas_by_mode(db_engine: Engine) -> None:
    sealed_loop = PlayerConfig(model="m", activation_mode="tool_loop")
    sealed_shot = PlayerConfig(model="m", activation_mode="single_shot")
    assert (sealed_loop.con_reply_mode, sealed_shot.con_reply_mode) == ("sealed_cons", "sealed_cons")
    assert (sealed_loop.prompt_version, sealed_shot.prompt_version) == (
        "player-v1.10", "player-v1.11"
    )

    visible_loop = PlayerConfig(model="m", activation_mode="tool_loop", con_reply_mode="visible_reply")
    visible_shot = PlayerConfig(model="m", activation_mode="single_shot", con_reply_mode="visible_reply")
    assert (visible_loop.prompt_version, visible_shot.prompt_version) == (
        "player-v1.12", "player-v1.13"
    )
    with pytest.raises(ValueError, match="con_reply_mode"):
        PlayerConfig(model="m", con_reply_mode="anonymous")

    creation = _visible_game(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    visible = _player(
        db_engine, creation, "arkania", model_from_policy(default_policy),
        activation_mode="tool_loop", con_reply_mode="visible_reply",
    )
    sealed = _player(
        db_engine, creation, "belvar", model_from_policy(default_policy), activation_mode="tool_loop",
    )
    assert "reply_to_cons" in visible.tool_names
    assert "reply_to_cons" not in sealed.tool_names
    assert visible.tool_schema_version == "player-tools-v13-reply-live-bounded-research"
    assert sealed.tool_schema_version == "player-tools-v11-live-bounded-research"
    batched = PlayerConfig(
        model="m", activation_mode="single_shot", con_reply_mode="visible_reply",
        research_mode="batched-v1",
    )
    shot = _player(db_engine, creation, "cestia", model_from_decision_policy(default_decision_policy),
                   activation_mode="single_shot", con_reply_mode="visible_reply", research_mode="batched-v1")
    assert shot.tool_schema_version == (
        "player-tools-v18-batched-reply-live-bounded-operations-item-outcomes"
    )
    assert "reply_to_cons" not in shot.tool_names  # single shot: mutations are decision fields
    assert batched.prompt_version == "player-v1.13"


def test_i046_014_visible_reply_prompts_disclose_visibility_and_sealed_prompts_do_not(
) -> None:
    prompts = REPO_ROOT / "docs/prompts"
    for version in ("player-v1.12", "player-v1.13"):
        text = prompts.joinpath(f"{version}.md").read_text(encoding="utf-8")
        assert "Cons in this game are not sealed" in text
        assert "which\nActor made it" in text or "which Actor made it" in text
        assert "revealed to every Actor" in text
        assert "reply_to_cons" in text
    for version in ("player-v1.10", "player-v1.11"):
        text = prompts.joinpath(f"{version}.md").read_text(encoding="utf-8")
        assert "reply_to_cons" not in text and "not sealed" not in text
    umpire = prompts.joinpath("umpire-v1.4.md").read_text(encoding="utf-8")
    assert "owner_reply" in umpire
    assert "owner_reply" not in prompts.joinpath("umpire-v1.3.md").read_text(encoding="utf-8")


def test_i046_011b_inference_profile_reply_falls_back_to_argument_and_is_recorded() -> None:
    profile = InferenceProfile.from_document({
        "schema": "inference-profile-v1",
        "name": "argument-heavy",
        "phases": {"argument": {"reasoning_effort": "high", "max_tokens": 4000},
                   "cons": {"reasoning_effort": "low"}},
    })
    resolved = profile.resolve_phase("reply", reasoning_effort="medium", max_tokens=1000)
    assert resolved == {"reasoning_effort": "high", "max_tokens": 4000}
    explicit = InferenceProfile.from_document({
        "schema": "inference-profile-v1",
        "name": "own-reply",
        "phases": {"argument": {"reasoning_effort": "high"}, "reply": {"reasoning_effort": "low"}},
    })
    assert explicit.resolve_phase("reply", reasoning_effort="medium", max_tokens=None) == {
        "reasoning_effort": "low", "max_tokens": None,
    }
    assert "reply" in PLAYER_INFERENCE_PHASES


def test_i046_012a_single_shot_reply_decision_is_validated_before_any_mutation(db_engine: Engine) -> None:
    creation, argument, con_b, con_c = _revealed(db_engine, points=1)
    tools = PlayerTools(client(db_engine), game_id=str(creation.game_id), token=creation.seat_tokens["arkania"])
    packet_actions = tools.observe().data["available_actions"]
    assert decisions.select_decision_type(packet_actions) is decisions.ReplyDecision

    memory = {
        key: "" if key in ("strategic_assessment", "intended_next_action") else []
        for key in decisions.StrategicMemory.model_fields
    }
    too_many = decisions.ReplyDecision(
        points=[{"con_id": str(con_b.id), "text": "a"}, {"con_id": str(con_c.id), "text": "b"}],
        strategic_memory=memory,
    )
    violation = decisions.validate_decision(
        too_many, guardrails=GuardrailConfig(), message_budget=None, con_allowed=False,
        con_slots_remaining=None, reply_max_points=1, reply_con_ids=[str(con_b.id), str(con_c.id)],
    )
    assert violation is not None and violation["error"]["code"] == "guardrail_reply_points_exceeded"
    unknown = decisions.ReplyDecision(points=[{"con_id": str(uuid.uuid4()), "text": "a"}], strategic_memory=memory)
    violation = decisions.validate_decision(
        unknown, guardrails=GuardrailConfig(), message_budget=None, con_allowed=False,
        con_slots_remaining=None, reply_max_points=1, reply_con_ids=[str(con_b.id), str(con_c.id)],
    )
    assert violation is not None and violation["error"]["code"] == "guardrail_reply_unknown_con"
    duplicate = decisions.ReplyDecision(
        points=[{"con_id": str(con_b.id), "text": "a"}, {"con_id": str(con_b.id), "text": "b"}],
        strategic_memory=memory,
    )
    violation = decisions.validate_decision(
        duplicate, guardrails=GuardrailConfig(), message_budget=None, con_allowed=False,
        con_slots_remaining=None, reply_max_points=3, reply_con_ids=[str(con_b.id), str(con_c.id)],
    )
    assert violation is not None and violation["error"]["code"] == "guardrail_reply_duplicate_con"
    with Session(db_engine) as session:
        assert session.exec(select(ArgumentReply)).all() == []

    good = decisions.ReplyDecision(points=[{"con_id": str(con_b.id), "text": "Freight only."}], strategic_memory=memory)
    assert decisions.validate_decision(
        good, guardrails=GuardrailConfig(), message_budget=None, con_allowed=False,
        con_slots_remaining=None, reply_max_points=1, reply_con_ids=[str(con_b.id), str(con_c.id)],
    ) is None
    report = decisions.execute_decision(
        good, tools, argument_id=str(argument.id), key_fn=lambda op, payload: f"{op}-k"
    )
    assert report.actions == ["reply_to_cons"] and report.rejected == []
    with Session(db_engine) as session:
        (reply,) = session.exec(select(ArgumentReply)).all()
        assert reply.points == [{"con_id": str(con_b.id), "text": "Freight only."}]


# --- Autonomous runner and provenance (TEST-I046-011/012/013) --------------------------------


def _reply_policy(packet: dict, tool_names: list[str], idx: int) -> list[Any]:
    """Tool-loop twin of ``default_policy`` that answers the first revealed Con once."""
    actions = packet.get("available_actions", [])
    current = packet.get("current_argument") or {}
    if "reply_to_cons" in actions and idx == 0:
        cons = current.get("cons") or []
        points = [{"con_id": cons[0]["con_id"], "text": "That risk is already priced in."}] if cons else []
        return [ToolCallPart(
            tool_name="reply_to_cons", args={"argument_id": current.get("id"), "points": points},
        )]
    return default_policy(packet, tool_names, idx)


def _reply_decision_policy(packet: dict, idx: int, info: AgentInfo) -> list[Any]:
    """Single-shot twin: one ReplyDecision answering the first revealed Con."""
    actions = packet.get("available_actions", [])
    current = packet.get("current_argument") or {}
    if "reply_to_cons" in actions:
        cons = current.get("cons") or []
        points = [{"con_id": cons[0]["con_id"], "text": "That risk is already priced in."}] if cons else []
        return [decision_call(info, {"points": points, "strategic_memory": current_memory(packet)})]
    return default_decision_policy(packet, idx, info)


def _factory(activation_mode: str, con_reply_mode: str = "visible_reply"):
    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        model = (
            model_from_policy(_reply_policy)
            if activation_mode == "tool_loop"
            else model_from_decision_policy(_reply_decision_policy)
        )
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model,
            config=PlayerConfig(
                model="fake/deterministic", activation_mode=activation_mode,
                con_reply_mode=con_reply_mode,
            ),
        )
        return LifecycleRunner(player)

    return player_factory


def _run(db_engine: Engine, scenario: Path, activation_mode: str, **runner_kwargs):
    api = _admin_client(db_engine, umpire=deterministic_umpire())
    runner = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=scenario,
        player_factory=_factory(activation_mode), seed=SEED, cons_concurrency=3,
        con_reply_mode="visible_reply", reply_max_points=2, **runner_kwargs,
    )
    return runner.run()


@pytest.mark.parametrize("activation_mode", ["single_shot", "tool_loop"])
@pytest.mark.slow
def test_i046_012_runner_spends_exactly_one_owner_activation_per_contested_argument(
    db_engine: Engine, activation_mode: str, short_game: Path
) -> None:
    result = _run(db_engine, short_game, activation_mode)
    export = result.export
    arguments = export["arguments"]
    assert len(arguments) == 3 * SHORT_TURNS
    replies = export["argument_replies"]
    # The fake policies always Con, so every Argument is contested and every owner replied once.
    assert len(replies) == len(arguments)
    by_argument = {reply["argument_id"]: reply for reply in replies}
    for argument in arguments:
        reply = by_argument[argument["id"]]
        assert reply["actor_id"] == argument["actor_id"]
        assert len(reply["points"]) == 1
    reply_runs = [run for run in export["agent_runs"] if run["phase"] == "reply"]
    assert len(reply_runs) == len(arguments)
    assert Counter(run["actor_id"] for run in reply_runs) == Counter(
        argument["actor_id"] for argument in arguments
    )
    assert all(run["model_parameters"]["activation_mode"] == activation_mode for run in reply_runs)
    assert all(run["model_parameters"]["con_reply_mode"] == "visible_reply" for run in reply_runs)
    assert all(run["model_parameters"]["reply_max_points"] == 2 for run in reply_runs)
    events = [event["type"] for event in export["events"]]
    assert events.count("cons_revealed") == len(arguments)
    assert events.count("owner_reply_submitted") == len(arguments)

    # TEST-I046-011: provenance round-trip.
    assert export["export_format_version"] == EXPORT_FORMAT_VERSION
    assert export["game"]["con_reply_policy_version"] == "con-reply-policy-v1"
    assert export["game"]["con_reply_mode"] == "visible_reply"
    assert export["game"]["reply_max_points"] == 2
    game_loop = export["run_manifest"]["game_loop"]
    assert game_loop["con_reply_policy_version"] == "con-reply-policy-v1"
    assert game_loop["con_reply_mode"] == "visible_reply"
    assert game_loop["reply_max_points"] == 2
    assert export["run_manifest"]["resume"]["coordinator"]["con_reply_mode"] == "visible_reply"
    assert export["run_manifest"]["resume"]["coordinator"]["reply_max_points"] == 2
    player = export["run_manifest"]["player"]
    expected_prompt = "player-v1.13" if activation_mode == "single_shot" else "player-v1.12"
    assert player["prompt_version"] == expected_prompt
    assert player["tool_schema_version"] == "player-tools-v13-reply-live-bounded-research"
    assert player["inference_profile"]["phases"]["reply"] == player["inference_profile"]["phases"]["argument"]
    condition = export["run_summary"]["con_reply_condition"]
    assert condition["con_reply_mode"] == "visible_reply"
    assert condition["reply_max_points"] == 2
    assert condition["arguments_contested"] == len(arguments)
    assert condition["arguments_skipped_no_cons"] == 0
    assert condition["replies"] == len(arguments)
    assert condition["empty_replies"] == 0
    assert condition["reply_points"] == len(arguments)


def test_i046_001b_sealed_runner_provenance_is_null_free_but_records_the_default(
    db_engine: Engine, short_game: Path,
) -> None:
    api = _admin_client(db_engine, umpire=deterministic_umpire())
    runner = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=short_game,
        player_factory=_factory("single_shot", con_reply_mode="sealed_cons"), seed=SEED,
    )
    export = runner.run().export
    assert export["argument_replies"] == []
    assert export["game"]["con_reply_mode"] == "sealed_cons"
    assert export["game"]["reply_max_points"] is None
    assert export["run_manifest"]["game_loop"]["con_reply_mode"] == "sealed_cons"
    assert export["run_manifest"]["player"]["prompt_version"] == "player-v1.11"
    assert export["run_manifest"]["player"]["tool_schema_version"] == (
        "player-tools-v11-live-bounded-research"
    )
    assert not any(run["phase"] == "reply" for run in export["agent_runs"])
    assert export["run_summary"]["con_reply_condition"]["con_reply_mode"] == "sealed_cons"


def test_i046_011c_runner_refuses_players_whose_condition_differs_from_the_game(
    db_engine: Engine,
) -> None:
    api = _admin_client(db_engine, umpire=deterministic_umpire())
    runner = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=FIXTURE_DIR,
        player_factory=_factory("single_shot", con_reply_mode="sealed_cons"), seed=SEED,
        con_reply_mode="visible_reply", reply_max_points=3,
    )
    with pytest.raises(AutonomousGameError, match="con_reply_mode"):
        runner.run()
    for bad in (
        {"con_reply_mode": "visible_reply"},
        {"con_reply_mode": "sealed_cons", "reply_max_points": 1},
        {"con_reply_mode": "visible_reply", "reply_max_points": 4},
    ):
        with pytest.raises(ValueError):
            AutonomousGameRunner(
                api, admin_token=ADMIN_TOKEN, scenario=FIXTURE_DIR,
                player_factory=_factory("single_shot"), seed=SEED, **bad,
            )


@pytest.mark.slow
def test_i046_013_composes_with_sampled_sealed_challengers(
    db_engine: Engine, short_game: Path
) -> None:
    result = _run(
        db_engine, short_game, "single_shot",
        con_mode="sampled_sealed", con_challengers_per_argument=1,
    )
    export = result.export
    cons_by_argument: dict[str, list[dict]] = {}
    for con in export["cons"]:
        cons_by_argument.setdefault(con["argument_id"], []).append(con)
    for argument in export["arguments"]:
        sealed = set(argument["challenger_actor_ids"])
        assert len(sealed) == 1
        cons = cons_by_argument.get(argument["id"], [])
        assert cons and {con["actor_id"] for con in cons} == sealed
        (reveal,) = [
            event for event in export["events"]
            if event["type"] == "cons_revealed" and event["payload"]["argument_id"] == argument["id"]
        ]
        assert sorted(reveal["payload"]["con_ids"]) == sorted(con["id"] for con in cons)
    con_runs = [run for run in export["agent_runs"] if run["phase"] == "cons"]
    assert len(con_runs) == len(export["arguments"])  # only the sealed challenger was activated


# --- Restart and privacy (TEST-I046-008, TEST-I046-015) --------------------------------------


def test_i046_008_restart_in_reply_reactivates_the_same_owner_once(db_engine: Engine, db_path) -> None:
    creation, argument, con_b, _ = _revealed(db_engine)
    db_engine.dispose()

    reopened = database.engine_for_path(db_path)
    try:
        owner = context.actor_context(reopened, creation.game_id, "arkania")
        assert owner["phase"] == "reply" and "reply_to_cons" in owner["available_actions"]
        assert owner["current_argument"]["cons"][0]["con_id"] == str(con_b.id)
        for other in ("belvar", "cestia"):
            assert context.actor_context(reopened, creation.game_id, other)["requires_input"] is False
        game_service.submit_reply(
            reopened, creation.game_id, argument_id=argument.id, actor_id="arkania", points=[]
        )
        with pytest.raises(game_service.ReplyAlreadySubmitted):
            game_service.submit_reply(
                reopened, creation.game_id, argument_id=argument.id, actor_id="arkania", points=[]
            )
        with Session(reopened) as session:
            assert len(session.exec(select(ArgumentReply)).all()) == 1
            game = session.get(Game, creation.game_id)
            assert game is not None and game.phase is GamePhase.adjudication
    finally:
        reopened.dispose()


def test_i046_015_reveal_projects_no_hidden_state_beyond_the_con_text(db_engine: Engine) -> None:
    """A voluntary disclosure changes the visibility of no Resource, fact or Effect (INV-REPLY-003)."""
    creation = _visible_game(db_engine)
    start_to_argument(db_engine, creation)
    # belvar cannot argue first in the fixture order, so arkania argues and the *challengers*
    # are the ones whose hidden state must stay hidden — cestia's private fact, and belvar's.
    argument = game_service.make_argument(
        db_engine, creation.game_id, actor_id="arkania", action="Mobilise.",
        desired_effect="Reserves deploy.", pros=["Plans exist."],
    )
    before = {actor: _ctx(db_engine, creation, actor) for actor in ("arkania", "belvar", "cestia")}
    # cestia discloses its own private fact verbatim; belvar discloses nothing.
    cestia_fact = before["cestia"]["you"]["private_information"][0]
    _con(db_engine, creation, argument, "cestia", f"We know: {cestia_fact}")
    _con(db_engine, creation, argument, "belvar", "Rail is disrupted.")
    _pass(db_engine, creation, argument, "cestia")
    _pass(db_engine, creation, argument, "belvar")

    after = {actor: _ctx(db_engine, creation, actor) for actor in ("arkania", "belvar", "cestia")}
    for actor in ("arkania", "belvar", "cestia"):
        # Con text is visible verbatim — including the voluntary disclosure …
        reasons = [con["reason"] for con in after[actor]["current_argument"]["cons"]]
        assert any(cestia_fact in reason for reason in reasons)
        # … but nothing about the viewer's own brief, facts or Effects changed.
        assert after[actor]["you"] == before[actor]["you"]
        assert after[actor]["effects"] == before[actor]["effects"]
    # arkania's private Effect and private Resource stay arkania's alone.
    private_effects = [e for e in after["arkania"]["effects"] if e["visibility"] == "private"]
    assert private_effects
    for other in ("belvar", "cestia"):
        assert not [e for e in after[other]["effects"] if e["visibility"] == "private"]
        api = client(db_engine)
        search = api.get(
            f"/games/{creation.game_id}/resources/search", params={"q": "intelligence"},
            headers=bearer(creation.seat_tokens[other]),
        )
        assert search.status_code == 200
        assert all(hit["id"] != "arkania-intel" for hit in search.json()["results"])
        read = api.get(
            f"/games/{creation.game_id}/resources/arkania-intel",
            headers=bearer(creation.seat_tokens[other]),
        )
        assert read.status_code == 404


# --- CLI / settings (TEST-I046-011) ----------------------------------------------------------


def _cli_capture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    package = tmp_path / "example-scenario"
    package.mkdir()
    (package / "scenario.yaml").write_text("scenario: {}\n", encoding="utf-8")
    return package, capture_run_cli(tmp_path, monkeypatch, fake_player=True)


def test_i046_011d_cli_resolves_condition_and_umpire_prompt(tmp_path: Path, monkeypatch) -> None:
    package, captured = _cli_capture(tmp_path, monkeypatch)

    # Default: sealed Cons and the released player prompt; the Umpire default is v1.7 (D-114).
    result = invoke_run(package)
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["con_reply_mode"] == "sealed_cons"
    assert captured["runner_options"]["reply_max_points"] is None
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"
    captured["runner_options"]["player_factory"](object(), "arkania", "Arkania")
    assert captured["player_config"].con_reply_mode == "sealed_cons"
    assert captured["player_config"].prompt_version == "player-v1.11"

    # visible_reply selects the reply-aware player prompt; the Umpire default is already
    # reply-aware (D-114).
    result = invoke_run(package, "--con-reply-mode", "visible_reply", "--reply-max-points", "2",
                     "--activation-mode", "tool_loop")
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["con_reply_mode"] == "visible_reply"
    assert captured["runner_options"]["reply_max_points"] == 2
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"
    recipe = captured["runner_options"]["run_configuration"]["resume"]
    assert recipe["coordinator"]["con_reply_mode"] == "visible_reply"
    assert recipe["coordinator"]["reply_max_points"] == 2
    assert recipe["umpire"]["prompt_version"] == "umpire-v1.7"
    captured["runner_options"]["player_factory"](object(), "arkania", "Arkania")
    assert captured["player_config"].con_reply_mode == "visible_reply"
    assert captured["player_config"].prompt_version == "player-v1.12"

    # A pin still wins over the default.
    result = invoke_run(package, "--umpire-prompt-version", "umpire-v1.4")
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["con_reply_mode"] == "sealed_cons"
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.4"

    # Environment resolution, persisted the same way.
    monkeypatch.setenv("AMAW_CON_REPLY_MODE", "visible_reply")
    monkeypatch.setenv("AMAW_REPLY_MAX_POINTS", "3")
    result = invoke_run(package)
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["reply_max_points"] == 3
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"


def test_i046_011e_cli_rejects_reply_unaware_umpire_and_bad_bounds(tmp_path: Path, monkeypatch) -> None:
    package, captured = _cli_capture(tmp_path, monkeypatch)
    for extra in (
        ["--con-reply-mode", "visible_reply", "--reply-max-points", "2",
         "--umpire-prompt-version", "umpire-v1.3"],
        ["--con-reply-mode", "visible_reply"],
        ["--reply-max-points", "2"],
        ["--con-reply-mode", "visible_reply", "--reply-max-points", "4"],
        ["--umpire-prompt-version", "umpire-v9.9"],
    ):
        result = invoke_run(package, *extra)
        assert result.exit_code == 2, (extra, result.output)
        assert "runner_options" not in captured


def test_i046_009c_ai_umpire_loads_the_prompt_of_its_declared_version() -> None:
    client_ = OpenRouterClient(
        api_key="sk-test", transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    v13 = AIUmpire(client_, model="test/umpire")
    v14 = AIUmpire(client_, model="test/umpire", prompt_version="umpire-v1.4")
    assert v13.prompt_version == "umpire-v1.3" and "owner_reply" not in v13._prompt
    assert v14.prompt_version == "umpire-v1.4" and "owner_reply" in v14._prompt
    assert v14._prompt == (REPO_ROOT / "docs/prompts/umpire-v1.4.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="prompt_version"):
        AIUmpire(client_, model="test/umpire", prompt_version="umpire-v9.9")
