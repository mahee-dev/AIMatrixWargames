"""T-019 + T-020 acceptance tests: server-owned adjudication and Effect application.

Covers TEST-ADJ-001…009, TEST-EFF-001…005 and TEST-RNG-002/003, plus the batch's atomicity,
provenance, selected-branch, supersession, cross-game and player-freeze rules. Resolution runs
entirely in-process through the FakeUmpire — no provider, no API key (testing.md §3).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine
from sqlmodel import select

from app import database, game_service, persistence, rng
from app.database import session_scope
from app.models import (
    AdjudicationMode,
    AdjudicationRecord,
    Argument,
    Effect,
    Game,
    GamePhase,
    GameStatus,
)
from app.umpire import MAX_EFFECTS_PER_BRANCH, AdjudicationProposal
from agents.umpire import FakeUmpire
from tests.db_helpers import actor_row

ACTORS = ["arkania", "belvar", "cestia"]
SEED = 424242


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
            persistence.create(session, actor_row(game.id, actor_id))
        return game.id


def _drive_to_adjudication(engine: Engine, game_id: uuid.UUID) -> uuid.UUID:
    """Run the real argument→cons→adjudication flow; return the current Argument id."""
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
    phase, _ = _game_state(engine, game_id)
    assert phase is GamePhase.adjudication
    return argument_id


def _adjudicating_game(engine: Engine, *, seed: int = SEED) -> tuple[uuid.UUID, uuid.UUID]:
    game_id = _seed_running_argument_game(engine, seed=seed)
    argument_id = _drive_to_adjudication(engine, game_id)
    return game_id, argument_id


def _game_state(engine: Engine, game_id: uuid.UUID) -> tuple[GamePhase, int]:
    with session_scope(engine) as session:
        game = persistence.get(session, Game, game_id)
        assert game is not None
        return game.phase, game.rng_draw_index


@dataclass(frozen=True)
class _EffectRow:
    id: uuid.UUID
    description: str
    active: bool
    visibility: str
    source_argument_id: uuid.UUID | None
    created_turn: int
    supersedes: tuple[uuid.UUID, ...]


def _effects(engine: Engine, game_id: uuid.UUID) -> list[_EffectRow]:
    with session_scope(engine) as session:
        rows = session.exec(select(Effect).where(Effect.game_id == game_id)).all()
        return [
            _EffectRow(
                e.id,
                e.description,
                e.active,
                e.visibility,
                e.source_argument_id,
                e.created_turn,
                tuple(e.supersedes),
            )
            for e in rows
        ]


def _record(engine: Engine, argument_id: uuid.UUID) -> AdjudicationRecord | None:
    with session_scope(engine) as session:
        rec = session.get(AdjudicationRecord, argument_id)
        if rec is None:
            return None
        session.expunge(rec)
        return rec


def _argument_status(engine: Engine, argument_id: uuid.UUID) -> str:
    with session_scope(engine) as session:
        arg = session.get(Argument, argument_id)
        assert arg is not None
        return arg.status


def _add_effect(engine: Engine, game_id: uuid.UUID, description: str) -> uuid.UUID:
    with session_scope(engine) as session:
        effect = persistence.create(
            session,
            Effect(
                game_id=game_id,
                description=description,
                visibility="public",
                visible_to=[],
                created_turn=1,
                active=True,
            ),
        )
        return effect.id


# --- T-019: adjudication modes ----------------------------------------------------------


def test_adj_001_automatic_success(db_engine: Engine) -> None:
    """TEST-ADJ-001 / TEST-EFF-001: success branch applied, no RNG draw consumed."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="clear",
        success_effects=["Reserves begin deploying."],
        failure_effects=["Mobilisation stalls."],
    )
    outcome = game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    assert outcome.success is True
    assert outcome.roll is None
    record = _record(db_engine, argument_id)
    assert record is not None and record.success is True and record.roll is None
    _, draw_index = _game_state(db_engine, game_id)
    assert draw_index == 0  # automatic mode consumes no RNG
    descriptions = {e.description for e in _effects(db_engine, game_id)}
    assert "Reserves begin deploying." in descriptions
    assert "Mobilisation stalls." not in descriptions  # unselected branch not applied
    assert _argument_status(db_engine, argument_id) == "resolved"


