"""T-042 acceptance: the AI Umpire (`agents/umpire.py`, agents.md §20–§27, TRD §22/§35).

The real LLM adjudicator behind the same ``app.umpire.Umpire`` protocol as the FakeUmpire. It
*proposes*; the server *resolves* — it never rolls, never decides the branch, never writes
Effects (agents.md §26, INV-RNG-001, INV-EFFECT-002). The whole default suite stays offline and
key-free (testing.md §3) by driving an ``httpx.MockTransport`` through the T-038
:class:`OpenRouterClient` (D-020); a single ``@pytest.mark.llm`` test exercises a real model and
skips cleanly without a key.

Covered here:

- a well-formed model response maps to an :class:`AdjudicationProposal` for each mode, and driving
  :func:`resolve_adjudication` with the ``AIUmpire`` reproduces the ``TEST-ADJ-001…006`` server-
  resolution semantics (server owns the roll; probabilistic draws exactly one server roll);
- a malformed/invalid model response never becomes an authoritative record — refused by the
  server's ``_validate_proposal``/``_validate_supersession`` with no partial write, or (when
  unusable) surfaced as an outage;
- exactly one retry on a transient model failure, none on a deterministic one;
- an Umpire outage pauses the game with no draw/record/Effect, emits ``game_paused``, does not
  double-draw on a retry, and surfaces at the API as ``503 adjudicator_unavailable``
  (TEST-RNG-003 parity: the Umpire cannot inject a roll or a success);
- (the import boundary of ``agents.umpire`` is in ``test_architecture_boundaries.py``).
"""

from __future__ import annotations

import dataclasses
import json
import uuid

import httpx
import pytest
from sqlalchemy import Engine
from sqlmodel import select

from agents.model_client import OpenRouterClient
from agents.umpire import (
    UMPIRE_PROMPT_VERSION,
    UMPIRE_PROMPT_VERSIONS,
    AIUmpire,
    FakeUmpire,
    UmpireProvenance,
    load_umpire_prompt,
)
from app import game_service, persistence
from app.database import session_scope
from app.models import (
    Actor,
    AdjudicationMode,
    AdjudicationRecord,
    Argument,
    Effect,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
)
from app.umpire import (
    MAX_EFFECTS_PER_BRANCH,
    AdjudicationContext,
    AdjudicationProposal,
    RecordedUmpire,
    Umpire,
    UmpireUnavailable,
)

ACTORS = ("arkania", "belvar", "cestia")
SEED = 424242

_CTX = AdjudicationContext(
    scenario_summary="A regional standoff.",
    turn=2,
    turn_length="2 weeks",
    argument={
        "actor_id": "arkania",
        "action": "Mobilise reserves",
        "desired_effect": "Reserves deploy to the frontier",
        "pros": ["Plans exist"],
    },
    cons=[{"actor_id": "belvar", "reason": "Rail disruption"}],
    relevant_effects=[{"id": str(uuid.uuid4()), "description": "Tensions elevated"}],
    relevant_hidden_information=[],
)


# --- offline model plumbing -------------------------------------------------------------


def _umpire_body(output: dict, *, model: str = "test/umpire") -> dict:
    """A chat-completion envelope whose assistant content is the Umpire's structured JSON."""
    return {
        "id": "gen-umpire-1",
        "model": model,
        "provider": "TestProvider",
        "choices": [
            {"message": {"role": "assistant", "content": json.dumps(output)}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _umpire_with_output(output: dict, *, model: str = "test/umpire") -> AIUmpire:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_umpire_body(output, model=model))

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIUmpire(client, model=model)


def _umpire_returning_text(text: str) -> AIUmpire:
    def handler(_request: httpx.Request) -> httpx.Response:
        body = {
            "id": "gen-umpire-1",
            "model": "test/umpire",
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        }
        return httpx.Response(200, json=body)

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIUmpire(client, model="test/umpire")


# --- game setup at the adjudication boundary --------------------------------------------


def _seed_running_argument_game(engine: Engine, *, seed: int = SEED) -> uuid.UUID:
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="resolution",
                turn=1,
                max_turns=6,
                phase=GamePhase.argument,
                status=GameStatus.running,
                turn_order=list(ACTORS),
                active_actor_id="arkania",
                seed=seed,
                primary_arguments_per_actor=1,
                max_cons_per_actor=3,
            ),
        )
        for actor_id in ACTORS:
            persistence.create(
                session,
                Actor(
                    game_id=game.id,
                    id=actor_id,
                    name=actor_id.title(),
                    public_description="public",
                    private_brief="private",
                ),
            )
        return game.id


def _adjudicating_game(engine: Engine, *, seed: int = SEED) -> tuple[uuid.UUID, uuid.UUID]:
    game_id = _seed_running_argument_game(engine, seed=seed)
    argument_id = game_service.make_argument(
        engine,
        game_id,
        actor_id="arkania",
        action="Mobilise reserves",
        desired_effect="Reserves deploy to the frontier",
        pros=["Plans exist"],
    ).id
    game_service.pass_con(engine, game_id, argument_id=argument_id, actor_id="belvar")
    game_service.pass_con(engine, game_id, argument_id=argument_id, actor_id="cestia")
    return game_id, argument_id


