"""Domain model round-trip tests (T-004/005/006, WBS 2.1.1–2.1.9).

Every entity from TRD §7.1–7.9 must instantiate with its documented fields and survive a
write/read round-trip through SQLite on the T-003 ``db_engine`` fixture. This layer proves
*shape*, not legality — correct-actor/phase/budget rules are the engine phases (T-014–T-016)
and are explicitly out of scope here.

TRD §7.1 permits the database representation to differ from the Pydantic representation.
UUID lists are stored as JSON strings but restored as UUID objects at the model boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app.models import (
    Actor,
    Argument,
    Con,
    Effect,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
    Message,
    Note,
    Resource,
    Seat,
)


@pytest.fixture
def session(db_engine: Engine) -> Session:
    """A session on the temp DB with every table created (T-007 owns the real engine)."""
    SQLModel.metadata.create_all(db_engine)
    with Session(db_engine) as s:
        yield s


def _roundtrip(session: Session, obj):
    """Persist ``obj``, expunge it from the identity map, reload a fresh instance by PK."""
    session.add(obj)
    session.commit()
    identity = inspect(obj).identity
    assert identity is not None
    pk = identity[0] if len(identity) == 1 else identity
    model = type(obj)
    session.expunge_all()
    return session.get(model, pk)


def _rejects_incomplete(session: Session, obj) -> None:
    """A required (non-null) field is missing, so persisting the row must be rejected.

    SQLModel ``table=True`` models skip Pydantic validation, so required-field *shape* is
    proven at the persistence boundary: the field is a ``NOT NULL`` column and the insert
    fails. Rolls back so the session stays usable.
    """
    session.add(obj)
    try:
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()


def _parents(session: Session) -> uuid.UUID:
    """Seed one Game with Actors A/B/C so child rows have real parents.

    I-082: the app engine enforces the declared foreign keys, so a round-trip of a child row
    needs the parent rows the schema names — the shape under test is unchanged.
    """
    game = Game(scenario_id="scn", max_turns=6, seed=1)
    session.add(game)
    session.commit()
    for actor_id in ("A", "B", "C"):
        session.add(
            Actor(
                game_id=game.id,
                id=actor_id,
                name=actor_id,
                public_description="public",
                private_brief="private",
            )
        )
    session.commit()
    return game.id


def test_game_roundtrips_with_documented_fields(session: Session) -> None:
    game = Game(
        scenario_id="scn-1",
        turn=1,
        max_turns=6,
        phase=GamePhase.diplomacy,
        status=GameStatus.running,
        turn_order=["A", "B", "C"],
        active_actor_id="A",
        seed=42,
    )
    loaded = _roundtrip(session, game)

    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.scenario_id == "scn-1"
    assert loaded.turn == 1
    assert loaded.max_turns == 6
    assert loaded.phase is GamePhase.diplomacy
    assert loaded.status is GameStatus.running
    assert loaded.turn_order == ["A", "B", "C"]  # list[str] JSON survives
    assert loaded.active_actor_id == "A"
    assert loaded.seed == 42


def test_game_active_actor_may_be_none(session: Session) -> None:
    game = Game(scenario_id="s", max_turns=6, seed=1, active_actor_id=None)
    loaded = _roundtrip(session, game)
    assert loaded.active_actor_id is None


def test_actor_roundtrips_including_json_and_optional_fields(session: Session) -> None:
    game_id = _parents(session)
    actor = Actor(
        game_id=game_id,
        id="D",
        name="Alice",
        public_description="a public face",
        private_brief="a secret brief",
        objectives=["win", "survive"],
        private_information=["knows X", "hides Y"],
        organisation_id="org-1",
        role="leader",
        actor_metadata={"weight": 3, "tags": ["red"]},  # D-007: `metadata` is ORM-reserved
    )
    loaded = _roundtrip(session, actor)

    assert loaded.id == "D"
    assert loaded.objectives == ["win", "survive"]
    assert loaded.private_information == ["knows X", "hides Y"]
    assert loaded.organisation_id == "org-1"
    assert loaded.role == "leader"
    assert loaded.actor_metadata == {"weight": 3, "tags": ["red"]}


def test_actor_optional_metadata_defaults(session: Session) -> None:
    game_id = _parents(session)
    actor = Actor(
        game_id=game_id,
        id="D",
        name="Bob",
        public_description="p",
        private_brief="b",
    )
    loaded = _roundtrip(session, actor)
    assert loaded.objectives == []
    assert loaded.private_information == []
    assert loaded.organisation_id is None
    assert loaded.role is None
    assert loaded.actor_metadata == {}


def test_resource_roundtrips_as_generic_game_scoped_byte_snapshot(session: Session) -> None:
    game_id = _parents(session)
    resource = Resource(
        game_id=game_id,
        id="source-1",
        name="Source One",
        type="scenario-defined-value",
        visibility="private",
        visible_to=["A"],
        tags=["one", "two"],
        resource_metadata={"nested": {"score": 3}},
        source_kind="file",
        source_bytes=b"\x00exact\r\nbytes",
        attachment_name="facts.bin",
        content_sha256="a" * 64,
        representation_kind="text",
        media_type="text/plain; charset=utf-8",
        text_content="exact\r\nbytes",
        size_bytes=13,
    )
    loaded = _roundtrip(session, resource)
    assert loaded.id == "source-1"
    assert loaded.type == "scenario-defined-value"
    assert loaded.visible_to == ["A"]
    assert loaded.resource_metadata == {"nested": {"score": 3}}
    assert loaded.source_bytes == b"\x00exact\r\nbytes"
    assert loaded.attachment_name == "facts.bin"
    assert loaded.content_sha256 == "a" * 64
    assert loaded.representation_kind == "text"
    assert loaded.media_type == "text/plain; charset=utf-8"
    assert loaded.text_content == "exact\r\nbytes"
    assert loaded.size_bytes == 13


def test_seat_persists_only_token_hash(session: Session) -> None:
    """AGENTS.md §6 / INV-ACTOR-001: the raw bearer token is never stored."""
    game_id = _parents(session)
    seat = Seat(game_id=game_id, actor_id="A", token_hash="sha256:deadbeef")
    loaded = _roundtrip(session, seat)

    assert loaded.token_hash == "sha256:deadbeef"
    columns = {c["name"] for c in inspect(session.get_bind()).get_columns("seat")}
    assert columns == {"id", "game_id", "actor_id", "token_hash"}
    assert not any("token" in c and c != "token_hash" for c in columns)


def test_message_roundtrips_and_carries_public_flag(session: Session) -> None:
    """TRD §7.4 / INV-MSG-001..003 shape (immutability enforcement is T-014)."""
    game_id = _parents(session)
    msg = Message(
        game_id=game_id,
        turn=2,
        sender_actor_id="A",
        recipients=["B", "C"],
        text="hello",
        public=False,
    )
    loaded = _roundtrip(session, msg)

    assert loaded.recipients == ["B", "C"]  # list[str] JSON survives
    assert loaded.text == "hello"
    assert loaded.public is False
    assert isinstance(loaded.created_at, datetime)


def test_message_requires_its_core_fields(session: Session) -> None:
    # sender_actor_id and text are required (NOT NULL); an insert without them is rejected.
    _rejects_incomplete(
        session, Message(game_id=uuid.uuid4(), turn=1, recipients=[], public=True)
    )


def test_argument_roundtrips_with_pros_list(session: Session) -> None:
    game_id = _parents(session)
    arg = Argument(
        game_id=game_id,
        turn=1,
        actor_id="A",
        action="invade",
        desired_effect="gain territory",
        pros=["strong army", "weak neighbour"],
        status="submitted",
    )
    loaded = _roundtrip(session, arg)

    assert loaded.action == "invade"
    assert loaded.desired_effect == "gain territory"
    assert loaded.pros == ["strong army", "weak neighbour"]
    assert loaded.status == "submitted"


def test_argument_requires_action_and_desired_effect(session: Session) -> None:
    # INV-ARG-004 shape: action + desired_effect are NOT NULL; a bare Argument is rejected.
    _rejects_incomplete(session, Argument(game_id=uuid.uuid4(), turn=1, actor_id="A"))


def test_con_roundtrips_and_requires_reason(session: Session) -> None:
    game_id = _parents(session)
    arg = Argument(
        game_id=game_id,
        turn=1,
        actor_id="A",
        action="a",
        desired_effect="e",
        pros=["p"],
        status="submitted",
    )
    session.add(arg)
    session.commit()
    arg_id = arg.id  # capture before expunge so it survives the round-trip below

    con = Con(argument_id=arg_id, actor_id="B", reason="bad idea")
    loaded = _roundtrip(session, con)
    assert loaded.argument_id == arg_id
    assert loaded.actor_id == "B"
    assert loaded.reason == "bad idea"

    # reason is required (NOT NULL): a Con without it is rejected at persistence.
    _rejects_incomplete(session, Con(argument_id=arg_id, actor_id="B"))


def test_effect_roundtrips_with_visibility_and_defaults(session: Session) -> None:
    game_id = _parents(session)
    source = Argument(
        game_id=game_id, turn=1, actor_id="A", action="a", desired_effect="e", pros=["p"]
    )
    session.add(source)
    session.commit()
    eff = Effect(
        game_id=game_id,
        description="border shifted",
        visibility="private",
        visible_to=["A", "B"],
        source_argument_id=source.id,
        created_turn=1,
        expires_turn=3,
        active=True,
    )
    loaded = _roundtrip(session, eff)

    assert loaded.description == "border shifted"
    assert loaded.visibility == "private"
    assert loaded.visible_to == ["A", "B"]
    assert isinstance(loaded.source_argument_id, uuid.UUID)
    assert loaded.created_turn == 1
    assert loaded.expires_turn == 3
    assert loaded.active is True
    assert loaded.supersedes == []  # default empty


def test_effect_supersedes_references_another_effect(session: Session) -> None:
    """TRD §7.7 / INV-EFFECT-004: supersession points at prior Effect ids; nothing is deleted."""
    game_id = _parents(session)
    old = Effect(
        game_id=game_id,
        description="old",
        visibility="public",
        visible_to=[],
        created_turn=1,
    )
    session.add(old)
    session.commit()
    old_id = old.id

    new = Effect(
        game_id=old.game_id,
        description="new",
        visibility="public",
        visible_to=[],
        created_turn=2,
        supersedes=[old_id],
    )
    loaded = _roundtrip(session, new)

    assert loaded.supersedes == [old_id]
    assert isinstance(loaded.supersedes[0], uuid.UUID)
    # The superseded Effect is still present in history (not deleted).
    assert session.get(Effect, old_id) is not None


def test_effect_optional_fields_default(session: Session) -> None:
    game_id = _parents(session)
    eff = Effect(
        game_id=game_id,
        description="d",
        visibility="public",
        visible_to=[],
        created_turn=1,
    )
    loaded = _roundtrip(session, eff)
    assert loaded.source_argument_id is None
    assert loaded.expires_turn is None
    assert loaded.active is True


def test_game_event_has_int_id_and_json_payload(session: Session) -> None:
    ev1 = GameEvent(game_id=uuid.uuid4(), turn=1, type="game_started", payload={"seed": 42})
    ev2 = GameEvent(
        game_id=ev1.game_id, turn=1, type="phase_started", actor_id=None, payload={}
    )
    session.add(ev1)
    session.add(ev2)
    session.commit()

    ids = [ev1.id, ev2.id]
    assert all(isinstance(i, int) for i in ids)
    assert ids[1] > ids[0]  # monotonic integer ids

    reloaded = session.exec(select(GameEvent).order_by(GameEvent.id)).all()
    assert reloaded[0].type == "game_started"
    assert reloaded[0].payload == {"seed": 42}
    assert reloaded[1].actor_id is None


def test_note_roundtrips(session: Session) -> None:
    game_id = _parents(session)
    note = Note(game_id=game_id, actor_id="A", text="remember to betray B")
    loaded = _roundtrip(session, note)
    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.actor_id == "A"
    assert loaded.text == "remember to betray B"
    assert isinstance(loaded.created_at, datetime)