def test_adj_002_automatic_failure(db_engine: Engine) -> None:
    """TEST-ADJ-002 / TEST-EFF-002: failure branch applied, no RNG draw consumed."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_failure,
        rationale="impossible this turn",
        success_effects=["Reserves begin deploying."],
        failure_effects=["Mobilisation stalls."],
    )
    outcome = game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    assert outcome.success is False and outcome.roll is None
    _, draw_index = _game_state(db_engine, game_id)
    assert draw_index == 0
    descriptions = {e.description for e in _effects(db_engine, game_id)}
    assert "Mobilisation stalls." in descriptions
    assert "Reserves begin deploying." not in descriptions


def test_probabilistic_success_at_roll_equals_probability(db_engine: Engine) -> None:
    """§26 boundary: roll <= probability → success (roll == probability is success)."""
    game_id, argument_id = _adjudicating_game(db_engine)
    expected_roll = rng.draw(SEED, 0)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic,
        probability=expected_roll,  # roll == probability
        rationale="exactly even",
        success_effects=["success"],
        failure_effects=["failure"],
    )
    outcome = game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    assert outcome.roll == expected_roll
    assert outcome.success is True  # roll <= probability
    _, draw_index = _game_state(db_engine, game_id)
    assert draw_index == 1  # exactly one draw consumed


def test_rng_002_and_003_probabilistic_records_server_roll(db_engine: Engine) -> None:
    """TEST-RNG-002/003: probability/roll/result persisted; roll is server-derived."""
    game_id, argument_id = _adjudicating_game(db_engine)
    # Probability just below the deterministic draw → failure, proving roll drives the result.
    expected_roll = rng.draw(SEED, 0)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic,
        probability=max(0.0, expected_roll - 0.01),
        rationale="unlikely",
        success_effects=["success"],
        failure_effects=["failure"],
    )
    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    record = _record(db_engine, argument_id)
    assert record is not None
    assert record.probability == pytest.approx(max(0.0, expected_roll - 0.01))
    assert record.roll == expected_roll  # server drew it; the Umpire cannot inject a roll
    assert record.success is False


def test_adj_003_probabilistic_requires_probability(db_engine: Engine) -> None:
    """TEST-ADJ-003: missing probability is rejected with no partial mutation."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic, probability=None, rationale="oops"
    )
    with pytest.raises(game_service.InvalidAdjudication):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    assert _record(db_engine, argument_id) is None
    assert _effects(db_engine, game_id) == []
    _, draw_index = _game_state(db_engine, game_id)
    assert draw_index == 0  # no RNG consumed on an invalid proposal
    assert _argument_status(db_engine, argument_id) == "adjudicating"


@pytest.mark.parametrize("bad_probability", [-0.1, 1.1])
def test_adj_004_probability_bounds(db_engine: Engine, bad_probability: float) -> None:
    """TEST-ADJ-004: out-of-range probability rejected, no side effects."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic,
        probability=bad_probability,
        rationale="out of range",
    )
    with pytest.raises(game_service.InvalidAdjudication):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))
    assert _record(db_engine, argument_id) is None
    assert _game_state(db_engine, game_id)[1] == 0


def test_automatic_mode_with_probability_is_rejected(db_engine: Engine) -> None:
    """INV-ADJ-002 / agents.md §22: automatic modes must carry a null probability."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success, probability=0.9, rationale="not allowed"
    )
    with pytest.raises(game_service.InvalidAdjudication):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))
    assert _record(db_engine, argument_id) is None


def test_empty_rationale_is_rejected(db_engine: Engine) -> None:
    """agents.md §22: rationale must be non-empty."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success, rationale="   "
    )
    with pytest.raises(game_service.InvalidAdjudication):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))


def test_adj_005_double_adjudication_prevented(db_engine: Engine) -> None:
    """TEST-ADJ-005 / INV-ADJ-003: a second attempt produces no second resolution."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic,
        probability=0.5,
        rationale="once",
        success_effects=["one effect"],
    )
    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))
    index_after_first = _game_state(db_engine, game_id)[1]
    effects_after_first = len(_effects(db_engine, game_id))

    with pytest.raises(game_service.AlreadyAdjudicated):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    # No second draw, no duplicate effects, single record.
    assert _game_state(db_engine, game_id)[1] == index_after_first
    assert len(_effects(db_engine, game_id)) == effects_after_first
    with session_scope(db_engine) as session:
        records = session.exec(
            select(AdjudicationRecord).where(
                AdjudicationRecord.argument_id == argument_id
            )
        ).all()
    assert len(records) == 1


def test_resolve_outside_adjudication_phase_is_rejected(db_engine: Engine) -> None:
    game_id = _seed_running_argument_game(db_engine)  # still in argument phase
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success, rationale="wrong phase"
    )
    with pytest.raises(game_service.InvalidPhase):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))


def test_resolve_absent_game_fails_closed(db_engine: Engine) -> None:
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success, rationale="none"
    )
    with pytest.raises(game_service.GameNotFound):
        game_service.resolve_adjudication(db_engine, uuid.uuid4(), FakeUmpire(proposal))


# --- T-020: Effect application, supersession, provenance --------------------------------