def _game(engine: Engine, game_id: uuid.UUID) -> Game:
    with session_scope(engine) as session:
        game = persistence.get(session, Game, game_id)
        assert game is not None
        session.expunge(game)
        return game


def _effects(engine: Engine, game_id: uuid.UUID) -> list[Effect]:
    with session_scope(engine) as session:
        rows = list(session.exec(select(Effect).where(Effect.game_id == game_id)).all())
        for row in rows:
            session.expunge(row)  # detach before commit so columns stay readable
        return rows


def _record(engine: Engine, argument_id: uuid.UUID) -> AdjudicationRecord | None:
    with session_scope(engine) as session:
        rec = session.get(AdjudicationRecord, argument_id)
        if rec is not None:
            session.expunge(rec)  # detach before commit so its columns stay readable
        return rec


def _events(engine: Engine, game_id: uuid.UUID) -> list[str]:
    with session_scope(engine) as session:
        rows = session.exec(select(GameEvent).where(GameEvent.game_id == game_id)).all()
        return [r.type for r in rows]


# --- 1. well-formed responses map to a proposal for each mode ---------------------------


def test_aiumpire_is_an_umpire() -> None:
    umpire: Umpire = _umpire_with_output(
        {"mode": "automatic_success", "rationale": "clear", "success_effects": ["ok"]}
    )
    assert isinstance(umpire, Umpire)


def test_wellformed_probabilistic_response_maps_to_proposal() -> None:
    umpire = _umpire_with_output(
        {
            "mode": "probabilistic",
            "probability": 0.65,
            "rationale": "Contested but plausible.",
            "success_effects": ["Reserves deploy."],
            "failure_effects": ["Mobilisation stalls."],
            "success_supersedes_effect_ids": [],
            "failure_supersedes_effect_ids": [],
        }
    )
    proposal = umpire.adjudicate(_CTX)
    assert isinstance(proposal, AdjudicationProposal)
    assert proposal.mode is AdjudicationMode.probabilistic
    assert proposal.probability == 0.65
    assert proposal.rationale == "Contested but plausible."
    assert proposal.success_effects == ["Reserves deploy."]
    assert proposal.failure_effects == ["Mobilisation stalls."]


@pytest.mark.parametrize("mode", ["automatic_success", "automatic_failure"])
def test_wellformed_automatic_response_maps_to_proposal(mode: str) -> None:
    umpire = _umpire_with_output(
        {"mode": mode, "probability": None, "rationale": "decisive", "success_effects": ["s"]}
    )
    proposal = umpire.adjudicate(_CTX)
    assert proposal.mode is AdjudicationMode(mode)
    assert proposal.probability is None


def test_supersession_ids_parsed_to_uuids() -> None:
    allowed_target = uuid.UUID(_CTX.relevant_effects[0]["id"])
    umpire = _umpire_with_output(
        {
            "mode": "automatic_success",
            "rationale": "supersede",
            "success_effects": ["new"],
            "success_supersedes_effect_ids": [str(allowed_target)],
            "failure_supersedes_effect_ids": [str(allowed_target)],
        }
    )
    proposal = umpire.adjudicate(_CTX)
    assert proposal.success_supersedes_effect_ids == [allowed_target]
    assert proposal.failure_supersedes_effect_ids == [allowed_target]


def test_provenance_is_exposed_for_t053() -> None:
    umpire = _umpire_with_output(
        {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]},
        model="anthropic/claude-umpire",
    )
    result = umpire.adjudicate_recorded(_CTX)
    prov = umpire.last_provenance
    assert isinstance(prov, UmpireProvenance)
    assert prov.model_requested == "anthropic/claude-umpire"
    assert prov.model_returned == "anthropic/claude-umpire"
    assert prov.provider == "TestProvider"
    assert prov.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert prov.latency_seconds >= 0.0
    assert prov.prompt_version == UMPIRE_PROMPT_VERSION
    [pending] = result.attempts  # I-081: the call's own records, nothing queued to drain
    assert pending["model_calls"] == 1
    assert pending["prompt_hash"] == umpire.provenance_configuration()["prompt_hash"]


def test_prompt_is_the_versioned_umpire_prompt_loaded_from_file() -> None:
    sent: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_umpire_body(
                {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]}
            ),
        )

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    AIUmpire(client, model="test/umpire", reasoning_effort="medium").adjudicate(_CTX)

    messages = sent["body"]["messages"]
    response_format = sent["body"]["response_format"]
    assert sent["body"]["provider"]["require_parameters"] is True
    assert sent["body"]["reasoning"] == {"effort": "medium"}
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert set(response_format["json_schema"]["schema"]["properties"]["mode"]["enum"]) == {
        mode.value for mode in AdjudicationMode
    }
    assert response_format["json_schema"]["schema"]["additionalProperties"] is False
    for field in ("success_effects", "failure_effects"):
        effects = response_format["json_schema"]["schema"]["properties"][field]
        assert effects["maxItems"] == MAX_EFFECTS_PER_BRANCH
    for field in (
        "success_supersedes_effect_ids",
        "failure_supersedes_effect_ids",
    ):
        supersedes = response_format["json_schema"]["schema"]["properties"][field]
        assert supersedes["maxItems"] == MAX_EFFECTS_PER_BRANCH
        assert supersedes["uniqueItems"] is True
        assert supersedes["items"]["enum"] == [_CTX.relevant_effects[0]["id"]]
        assert supersedes["items"]["format"] == "uuid"
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == load_umpire_prompt()
    assert UMPIRE_PROMPT_VERSION == "umpire-v1.3"
    # The Argument text is carried as data in the user message, never merged into the system prompt.
    assert messages[-1]["role"] == "user"
    assert "Mobilise reserves" in messages[-1]["content"]


