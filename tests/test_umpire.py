"""T-018 acceptance tests: the framework-neutral Umpire boundary and the FakeUmpire.

Proves the boundary contract (agents.md §21/§22, TRD §22): a structured proposal with all
three modes, success/failure Effect proposals, supersession proposals, that the FakeUmpire
performs **no** RNG (INV-RNG-001, §26 "the Umpire does not perform or fabricate the roll"),
and that resolving with it needs no agent framework or API key.
"""

from __future__ import annotations

import uuid

from app.models import AdjudicationMode
from app.umpire import AdjudicationContext, AdjudicationProposal, Umpire
from agents.umpire import FakeUmpire

_CTX = AdjudicationContext(
    scenario_summary="test",
    turn=1,
    turn_length=None,
    argument={"actor_id": "arkania", "action": "a", "desired_effect": "d", "pros": ["p"]},
    cons=[],
    relevant_effects=[],
    relevant_hidden_information=[],
)


def test_fakeumpire_satisfies_the_umpire_protocol() -> None:
    """FakeUmpire is a structural :class:`Umpire` and its output is an AdjudicationProposal."""
    umpire: Umpire = FakeUmpire(
        AdjudicationProposal(mode=AdjudicationMode.automatic_success, rationale="r")
    )
    result = umpire.adjudicate(_CTX)
    assert isinstance(result, AdjudicationProposal)


def test_fakeumpire_deterministic_automatic_success() -> None:
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="clear",
        success_effects=["It works."],
        failure_effects=["It fails."],
    )
    umpire = FakeUmpire(proposal)
    assert umpire.adjudicate(_CTX) == proposal
    assert umpire.adjudicate(_CTX) == proposal  # deterministic


def test_fakeumpire_deterministic_automatic_failure() -> None:
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_failure, rationale="impossible"
    )
    assert FakeUmpire(proposal).adjudicate(_CTX).mode is AdjudicationMode.automatic_failure


def test_fakeumpire_probabilistic_returns_probability_but_no_roll() -> None:
    """A probabilistic proposal carries a probability and never an authoritative roll."""
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.probabilistic, probability=0.7, rationale="contested"
    )
    out = FakeUmpire(proposal).adjudicate(_CTX)
    assert out.probability == 0.7
    # The proposal type has no field through which the Umpire could inject a roll.
    assert not hasattr(out, "roll")


def test_fakeumpire_supports_supersession_proposals() -> None:
    old = uuid.uuid4()
    proposal = AdjudicationProposal(
        mode=AdjudicationMode.automatic_success,
        rationale="supersede",
        success_effects=["new"],
        success_supersedes_effect_ids=[old],
    )
    assert FakeUmpire(proposal).adjudicate(_CTX).success_supersedes_effect_ids == [old]


def test_fakeumpire_can_respond_per_context() -> None:
    """A callable responder lets one FakeUmpire drive different Arguments deterministically."""
    def responder(ctx: AdjudicationContext) -> AdjudicationProposal:
        mode = (
            AdjudicationMode.automatic_success
            if ctx.argument["actor_id"] == "arkania"
            else AdjudicationMode.automatic_failure
        )
        return AdjudicationProposal(mode=mode, rationale="by actor")

    umpire = FakeUmpire(responder=responder)
    assert umpire.adjudicate(_CTX).mode is AdjudicationMode.automatic_success