def test_eff_created_effects_carry_full_provenance(db_engine: Engine) -> None:
    """TEST-EFF-003 shape: active, correct game/turn/source, public default visibility."""
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="provenance",
        success_effects=["A created effect."],
    )
    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    created = [e for e in _effects(db_engine, game_id) if e.description == "A created effect."]
    assert len(created) == 1
    effect = created[0]
    assert effect.active is True
    assert effect.visibility == "public"  # adjudication invents no hidden Effects
    assert effect.source_argument_id == argument_id
    assert effect.created_turn == 1


def test_eff_005_supersession_deactivates_target(db_engine: Engine) -> None:
    """TEST-EFF-005: new active, old inactive, old retained, new records supersession."""
    game_id, argument_id = _adjudicating_game(db_engine)
    old_id = _add_effect(db_engine, game_id, "Old state.")
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="supersede",
        success_effects=["New state."],
        success_supersedes_effect_ids=[old_id],
    )
    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    by_desc = {e.description: e for e in _effects(db_engine, game_id)}
    assert by_desc["New state."].active is True
    assert by_desc["Old state."].active is False  # deactivated, not deleted
    assert old_id in {e.id for e in _effects(db_engine, game_id)}  # retained historically
    assert old_id in by_desc["New state."].supersedes  # relationship recorded


@pytest.mark.parametrize(
    ("probability", "expected_deactivated", "expected_retained"),
    [
        (1.0, "Success blocker.", "Failure blocker."),
        (0.0, "Failure blocker.", "Success blocker."),
    ],
)
def test_adj_009_only_selected_branch_supersedes(
    db_engine: Engine,
    probability: float,
    expected_deactivated: str,
    expected_retained: str,
) -> None:
    """TEST-ADJ-009: an unselected branch cannot deactivate authoritative state."""
    game_id, _ = _adjudicating_game(db_engine)
    success_old = _add_effect(db_engine, game_id, "Success blocker.")
    failure_old = _add_effect(db_engine, game_id, "Failure blocker.")
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic,
        probability=probability,
        rationale="branch-specific supersession",
        success_effects=["Success state."],
        failure_effects=["Failure state."],
        success_supersedes_effect_ids=[success_old],
        failure_supersedes_effect_ids=[failure_old],
    )

    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    by_desc = {effect.description: effect for effect in _effects(db_engine, game_id)}
    assert by_desc[expected_deactivated].active is False
    assert by_desc[expected_retained].active is True
    selected_state = "Success state." if probability == 1.0 else "Failure state."
    assert by_desc[expected_deactivated].id in by_desc[selected_state].supersedes


def test_eff_003_active_effect_persists_across_turn(db_engine: Engine) -> None:
    """TEST-EFF-003: a non-expiring Effect stays active into the next turn."""
    game_id, _ = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="persist",
        success_effects=["Enduring state."],
    )
    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))
    # Simulate reaching turn 2 (turn advancement is T-021; drive expiry directly per D-010).
    with session_scope(db_engine) as session:
        game = persistence.get(session, Game, game_id)
        game.turn = 2
        game_service._expire_effects(session, game)
    still = {e.description: e for e in _effects(db_engine, game_id)}
    assert still["Enduring state."].active is True


def test_eff_004_expiry_deactivates_at_turn_boundary(db_engine: Engine) -> None:
    """TEST-EFF-004 / D-010: an Effect with expires_turn=2 is inactive at the start of turn 2."""
    game_id = _seed_running_argument_game(db_engine)
    with session_scope(db_engine) as session:
        persistence.create(
            session,
            Effect(
                game_id=game_id,
                description="Expiring.",
                visibility="public",
                visible_to=[],
                created_turn=1,
                expires_turn=2,
                active=True,
            ),
        )
    with session_scope(db_engine) as session:
        game = persistence.get(session, Game, game_id)
        game.turn = 2
        game_service._expire_effects(session, game)
    row = {e.description: e for e in _effects(db_engine, game_id)}["Expiring."]
    assert row.active is False


def test_adj_006_cross_game_supersession_rejected(db_engine: Engine) -> None:
    """TEST-ADJ-006: an adjudication cannot supersede an Effect from another game."""
    game_a, _ = _adjudicating_game(db_engine)
    game_b = _seed_running_argument_game(db_engine)
    foreign_effect = _add_effect(db_engine, game_b, "Foreign effect.")

    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="cross-game",
        success_effects=["new"],
        success_supersedes_effect_ids=[foreign_effect],
    )
    with pytest.raises(game_service.InvalidSupersession):
        game_service.resolve_adjudication(db_engine, game_a, FakeUmpire(proposal))

    # Nothing applied in game A; the foreign Effect in game B is untouched.
    assert _record(db_engine, _drive_target(db_engine, game_a)) is None
    foreign = {e.id: e for e in _effects(db_engine, game_b)}[foreign_effect]
    assert foreign.active is True