def test_i068_schema_caps_supersession_with_more_eligible_effects_than_the_limit() -> None:
    sent: dict = {}
    relevant_effects = [
        {"id": str(uuid.uuid4()), "description": f"Active effect {index}"}
        for index in range(MAX_EFFECTS_PER_BRANCH + 21)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_umpire_body(
                {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]}
            ),
        )

    context = AdjudicationContext(
        scenario_summary=_CTX.scenario_summary,
        turn=_CTX.turn,
        turn_length=_CTX.turn_length,
        argument=_CTX.argument,
        cons=_CTX.cons,
        relevant_effects=relevant_effects,
    )
    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    AIUmpire(client, model="test/umpire").adjudicate(context)

    properties = sent["body"]["response_format"]["json_schema"]["schema"]["properties"]
    for field in ("success_supersedes_effect_ids", "failure_supersedes_effect_ids"):
        supersedes = properties[field]
        assert len(supersedes["items"]["enum"]) == MAX_EFFECTS_PER_BRANCH + 21
        assert supersedes["maxItems"] == MAX_EFFECTS_PER_BRANCH
        assert supersedes["uniqueItems"] is True


def test_adj_default_umpire_prompt_is_v13() -> None:
    """The default Umpire prompt stays ``umpire-v1.3``; later versions are selected by condition."""
    assert UMPIRE_PROMPT_VERSION == "umpire-v1.3"


def _policy_text(version: str) -> str:
    return " ".join(load_umpire_prompt(version=version).lower().split())


@pytest.mark.parametrize("version", UMPIRE_PROMPT_VERSIONS)
def test_adj_007_unsupported_components_are_omitted_not_made_probabilistic(version: str) -> None:
    """TEST-ADJ-007: every shipped Umpire prompt omits unsupported components from both branches.

    The policy is model input, so it is proven on each prompt a condition can select (D-044).
    """
    lowered = _policy_text(version)
    assert "authority / scope gate" in lowered
    assert "randomness may resolve uncertainty" in lowered
    assert "omit every unsupported component from both branches" in lowered
    assert "do not assign probability to arithmetic" in lowered


@pytest.mark.parametrize("version", UMPIRE_PROMPT_VERSIONS)
def test_adj_008_self_binding_is_identical_in_both_branches(version: str) -> None:
    """TEST-ADJ-008: every shipped Umpire prompt repeats a self-binding Effect in both branches."""
    assert "repeat the self-binding effect identically in both branches" in _policy_text(version)


@pytest.mark.parametrize("version", UMPIRE_PROMPT_VERSIONS)
def test_adj_009_supersession_policy_is_causal(version: str) -> None:
    """TEST-ADJ-009: every shipped Umpire prompt refuses supersession by mere restatement."""
    lowered = _policy_text(version)
    assert "restating the opposite proposition" in lowered
    assert "never restate a hidden fact" in lowered


# --- 2. server-resolution semantics reproduced through the AI path (TEST-ADJ-001…006) ---


def test_adj_001_parity_automatic_success_no_draw(db_engine: Engine) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    umpire = _umpire_with_output(
        {
            "mode": "automatic_success",
            "rationale": "clear",
            "success_effects": ["Reserves deploy."],
            "failure_effects": ["Stalls."],
        }
    )
    outcome = game_service.resolve_adjudication(db_engine, game_id, umpire)
    assert outcome.success is True and outcome.roll is None
    assert _game(db_engine, game_id).rng_draw_index == 0  # automatic → no draw
    descriptions = {e.description for e in _effects(db_engine, game_id)}
    assert "Reserves deploy." in descriptions and "Stalls." not in descriptions
    assert _record(db_engine, argument_id) is not None


def test_adj_probabilistic_parity_draws_exactly_one_server_roll(db_engine: Engine) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    umpire = _umpire_with_output(
        {
            "mode": "probabilistic",
            "probability": 1.0,
            "rationale": "likely",
            "success_effects": ["Reserves deploy."],
            "failure_effects": ["Stalls."],
        }
    )
    outcome = game_service.resolve_adjudication(db_engine, game_id, umpire)
    # The server drew: p=1.0 always succeeds, and the roll is server-supplied, never the Umpire's.
    assert outcome.roll is not None and 0.0 <= outcome.roll <= 1.0
    assert outcome.success is True
    assert _game(db_engine, game_id).rng_draw_index == 1  # exactly one server draw
    record = _record(db_engine, argument_id)
    assert record is not None and record.roll == outcome.roll


