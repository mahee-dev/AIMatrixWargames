"""Visibility service (T-012, WBS 2.5).

The single authoritative answer to "what may this Actor see in this Game?" — a security
boundary, not presentation (TRD §36). These tests assert both what IS returned and, for every
hidden record, that its sensitive values are ABSENT (not merely that a count is right):

- Actor private state isolation — own brief/objectives/private_information visible, another
  Actor's never (INV-ACTOR-002);
- Message visibility — public to all, private to sender + recipients only, no indirect leak
  (INV-MSG-002/003, TEST-VIS-001..003);
- Effect visibility — public to all, private/restricted only to authorised Actors, decided
  from `visibility`/`visible_to` alone, hidden metadata not leaked (INV-EFFECT-005);
- Note isolation — own Notes only (INV-NOTE-001, TEST-NOTE-001/002);
- cross-game isolation — identical Actor IDs in two games never bleed (TRD §7.2);
- fail-closed — an Actor not belonging to the Game raises, never an empty-but-successful view.

Distinctive sentinel strings make accidental leakage obvious. Each test runs on its own
isolated database (the ``db_engine`` fixture); the suite is order-independent and key-free.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine

from app import game_service, persistence, visibility
from app.database import session_scope
from app.models import Actor, Effect, Game, GameEvent, Message, Note, Resource
from tests.api_helpers import seed, start_to_diplomacy

# Sentinels — if any of these reaches an unauthorised Actor, a visibility test must fail.
BRIEF = {
    "arkania": "SECRET-ARKANIA-BRIEF-DO-NOT-LEAK",
    "belvar": "SECRET-BELVAR-BRIEF-DO-NOT-LEAK",
    "cestia": "SECRET-CESTIA-BRIEF-DO-NOT-LEAK",
}
OBJECTIVE = {a: f"SECRET-{a.upper()}-OBJECTIVE" for a in BRIEF}
PRIV_INFO = {a: f"SECRET-{a.upper()}-PRIVINFO" for a in BRIEF}


def _make_game(**overrides) -> Game:
    fields = dict(
        scenario_id="scn",
        max_turns=6,
        seed=1234,
        turn_order=["arkania", "belvar", "cestia"],
    )
    fields.update(overrides)
    return Game(**fields)


def _make_actor(game_id: uuid.UUID, actor_id: str) -> Actor:
    return Actor(
        game_id=game_id,
        id=actor_id,
        name=f"Actor {actor_id}",
        public_description=f"{actor_id} is public knowledge",
        private_brief=BRIEF[actor_id],
        objectives=[OBJECTIVE[actor_id]],
        private_information=[PRIV_INFO[actor_id]],
    )


def _seed_three_actor_game(session) -> Game:
    game = persistence.create(session, _make_game())
    for actor_id in ("arkania", "belvar", "cestia"):
        persistence.create(session, _make_actor(game.id, actor_id))
    return game


# --- Messages: TEST-VIS-001..003 --------------------------------------------------------


def test_vis_001_public_message_visible_to_all(db_engine: Engine) -> None:
    """A public Message from A is readable by A, B and C (INV-MSG-002)."""
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        persistence.create(
            session,
            Message(
                game_id=game.id,
                turn=1,
                sender_actor_id="arkania",
                recipients=[],
                text="PUBLIC-ANNOUNCEMENT",
                public=True,
            ),
        )
        gid = game.id

    with session_scope(db_engine) as session:
        for actor_id in ("arkania", "belvar", "cestia"):
            texts = [m.text for m in visibility.visible_messages(session, gid, actor_id)]
            assert "PUBLIC-ANNOUNCEMENT" in texts


def test_vis_002_private_message_visible_to_sender_and_recipient(db_engine: Engine) -> None:
    """A private A→B Message is seen by A and B, and not by C (INV-MSG-003)."""
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        persistence.create(
            session,
            Message(
                game_id=game.id,
                turn=1,
                sender_actor_id="arkania",
                recipients=["belvar"],
                text="PRIVATE-ARKANIA-TO-BELVAR",
                public=False,
            ),
        )
        gid = game.id

    with session_scope(db_engine) as session:
        for actor_id in ("arkania", "belvar"):
            texts = [m.text for m in visibility.visible_messages(session, gid, actor_id)]
            assert "PRIVATE-ARKANIA-TO-BELVAR" in texts

        cestia_texts = [m.text for m in visibility.visible_messages(session, gid, "cestia")]
        assert "PRIVATE-ARKANIA-TO-BELVAR" not in cestia_texts


def test_vis_003_private_message_does_not_leak_indirectly(db_engine: Engine) -> None:
    """C's view exposes neither the private Message's text, recipient list nor its existence."""
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        persistence.create(
            session,
            Message(
                game_id=game.id,
                turn=1,
                sender_actor_id="arkania",
                recipients=["belvar"],
                text="PRIVATE-ARKANIA-TO-BELVAR",
                public=False,
            ),
        )
        gid = game.id

    with session_scope(db_engine) as session:
        cestia_messages = visibility.visible_messages(session, gid, "cestia")
        # Count does not reveal the hidden Message.
        assert cestia_messages == []
        # No field of any returned row leaks the text or the recipient list.
        for m in cestia_messages:
            assert "PRIVATE-ARKANIA-TO-BELVAR" not in m.text
            assert "belvar" not in m.recipients


def test_private_message_multiple_recipients(db_engine: Engine) -> None:
    """A→[B,C] private Message is seen by A, B and C but not a fourth, non-recipient Actor."""
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        # add a fourth actor as a non-recipient
        persistence.create(
            session,
            Actor(
                game_id=game.id,
                id="doria",
                name="Actor doria",
                public_description="doria public",
                private_brief="SECRET-DORIA-BRIEF-DO-NOT-LEAK",
            ),
        )
        persistence.create(
            session,
            Message(
                game_id=game.id,
                turn=1,
                sender_actor_id="arkania",
                recipients=["belvar", "cestia"],
                text="PRIVATE-TO-TWO",
                public=False,
            ),
        )
        gid = game.id

    with session_scope(db_engine) as session:
        for actor_id in ("arkania", "belvar", "cestia"):
            texts = [m.text for m in visibility.visible_messages(session, gid, actor_id)]
            assert "PRIVATE-TO-TWO" in texts
        doria_texts = [m.text for m in visibility.visible_messages(session, gid, "doria")]
        assert "PRIVATE-TO-TWO" not in doria_texts


def test_can_view_message_predicate(db_engine: Engine) -> None:
    """The reusable predicate agrees with the list filter for sender/recipient/public."""
    public_msg = Message(
        game_id=uuid.uuid4(),
        turn=1,
        sender_actor_id="arkania",
        recipients=[],
        text="x",
        public=True,
    )
    private_msg = Message(
        game_id=uuid.uuid4(),
        turn=1,
        sender_actor_id="arkania",
        recipients=["belvar"],
        text="x",
        public=False,
    )
    assert visibility.can_view_message("cestia", public_msg) is True
    assert visibility.can_view_message("arkania", private_msg) is True  # sender
    assert visibility.can_view_message("belvar", private_msg) is True  # recipient
    assert visibility.can_view_message("cestia", private_msg) is False


# --- Notes: TEST-NOTE-001/002 -----------------------------------------------------------


def test_note_001_note_visible_to_owner(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        persistence.create(
            session, Note(game_id=game.id, actor_id="arkania", text="SECRET-ARKANIA-NOTE")
        )
        gid = game.id

    with session_scope(db_engine) as session:
        texts = [n.text for n in visibility.visible_notes(session, gid, "arkania")]
        assert texts == ["SECRET-ARKANIA-NOTE"]


def test_note_002_note_invisible_to_others(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        persistence.create(
            session, Note(game_id=game.id, actor_id="arkania", text="SECRET-ARKANIA-NOTE")
        )
        gid = game.id

    with session_scope(db_engine) as session:
        for actor_id in ("belvar", "cestia"):
            notes = visibility.visible_notes(session, gid, actor_id)
            assert notes == []
            for n in notes:
                assert "SECRET-ARKANIA-NOTE" not in n.text


def test_each_actor_sees_only_its_own_notes(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        for actor_id in ("arkania", "belvar", "cestia"):
            persistence.create(
                session,
                Note(game_id=game.id, actor_id=actor_id, text=f"NOTE-OF-{actor_id.upper()}"),
            )
        gid = game.id

    with session_scope(db_engine) as session:
        for actor_id in ("arkania", "belvar", "cestia"):
            texts = [n.text for n in visibility.visible_notes(session, gid, actor_id)]
            assert texts == [f"NOTE-OF-{actor_id.upper()}"]


# --- Actor private state (INV-ACTOR-002) ------------------------------------------------


def test_actor_sees_own_private_state(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        gid = game.id

    with session_scope(db_engine) as session:
        view = visibility.get_actor_view(session, gid, "belvar")
        assert view.private_brief == BRIEF["belvar"]
        assert OBJECTIVE["belvar"] in view.objectives
        assert PRIV_INFO["belvar"] in view.private_information


def test_actor_cannot_see_others_private_state(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        gid = game.id

    with session_scope(db_engine) as session:
        view = visibility.get_actor_view(session, gid, "arkania")
        # Others appear as PUBLIC projections only — no private field anywhere in the view.
        others = {o.id: o for o in view.others}
        assert set(others) == {"belvar", "cestia"}
        blob = repr(view)
        for other in ("belvar", "cestia"):
            assert BRIEF[other] not in blob
            assert OBJECTIVE[other] not in blob
            assert PRIV_INFO[other] not in blob
            assert others[other].public_description == f"{other} is public knowledge"


# --- Effects (INV-EFFECT-005) -----------------------------------------------------------


def _make_effect(game_id: uuid.UUID, *, visibility_: str, visible_to, description: str) -> Effect:
    return Effect(
        game_id=game_id,
        description=description,
        visibility=visibility_,
        visible_to=list(visible_to),
        created_turn=0,
    )


def test_public_effect_visible_to_all(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        persistence.create(
            session,
            _make_effect(game.id, visibility_="public", visible_to=[], description="PUBLIC-EFFECT"),
        )
        gid = game.id

    with session_scope(db_engine) as session:
        for actor_id in ("arkania", "belvar", "cestia"):
            descs = [e.description for e in visibility.visible_effects(session, gid, actor_id)]
            assert "PUBLIC-EFFECT" in descs


def test_private_effect_visible_only_to_authorised(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        persistence.create(
            session,
            _make_effect(
                game.id,
                visibility_="private",
                visible_to=["arkania"],
                description="SECRET-HIDDEN-EFFECT",
            ),
        )
        gid = game.id

    with session_scope(db_engine) as session:
        ark = [e.description for e in visibility.visible_effects(session, gid, "arkania")]
        assert "SECRET-HIDDEN-EFFECT" in ark
        for actor_id in ("belvar", "cestia"):
            effects = visibility.visible_effects(session, gid, actor_id)
            assert effects == []
            for e in effects:
                assert "SECRET-HIDDEN-EFFECT" not in e.description


def test_can_view_effect_predicate() -> None:
    gid = uuid.uuid4()
    public = _make_effect(gid, visibility_="public", visible_to=[], description="x")
    private = _make_effect(gid, visibility_="private", visible_to=["arkania"], description="x")
    assert visibility.can_view_effect("cestia", public) is True
    assert visibility.can_view_effect("arkania", private) is True
    assert visibility.can_view_effect("belvar", private) is False


# --- Cross-game isolation (TRD §7.2 — Actor IDs are game-local) --------------------------


def _seed_two_games_sharing_ids(session) -> tuple[uuid.UUID, uuid.UUID]:
    game_a = _seed_three_actor_game(session)
    game_b = persistence.create(session, _make_game())
    for actor_id in ("arkania", "belvar", "cestia"):
        actor = _make_actor(game_b.id, actor_id)
        # distinct brief per game so a leak is unambiguous
        actor.private_brief = f"SECRET-GAMEB-{actor_id.upper()}-BRIEF"
        persistence.create(session, actor)
    return game_a.id, game_b.id


def test_cross_game_message_isolation(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        gid_a, gid_b = _seed_two_games_sharing_ids(session)
        persistence.create(
            session,
            Message(
                game_id=gid_b,
                turn=1,
                sender_actor_id="belvar",
                recipients=["arkania"],
                text="SECRET-CROSS-GAME-MESSAGE",
                public=False,
            ),
        )

    with session_scope(db_engine) as session:
        texts = [m.text for m in visibility.visible_messages(session, gid_a, "arkania")]
        assert "SECRET-CROSS-GAME-MESSAGE" not in texts


def test_cross_game_note_isolation(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        gid_a, gid_b = _seed_two_games_sharing_ids(session)
        persistence.create(
            session, Note(game_id=gid_b, actor_id="arkania", text="SECRET-CROSS-GAME-NOTE")
        )

    with session_scope(db_engine) as session:
        texts = [n.text for n in visibility.visible_notes(session, gid_a, "arkania")]
        assert "SECRET-CROSS-GAME-NOTE" not in texts


def test_cross_game_effect_isolation(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        gid_a, gid_b = _seed_two_games_sharing_ids(session)
        persistence.create(
            session,
            _make_effect(
                gid_b,
                visibility_="private",
                visible_to=["arkania"],
                description="SECRET-CROSS-GAME-EFFECT",
            ),
        )

    with session_scope(db_engine) as session:
        descs = [e.description for e in visibility.visible_effects(session, gid_a, "arkania")]
        assert "SECRET-CROSS-GAME-EFFECT" not in descs


def test_cross_game_actor_private_isolation(db_engine: Engine) -> None:
    """Game A's arkania view carries Game A's brief, never Game B's, for the same Actor ID."""
    with session_scope(db_engine) as session:
        gid_a, gid_b = _seed_two_games_sharing_ids(session)

    with session_scope(db_engine) as session:
        view = visibility.get_actor_view(session, gid_a, "arkania")
        assert view.private_brief == BRIEF["arkania"]
        assert "SECRET-GAMEB-ARKANIA-BRIEF" not in repr(view)