def _drive_target(engine: Engine, game_id: uuid.UUID) -> uuid.UUID:
    with session_scope(engine) as session:
        arg = session.exec(
            select(Argument).where(
                Argument.game_id == game_id, Argument.status == "adjudicating"
            )
        ).first()
        assert arg is not None
        return arg.id


def test_unknown_supersession_target_rejected(db_engine: Engine) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="unknown target",
        success_effects=["new"],
        success_supersedes_effect_ids=[uuid.uuid4()],
    )
    with pytest.raises(game_service.InvalidSupersession):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))
    assert _record(db_engine, argument_id) is None


def test_duplicate_supersession_ids_rejected(db_engine: Engine) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    old_id = _add_effect(db_engine, game_id, "Old.")
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="dupes",
        success_effects=["new"],
        success_supersedes_effect_ids=[old_id, old_id],
    )
    with pytest.raises(game_service.InvalidSupersession):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))
    assert _record(db_engine, argument_id) is None


def test_i068_oversized_effect_branch_is_rejected_without_partial_write(
    db_engine: Engine,
) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="oversized Effect branch",
        success_effects=[
            f"Effect {index}" for index in range(MAX_EFFECTS_PER_BRANCH + 1)
        ],
    )

    with pytest.raises(game_service.InvalidAdjudication, match="Effect branch is limited"):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    assert _record(db_engine, argument_id) is None
    assert _effects(db_engine, game_id) == []
    assert _game_state(db_engine, game_id)[1] == 0


def test_i068_oversized_supersession_branch_is_rejected_without_partial_write(
    db_engine: Engine,
) -> None:
    game_id, argument_id = _adjudicating_game(db_engine)
    existing = [
        _add_effect(db_engine, game_id, f"Existing effect {index}")
        for index in range(MAX_EFFECTS_PER_BRANCH + 1)
    ]
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="oversized supersession branch",
        success_effects=["Replacement"],
        success_supersedes_effect_ids=existing,
    )

    with pytest.raises(game_service.InvalidAdjudication, match="supersession branch is limited"):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))

    assert _record(db_engine, argument_id) is None
    effects = _effects(db_engine, game_id)
    assert len(effects) == MAX_EFFECTS_PER_BRANCH + 1
    assert all(effect.active for effect in effects)
    assert _game_state(db_engine, game_id)[1] == 0


def test_superseding_inactive_effect_rejected(db_engine: Engine) -> None:
    """D-011: an already-inactive Effect is not eligible to be superseded again."""
    game_id, argument_id = _adjudicating_game(db_engine)
    old_id = _add_effect(db_engine, game_id, "Already gone.")
    with session_scope(db_engine) as session:
        effect = session.get(Effect, old_id)
        effect.active = False
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="stale supersession",
        success_effects=["new"],
        success_supersedes_effect_ids=[old_id],
    )
    with pytest.raises(game_service.InvalidSupersession):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(proposal))
    assert _record(db_engine, argument_id) is None


# --- Player freeze + integration -------------------------------------------------------


def test_player_mutation_frozen_during_adjudication(db_engine: Engine) -> None:
    """state-machine §19/§23: a Con after adjudication starts must be rejected."""
    game_id, argument_id = _adjudicating_game(db_engine)
    with pytest.raises(game_service.InvalidPhase):
        game_service.make_con(
            db_engine, game_id, argument_id=argument_id, actor_id="belvar", reason="late"
        )


def test_full_resolution_pipeline_survives_restart(db_path) -> None:
    """Integration: cons collected → probabilistic resolve → supersession, reloaded fresh."""
    engine = database.engine_for_path(db_path)
    database.create_db_and_tables(engine)
    game_id = _seed_running_argument_game(engine)
    old_id = _add_effect(engine, game_id, "Prior tension.")
    argument_id = _drive_to_adjudication(engine, game_id)

    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic,
        probability=1.0,  # deterministic success (roll < 1.0)
        rationale="likely",
        success_effects=["Frontier reinforced."],
        failure_effects=["Nothing happens."],
        success_supersedes_effect_ids=[old_id],
    )
    outcome = game_service.resolve_adjudication(engine, game_id, FakeUmpire(proposal))
    assert outcome.success is True
    engine.dispose()

    # Reopen on the same file — a simulated process restart.
    reopened = database.engine_for_path(db_path)
    try:
        record = _record(reopened, argument_id)
        assert record is not None and record.success is True
        assert record.roll is not None
        by_desc = {e.description: e for e in _effects(reopened, game_id)}
        assert by_desc["Frontier reinforced."].active is True
        assert "Nothing happens." not in by_desc
        assert by_desc["Prior tension."].active is False
        phase, draw_index = _game_state(reopened, game_id)
        assert phase is GamePhase.adjudication  # advancement is T-021
        assert draw_index == 1  # RNG progress persisted; restart continues, not replays
        assert _argument_status(reopened, argument_id) == "resolved"
    finally:
        reopened.dispose()