def test_adj_006_parity_cross_game_supersession_refused(db_engine: Engine) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    foreign = uuid.uuid4()  # an Effect id that is not part of this game
    umpire = FakeUmpire(
        AdjudicationProposal(
            mode=AdjudicationMode.automatic_success,
            rationale="supersede a foreign effect",
            success_effects=["new"],
            success_supersedes_effect_ids=[foreign],
        )
    )
    with pytest.raises(game_service.InvalidSupersession):
        game_service.resolve_adjudication(db_engine, game_id, umpire)
    # No partial write.
    assert _record(db_engine, argument_id) is None
    assert _game(db_engine, game_id).rng_draw_index == 0
    assert _effects(db_engine, game_id) == []


def test_umpire_context_carries_every_actors_private_facts(db_engine: Engine) -> None:
    """agents.md §21 / D-029: the server passes all Actors' hidden facts, tagged by owner."""
    from tests.api_helpers import seed, start_to_argument

    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania is the active Actor in turn 1
    accepted = game_service.make_argument(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        action="Begin mobilisation",
        desired_effect="Reserves deploy",
        pros=["Plans exist"],
    )
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        argument = session.get(Argument, accepted.id)
        context = game_service._build_umpire_context(session, game, argument)

    items = context.relevant_hidden_information
    assert all(set(item) == {"kind", "actor_id", "text"} for item in items)
    by_text = {item["text"]: item for item in items}
    assert by_text["Reserve mobilisation requires two turns."] == {
        "kind": "private_information",
        "actor_id": "arkania",
        "text": "Reserve mobilisation requires two turns.",
    }
    assert by_text["Domestic support for a prolonged deployment is limited."]["actor_id"] == "belvar"
    assert by_text["The coalition government is politically fragile."]["actor_id"] == "cestia"
    assert by_text["Arkania's reserve mobilisation plan is incomplete."] == {
        "kind": "private_effect",
        "actor_id": "arkania",
        "text": "Arkania's reserve mobilisation plan is incomplete.",
    }
    # Public Effects appear only in relevant_effects; private ones appear there tagged AND in
    # the hidden list.
    assert "Commercial shipping continues normally." not in by_text
    effects_by_text = {e["description"]: e for e in context.relevant_effects}
    private_effect = effects_by_text["Arkania's reserve mobilisation plan is incomplete."]
    assert private_effect["visibility"] == "private"
    assert private_effect["visible_to"] == ["arkania"]
    public_effect = effects_by_text["Commercial shipping continues normally."]
    assert public_effect["visibility"] == "public"
    assert public_effect["visible_to"] == []


# --- 3. malformed/invalid responses never become an authoritative record ----------------


@pytest.mark.parametrize(
    "output",
    [
        {"mode": "probabilistic", "probability": 1.1, "rationale": "r", "success_effects": ["s"]},
        {"mode": "probabilistic", "probability": -0.1, "rationale": "r", "success_effects": ["s"]},
        {"mode": "probabilistic", "probability": None, "rationale": "r", "success_effects": ["s"]},
        {"mode": "automatic_success", "probability": 0.5, "rationale": "r", "success_effects": ["s"]},
        {"mode": "probabilistic", "probability": 0.5, "rationale": "", "success_effects": ["s"]},
    ],
)
def test_invalid_proposal_refused_by_server_with_no_partial_write(
    db_engine: Engine, output: dict
) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    umpire = _umpire_with_output(output)
    with pytest.raises(game_service.InvalidAdjudication):
        game_service.resolve_adjudication(db_engine, game_id, umpire)
    assert _record(db_engine, argument_id) is None
    assert _game(db_engine, game_id).rng_draw_index == 0
    assert _effects(db_engine, game_id) == []
    assert _game(db_engine, game_id).status is GameStatus.running  # not paused — a validation refusal


@pytest.mark.parametrize("text", ["not json at all", "{ broken json", ""])
def test_unusable_output_is_an_outage(db_engine: Engine, text: str) -> None:
    umpire = _umpire_returning_text(text)
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)


def test_invalid_mode_is_an_outage() -> None:
    umpire = _umpire_with_output({"mode": "maybe", "probability": 0.5, "rationale": "r"})
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)


# --- 4. exactly one retry (transient), none (deterministic) -----------------------------