# --- Fail-closed on invalid identity ----------------------------------------------------


def test_unknown_actor_fails_closed(db_engine: Engine) -> None:
    """An Actor not in the Game raises, never an empty-but-successful view (would hide a defect)."""
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        gid = game.id

    with session_scope(db_engine) as session:
        with pytest.raises(visibility.UnknownActor):
            visibility.get_actor_view(session, gid, "nobody")
        with pytest.raises(visibility.UnknownActor):
            visibility.visible_messages(session, gid, "nobody")
        with pytest.raises(visibility.UnknownActor):
            visibility.visible_effects(session, gid, "nobody")
        with pytest.raises(visibility.UnknownActor):
            visibility.visible_notes(session, gid, "nobody")


def test_unknown_game_fails_closed(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        with pytest.raises(visibility.UnknownActor):
            visibility.get_actor_view(session, uuid.uuid4(), "arkania")


# --- Resources (INV-RES-002/003) -------------------------------------------------------


def _make_resource(
    game_id: uuid.UUID,
    *,
    resource_id: str,
    visibility_: str = "public",
    visible_to: list[str] | None = None,
) -> Resource:
    content = f"content for {resource_id}"
    return Resource(
        game_id=game_id,
        id=resource_id,
        name=resource_id,
        type="opaque-scenario-type",
        visibility=visibility_,
        visible_to=visible_to or [],
        tags=["tag"],
        resource_metadata={},
        source_kind="inline",
        source_bytes=content.encode(),
        attachment_name=None,
        content_sha256="0" * 64,
        representation_kind="text",
        media_type="text/plain; charset=utf-8",
        text_content=content,
        size_bytes=len(content),
    )


def test_resource_predicate_and_visible_collection_share_one_policy(db_engine: Engine) -> None:
    with session_scope(db_engine) as session:
        game = _seed_three_actor_game(session)
        public = persistence.create(session, _make_resource(game.id, resource_id="public"))
        private = persistence.create(
            session,
            _make_resource(
                game.id,
                resource_id="private",
                visibility_="private",
                visible_to=["arkania"],
            ),
        )
        assert visibility.can_view_resource("belvar", public) is True
        assert visibility.can_view_resource("arkania", private) is True
        assert visibility.can_view_resource("belvar", private) is False
        gid = game.id

    with session_scope(db_engine) as session:
        assert [r.id for r in visibility.visible_resources(session, gid, "arkania")] == [
            "private",
            "public",
        ]
        assert [r.id for r in visibility.visible_resources(session, gid, "belvar")] == ["public"]
        assert visibility.visible_resource(session, gid, "arkania", "private").id == "private"
        assert visibility.visible_resource(session, gid, "belvar", "private") is None
        assert visibility.visible_resource(session, gid, "belvar", "missing") is None


def test_resource_visibility_is_game_scoped_and_unknown_actor_fails_closed(
    db_engine: Engine,
) -> None:
    with session_scope(db_engine) as session:
        gid_a, gid_b = _seed_two_games_sharing_ids(session)
        persistence.create(session, _make_resource(gid_a, resource_id="same"))
        persistence.create(session, _make_resource(gid_b, resource_id="same"))

    with session_scope(db_engine) as session:
        assert visibility.visible_resource(session, gid_a, "arkania", "same").game_id == gid_a
        with pytest.raises(visibility.UnknownActor):
            visibility.visible_resources(session, gid_a, "nobody")
        with pytest.raises(visibility.UnknownActor):
            visibility.visible_resource(session, gid_a, "nobody", "same")


# --- unknown events fail closed; reads are chronological ----------------------------------


def test_i010_unknown_and_broken_events_fail_closed(db_engine: Engine) -> None:
    creation = seed(db_engine)
    with session_scope(db_engine) as session:
        unknown = GameEvent(game_id=creation.game_id, turn=0, type="future_private", payload={})
        broken = GameEvent(
            game_id=creation.game_id,
            turn=0,
            type="message_sent",
            payload={"message_id": "not-a-uuid"},
        )
        assert visibility.can_view_event(session, "arkania", unknown) is False
        assert visibility.can_view_event(session, "arkania", broken) is False


def test_i014_visible_messages_and_notes_are_chronological(db_engine: Engine) -> None:
    """I-014: read boundaries order by (created_at, id) rather than relying on row order."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    for i in range(3):
        game_service.send_message(
            db_engine, creation.game_id, sender_actor_id="arkania",
            text=f"m{i}", public=True, recipients=[],
        )
        game_service.save_note(db_engine, creation.game_id, actor_id="arkania", text=f"n{i}")
    with session_scope(db_engine) as session:
        messages = [
            (m.text, m.created_at)
            for m in visibility.visible_messages(session, creation.game_id, "arkania")
        ]
        notes = [n.text for n in visibility.visible_notes(session, creation.game_id, "arkania")]
    assert [text for text, _ in messages] == ["m0", "m1", "m2"]
    assert notes == ["n0", "n1", "n2"]
    assert [ts for _, ts in messages] == sorted(ts for _, ts in messages)