def test_bounded_transient_attempts_then_outage() -> None:
    """D-063: transient failures consume the four-call bound, one provenance row per call."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "overloaded"})

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    result = umpire.adjudicate_recorded(_CTX)
    assert result.proposal is None
    assert calls["n"] == 4  # the whole bound, then pause
    failed = result.attempts
    assert len(failed) == 4
    assert all(row["model_calls"] == 1 and row["completed"] is False for row in failed)


def test_no_retry_on_deterministic_then_outage() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "bad key"})

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)
    assert calls["n"] == 1  # deterministic → no retry


def test_malformed_success_body_is_retried_then_named_in_last_failure() -> None:
    """I-023: an HTTP-200 body with no completion is retried once; the pause reason says why.

    A body with no choice at all is a malformed gateway envelope (transport-level transient).
    A completed choice whose ``content`` is ``null`` is *unusable output* instead — see I-070.
    Transient failures draw on the D-063 four-call bound.
    """
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": []})

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)
    assert calls["n"] == 4
    assert umpire.last_failure == (
        "provider:transient:status=none:provider response omitted a valid choice"
    )
    assert "sk-test" not in umpire.last_failure


def test_unusable_output_is_retried_once_then_recovers() -> None:
    """I-026: a truncated/unusable completion gets the single retry, like the player path."""
    calls = {"n": 0}
    good = {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            truncated = _umpire_body(good)
            truncated["choices"][0]["message"]["content"] = '{"mode": "automatic_succ'
            truncated["choices"][0]["finish_reason"] = "length"
            return httpx.Response(200, json=truncated)
        return httpx.Response(200, json=_umpire_body(good))

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    proposal = umpire.adjudicate(_CTX)
    assert proposal.mode is AdjudicationMode.automatic_success
    assert calls["n"] == 2
    assert umpire.last_failure is None


def test_i067_001_unusable_output_retry_excludes_failed_provider() -> None:
    """The one output retry keeps the model but diversifies a bad HTTP-200 endpoint."""
    requests: list[dict] = []
    good = {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            body = _umpire_body({})
            body["provider"] = "OpenInference"
            body["choices"][0]["message"]["content"] = ""
            body["usage"] = {
                "prompt_tokens": 5295,
                "completion_tokens": 698,
                "completion_tokens_details": {"reasoning_tokens": 698},
            }
            return httpx.Response(200, json=body)
        body = _umpire_body(good)
        body["provider"] = "DeepInfra"
        return httpx.Response(200, json=body)

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    proposal = AIUmpire(client, model="deepseek/deepseek-v4-flash-0731").adjudicate(_CTX)

    assert proposal.mode is AdjudicationMode.automatic_success
    assert len(requests) == 2
    assert requests[0]["model"] == requests[1]["model"] == "deepseek/deepseek-v4-flash-0731"
    assert requests[0]["provider"] == {"require_parameters": True}
    assert requests[1]["provider"] == {
        "require_parameters": True,
        "ignore": ["OpenInference"],
    }


def test_i070_001_null_content_completion_takes_the_provider_excluding_retry() -> None:
    """I-070: ``content: null`` from a completed choice is unusable *output*, not a transport
    transient — it takes the D-061 retry (same model, failing provider ignored), not the
    client-level retry against unchanged routing."""
    requests: list[dict] = []
    good = {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            # gen-1789656628-2x6llSJ2uUen3deJus2p: reasoning consumed max_tokens, no answer.
            body = _umpire_body({})
            body["provider"] = "OpenInference"
            body["choices"][0]["message"]["content"] = None
            body["choices"][0]["finish_reason"] = "length"
            body["usage"] = {
                "prompt_tokens": 4221,
                "completion_tokens": 16000,
                "completion_tokens_details": {"reasoning_tokens": 16000},
            }
            return httpx.Response(200, json=body)
        body = _umpire_body(good)
        body["provider"] = "DeepInfra"
        return httpx.Response(200, json=body)

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="deepseek/deepseek-v4-flash-0731")
    proposal = umpire.adjudicate(_CTX)

    assert proposal.mode is AdjudicationMode.automatic_success
    assert len(requests) == 2  # still exactly one retry in total
    assert requests[0]["model"] == requests[1]["model"] == "deepseek/deepseek-v4-flash-0731"
    assert requests[1]["provider"] == {
        "require_parameters": True,
        "ignore": ["OpenInference"],
    }
    assert umpire.last_failure is None


def test_i070_002_null_content_exhausting_the_bound_names_finish_reason() -> None:
    """I-070: null-content completions that exhaust the bound pause the game and the reason
    keeps ``finish_reason``."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = _umpire_body({})
        body["choices"][0]["message"]["content"] = None
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)
    assert calls["n"] == 4  # D-063 bound
    assert umpire.last_failure == (
        "output:Umpire returned no usable JSON object:finish_reason=length"
    )


def test_unusable_output_exhausting_the_bound_is_an_outage_naming_truncation() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = _umpire_body({})
        body["choices"][0]["message"]["content"] = "reasoning that never ends"
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)
    assert calls["n"] == 4  # the D-063 bound, then pause — never fabricate
    assert umpire.last_failure == (
        "output:Umpire returned no usable JSON object:finish_reason=length"
    )


def _unusable_from(provider: str) -> httpx.Response:
    body = _umpire_body({})
    body["provider"] = provider
    body["choices"][0]["message"]["content"] = ""
    body["choices"][0]["finish_reason"] = "length"
    return httpx.Response(200, json=body)


_GOOD = {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]}


def test_i071_002_four_attempts_with_cumulative_provider_exclusion() -> None:
    """D-063: up to four model calls; each unusable completion's provider stays excluded."""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) <= 3:
            return _unusable_from(["OpenInference", "DeepInfra", "Novita"][len(requests) - 1])
        body = _umpire_body(_GOOD)
        body["provider"] = "Together"
        return httpx.Response(200, json=body)

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="deepseek/deepseek-v4-flash-0731")
    proposal = umpire.adjudicate(_CTX)

    assert proposal.mode is AdjudicationMode.automatic_success
    assert len(requests) == 4
    assert {r["model"] for r in requests} == {"deepseek/deepseek-v4-flash-0731"}
    assert [r["provider"].get("ignore") for r in requests] == [
        None,
        ["OpenInference"],
        ["OpenInference", "DeepInfra"],
        ["OpenInference", "DeepInfra", "Novita"],
    ]
    assert all(r["provider"]["require_parameters"] is True for r in requests)
    assert umpire.last_failure is None


def test_i071_003_fourth_unusable_completion_pauses_the_game() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _unusable_from("OpenInference")

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)
    assert calls["n"] == 4  # the bound, then pause — never a fifth call, never fabricate
    assert umpire.last_failure == (
        "output:Umpire returned no usable JSON object:finish_reason=length"
    )


def test_i071_004_transient_failures_consume_attempts_within_the_same_bound() -> None:
    """A 5xx and an unusable completion draw from the one four-call bound; recovery on the third."""
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": "overloaded"})
        if len(requests) == 2:
            return _unusable_from("OpenInference")
        return httpx.Response(200, json=_umpire_body(_GOOD))

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    proposal = umpire.adjudicate(_CTX)
    assert proposal.mode is AdjudicationMode.automatic_success
    assert len(requests) == 3
    assert requests[2]["provider"] == {"require_parameters": True, "ignore": ["OpenInference"]}


def test_i071_005_four_transient_failures_pause_the_game() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "overloaded"})

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_CTX)
    assert calls["n"] == 4
    assert umpire.last_failure == "provider:transient:status=503:provider returned 503 for model test/umpire"


def test_i072_003_attempt_bound_and_deadline_are_fixed_provenance_conditions() -> None:
    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(lambda r: None))
    settings = AIUmpire(client, model="test/umpire").provenance_configuration()["settings"]
    assert settings["attempts"] == 4
    assert settings["deadline"] == 600.0


def test_i073_001_generation_id_is_kept_in_call_provenance() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        body = _umpire_body(_GOOD)
        body["id"] = "gen-1789657317-fM5Q6qNbXPbcc715beLi"
        return httpx.Response(200, json=body)

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    umpire = AIUmpire(client, model="test/umpire")
    (record,) = umpire.adjudicate_recorded(_CTX).attempts
    assert record["provider_metadata"]["generation_id"] == "gen-1789657317-fM5Q6qNbXPbcc715beLi"


def _semantic_invalid_from(
    provider: str,
    effect_id: uuid.UUID,
    *,
    generation_id: str,
) -> httpx.Response:
    body = _umpire_body(
        {
            "mode": "automatic_success",
            "probability": None,
            "rationale": "Replace an earlier effect.",
            "success_effects": ["A replacement effect."],
            "failure_effects": [],
            "success_supersedes_effect_ids": [str(effect_id)],
            "failure_supersedes_effect_ids": [],
        }
    )
    body["provider"] = provider
    body["id"] = generation_id
    return httpx.Response(200, json=body)


def test_i074_001_context_invalid_supersession_retries_with_provider_exclusion() -> None:
    requests: list[dict] = []
    invalid_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return _semantic_invalid_from(
                "Alibaba", invalid_id, generation_id="gen-invalid-supersession"
            )
        body = _umpire_body(_GOOD)
        body["provider"] = "DeepInfra"
        body["id"] = "gen-valid-retry"
        return httpx.Response(200, json=body)

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler)),
        model="deepseek/deepseek-v4-flash-0731",
    )
    result = umpire.adjudicate_recorded(_CTX)
    proposal = result.proposal

    assert proposal is not None and proposal.mode is AdjudicationMode.automatic_success
    assert len(requests) == 2
    assert requests[1]["provider"] == {
        "require_parameters": True,
        "ignore": ["Alibaba"],
    }
    failed, recovered = result.attempts
    assert failed["completed"] is False
    assert failed["failure_reason"] == (
        "output:context-invalid supersession reference:finish_reason=stop"
    )
    assert failed["provider_metadata"]["semantic_failure"] == {
        "code": "context_invalid_supersession",
        "offending_effect_ids": [str(invalid_id)],
    }
    assert failed["provider_metadata"]["generation_id"] == "gen-invalid-supersession"
    assert recovered["completed"] is True
    assert recovered["failure_reason"] is None
    assert recovered["provider_metadata"]["generation_id"] == "gen-valid-retry"
    assert umpire.last_failure is None


def test_i074_002_semantic_retry_keeps_the_fixed_umpire_request() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return _semantic_invalid_from(
                "Alibaba", uuid.uuid4(), generation_id="gen-invalid-request"
            )
        return httpx.Response(200, json=_umpire_body(_GOOD))

    AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler)),
        model="deepseek/deepseek-v4-flash-0731",
        reasoning_effort="low",
    ).adjudicate(_CTX)

    assert len(requests) == 2
    first_without_routing = {key: value for key, value in requests[0].items() if key != "provider"}
    second_without_routing = {
        key: value for key, value in requests[1].items() if key != "provider"
    }
    assert first_without_routing == second_without_routing
    assert requests[0]["provider"] == {"require_parameters": True}
    assert requests[1]["provider"] == {
        "require_parameters": True,
        "ignore": ["Alibaba"],
    }


def test_i074_003_four_context_invalid_supersessions_exhaust_the_bound() -> None:
    requests: list[dict] = []
    providers = ["Alibaba", "DeepInfra", "Novita", "Together"]
    invalid_ids = [uuid.uuid4() for _ in providers]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        index = len(requests) - 1
        return _semantic_invalid_from(
            providers[index], invalid_ids[index], generation_id=f"gen-invalid-{index + 1}"
        )

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler)),
        model="deepseek/deepseek-v4-flash-0731",
    )
    result = umpire.adjudicate_recorded(_CTX)
    assert result.proposal is None

    assert len(requests) == 4
    assert [request["provider"].get("ignore") for request in requests] == [
        None,
        ["Alibaba"],
        ["Alibaba", "DeepInfra"],
        ["Alibaba", "DeepInfra", "Novita"],
    ]
    assert result.failure == umpire.last_failure == (
        "output:context-invalid supersession reference:finish_reason=stop"
    )
    attempts = result.attempts
    assert len(attempts) == 4
    assert all(attempt["completed"] is False for attempt in attempts)
    assert [attempt["provider"] for attempt in attempts] == providers
    assert [attempt["provider_metadata"]["generation_id"] for attempt in attempts] == [
        "gen-invalid-1",
        "gen-invalid-2",
        "gen-invalid-3",
        "gen-invalid-4",
    ]


def test_i074_004_exhaustion_pauses_without_rng_adjudication_or_effects(
    db_engine: Engine,
) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _semantic_invalid_from(
            f"Provider-{calls}", uuid.uuid4(), generation_id=f"gen-pause-{calls}"
        )

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler)),
        model="test/umpire",
    )
    recorded = RecordedUmpire(umpire)  # I-081: the adjudication owns its attempt records
    with pytest.raises(game_service.AdjudicatorUnavailable):
        game_service.resolve_adjudication(db_engine, game_id, recorded)

    assert calls == 4
    game = _game(db_engine, game_id)
    assert game.status is GameStatus.paused
    assert game.phase is GamePhase.adjudication
    assert game.rng_draw_index == 0
    assert _record(db_engine, argument_id) is None
    assert _effects(db_engine, game_id) == []
    assert "game_paused" in _events(db_engine, game_id)
    assert recorded.result is not None and recorded.result.proposal is None
    attempts = recorded.result.attempts
    assert len(attempts) == 4
    assert all(attempt["completed"] is False for attempt in attempts)


def test_i074_005_duplicate_supersession_is_retryable_output() -> None:
    requests: list[dict] = []
    allowed_id = _CTX.relevant_effects[0]["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            body = _umpire_body(
                {
                    "mode": "automatic_success",
                    "probability": None,
                    "rationale": "Replace an earlier effect.",
                    "success_effects": ["A replacement effect."],
                    "failure_effects": [],
                    "success_supersedes_effect_ids": [allowed_id, allowed_id],
                    "failure_supersedes_effect_ids": [],
                }
            )
            body["provider"] = "Alibaba"
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=_umpire_body(_GOOD))

    umpire = AIUmpire(
        OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler)),
        model="test/umpire",
    )
    result = umpire.adjudicate_recorded(_CTX)

    assert result.proposal is not None
    assert result.proposal.mode is AdjudicationMode.automatic_success
    assert len(requests) == 2
    failed, recovered = result.attempts
    assert failed["provider_metadata"]["semantic_failure"] == {
        "code": "duplicate_supersession",
        "offending_effect_ids": [allowed_id],
    }
    assert failed["completed"] is False
    assert recovered["completed"] is True


def test_transient_recovers_on_the_single_retry() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "overloaded"})
        return httpx.Response(
            200,
            json=_umpire_body(
                {"mode": "automatic_success", "rationale": "r", "success_effects": ["s"]}
            ),
        )

    client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    proposal = AIUmpire(client, model="test/umpire").adjudicate(_CTX)
    assert proposal.mode is AdjudicationMode.automatic_success
    assert calls["n"] == 2


# --- 5. outage → paused game, no fabrication, no double draw ----------------------------


class _OutageUmpire:
    """A minimal :class:`Umpire` that always signals unavailability (post-retry outage)."""

    def adjudicate(self, context: AdjudicationContext) -> AdjudicationProposal:
        raise UmpireUnavailable("model unavailable after retry")


def test_outage_pauses_game_with_no_draw_record_or_effect(db_engine: Engine) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)

    with pytest.raises(game_service.AdjudicatorUnavailable):
        game_service.resolve_adjudication(db_engine, game_id, _OutageUmpire())

    game = _game(db_engine, game_id)
    assert game.status is GameStatus.paused  # paused, never a fabricated resolution
    assert game.phase is GamePhase.adjudication  # phase retained (state-machine §14.443)
    assert game.rng_draw_index == 0  # TEST-RNG-003 parity: no roll injected
    assert _record(db_engine, argument_id) is None
    assert _effects(db_engine, game_id) == []
    assert "game_paused" in _events(db_engine, game_id)
    # The Argument is untouched — still adjudicating, resumable by admin tooling (out of scope).
    with session_scope(db_engine) as session:
        assert session.get(Argument, argument_id).status == "adjudicating"


def test_rng_003_umpire_output_cannot_inject_the_roll_or_the_outcome(db_engine: Engine) -> None:
    """TEST-RNG-003: an Umpire cannot choose the roll; the server draws it from the game seed.

    The proposal type has no roll or success field, the requested response schema forbids extra
    properties, and a model that adds ``roll``/``success`` anyway resolves exactly as one that
    does not (INV-RNG-001, INV-EFFECT-002).
    """
    assert not {f.name for f in dataclasses.fields(AdjudicationProposal)} & {"roll", "success"}

    clean = {
        "mode": "probabilistic",
        "probability": 0.5,
        "rationale": "even odds",
        "success_effects": ["Reserves deploy."],
        "failure_effects": ["Stalls."],
    }
    requests: list[dict] = []

    def resolve(output: dict) -> AdjudicationRecord:
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=_umpire_body(output))

        client = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
        # Each call seeds a fresh game on the same seed, so both face the same first draw.
        game_id, argument_id = _adjudicating_game(db_engine)
        game_service.resolve_adjudication(db_engine, game_id, AIUmpire(client, model="test/umpire"))
        assert _game(db_engine, game_id).rng_draw_index == 1  # exactly one server draw
        record = _record(db_engine, argument_id)
        assert record is not None
        return record

    honest = resolve(clean)
    schema = requests[0]["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert not set(schema["properties"]) & {"roll", "success"}

    # The injected values contradict what the seeded draw decided.
    forged = resolve({**clean, "roll": 1.0 if honest.success else 0.0, "success": not honest.success})
    assert (forged.roll, forged.success) == (honest.roll, honest.success)


def test_subsequent_attempt_on_paused_game_does_not_double_draw(db_engine: Engine) -> None:
    game_id, _ = _adjudicating_game(db_engine)
    with pytest.raises(game_service.AdjudicatorUnavailable):
        game_service.resolve_adjudication(db_engine, game_id, _OutageUmpire())
    assert _game(db_engine, game_id).rng_draw_index == 0

    # A retry against the now-paused game is refused (not running) and draws nothing.
    with pytest.raises(game_service.InvalidPhase):
        game_service.resolve_adjudication(db_engine, game_id, _OutageUmpire())
    assert _game(db_engine, game_id).rng_draw_index == 0


def test_adjudicator_unavailable_maps_to_503(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """AdjudicatorUnavailable renders the documented ``503 adjudicator_unavailable`` envelope."""
    from tests.api_helpers import bearer, client, seed

    from app import game_service as gs

    assert gs.AdjudicatorUnavailable in __import__("app.api", fromlist=["_DOMAIN_ERROR_MAP"])._DOMAIN_ERROR_MAP

    creation = seed(db_engine)

    def _unavailable(*_args, **_kwargs):
        raise gs.AdjudicatorUnavailable("Umpire unavailable and game cannot continue")

    monkeypatch.setattr(gs, "save_note", _unavailable)
    response = client(db_engine).post(
        f"/games/{creation.game_id}/notes",
        headers=bearer(creation.seat_tokens["arkania"]),
        json={"text": "x"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "adjudicator_unavailable"


# --- 7. one provider-dependent smoke test (skips cleanly without a key) -----------------


@pytest.mark.llm
def test_real_model_adjudicates_and_server_resolves(db_engine: Engine) -> None:
    import os

    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("no OPENROUTER_API_KEY")

    from app.config import Settings

    settings = Settings()
    model = os.environ.get("AMAW_UMPIRE_MODEL", "anthropic/claude-3.5-haiku")
    umpire = AIUmpire.for_openrouter(settings, model=model)

    game_id, argument_id = _adjudicating_game(db_engine)
    outcome = game_service.resolve_adjudication(db_engine, game_id, umpire)
    # The server owns the outcome; we only assert a valid, recorded resolution — not a good game.
    assert outcome.mode in AdjudicationMode
    assert _record(db_engine, argument_id) is not None
    assert umpire.last_provenance is not None


def test_umpire_context_lists_cons_in_actor_order_not_arrival_order(db_engine: Engine) -> None:
    """I-029 / D-045: concurrent Con activations make arrival order latency-dependent, so the
    server presents Cons to the Umpire in a deterministic order — by Actor id — whatever order
    they were committed in (agents.md §21: the server constructs the context)."""
    from tests.api_helpers import seed, start_to_argument

    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania is the active Actor in turn 1
    accepted = game_service.make_argument(
        db_engine,
        creation.game_id,
        actor_id="arkania",
        action="Begin mobilisation",
        desired_effect="Reserves deploy",
        pros=["Plans exist"],
    )
    # cestia's Con arrives before belvar's — the reverse of Actor-id order.
    game_service.make_con(
        db_engine, creation.game_id, actor_id="cestia", argument_id=accepted.id,
        reason="Cestia objects first",
    )
    game_service.make_con(
        db_engine, creation.game_id, actor_id="belvar", argument_id=accepted.id,
        reason="Belvar objects second",
    )
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        argument = session.get(Argument, accepted.id)
        context = game_service._build_umpire_context(session, game, argument)

    assert [con["actor_id"] for con in context.cons] == ["belvar", "cestia"]
