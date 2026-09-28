"""T-047/T-048 visibility-first Resource search/read acceptance."""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from sqlalchemy import Engine, inspect

from app import persistence
from app.database import session_scope
from app.models import Game, GamePhase, GameStatus, Resource
from tests.api_helpers import ACTORS, bearer, client, seed, start_to_diplomacy


def _resource(
    game_id: uuid.UUID,
    resource_id: str,
    *,
    name: str,
    tags: list[str],
    content: str | None = None,
    visibility: str = "public",
    visible_to: list[str] | None = None,
    metadata: dict | None = None,
    attachment_name: str | None = None,
) -> Resource:
    is_text = content is not None
    raw = content.encode() if is_text else b"\x89PNG\r\n\x1a\nopaque"
    return Resource(
        game_id=game_id,
        id=resource_id,
        name=name,
        type="open-type",
        visibility=visibility,
        visible_to=visible_to or [],
        tags=tags,
        resource_metadata=metadata or {},
        source_kind="inline" if is_text else "file",
        source_bytes=raw,
        attachment_name=None if is_text else (attachment_name or "map.png"),
        content_sha256=hashlib.sha256(raw).hexdigest(),
        representation_kind="text" if is_text else "attachment",
        media_type="text/plain; charset=utf-8" if is_text else "image/png",
        text_content=content,
        size_bytes=len(raw),
    )


def _insert(engine: Engine, *rows: Resource) -> None:
    with session_scope(engine) as session:
        for row in rows:
            persistence.create(session, row)


def _search(c, game_id, token, query: str):
    return c.get(
        f"/games/{game_id}/resources/search", params={"q": query}, headers=bearer(token)
    )


def _read(c, game_id, token, resource_id: str):
    return c.get(f"/games/{game_id}/resources/{resource_id}", headers=bearer(token))


def _running_game(engine: Engine):
    creation = seed(engine)
    start_to_diplomacy(engine, creation)
    return creation


def test_res_001_public_resource_searchable_by_every_actor(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    for actor in ACTORS:
        response = _search(c, creation.game_id, creation.seat_tokens[actor], "REGIONAL BACK")
        assert response.status_code == 200
        # Every Actor discovers the public Resource (testing.md TEST-RES-001). Under the
        # released `search-v2` default (I-112) "REGIONAL" also covers "Regional Trade Data";
        # the ranking itself is TEST-I100-002's subject, not this one's.
        assert response.json()["query"] == "REGIONAL BACK"
        results = response.json()["results"]
        assert [item["id"] for item in results] == ["background", "trade"]
        assert results[0] == {
            "id": "background",
            "name": "Regional Background",
            "type": "background",
            "tags": ["history", "politics"],
            "snippet": "Regional Background",
        }
        read = _read(c, creation.game_id, creation.seat_tokens[actor], "background")
        assert read.status_code == 200
        assert read.json()["resource"]["id"] == "background"


def test_res_002_private_resource_search_and_read_only_for_authorised_actor(
    db_engine: Engine,
) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    authorised = _search(c, creation.game_id, creation.seat_tokens["arkania"], "intelligence")
    assert [item["id"] for item in authorised.json()["results"]] == ["arkania-intel"]
    assert _read(c, creation.game_id, creation.seat_tokens["arkania"], "arkania-intel").status_code == 200
    for actor in ("belvar", "cestia"):
        assert _search(c, creation.game_id, creation.seat_tokens[actor], "intelligence").json()["results"] == []
        assert _read(c, creation.game_id, creation.seat_tokens[actor], "arkania-intel").status_code == 404


def test_res_003_and_004_hidden_resource_has_zero_influence_or_metadata_leak(
    db_engine: Engine,
) -> None:
    creation_a = _running_game(db_engine)
    creation_b = _running_game(db_engine)
    marker = "HIDDEN-NEEDLE"
    hidden = _resource(
        creation_b.game_id,
        "hidden-needle-id",
        name=f"{marker} name",
        tags=[marker],
        content=f"{marker} content",
        visibility="private",
        visible_to=["arkania"],
        metadata={"secret": marker},
    )
    # File-backed readable Resources legitimately carry both text and a safe attachment basename.
    hidden.source_kind = "file"
    hidden.attachment_name = f"{marker}.txt"
    _insert(db_engine, hidden)
    c = client(db_engine)
    response_without = _search(c, creation_a.game_id, creation_a.seat_tokens["belvar"], marker)
    response_with = _search(c, creation_b.game_id, creation_b.seat_tokens["belvar"], marker)
    assert response_without.status_code == response_with.status_code == 200
    assert response_without.json() == response_with.json() == {"query": marker, "results": []}
    assert "hidden-needle-id" not in response_with.text
    assert "secret" not in response_with.text


def test_search_matches_name_tag_content_case_insensitively_and_opaque_name_tag_only(
    db_engine: Engine,
) -> None:
    creation = _running_game(db_engine)
    gid = creation.game_id
    _insert(
        db_engine,
        _resource(gid, "by-name", name="Alpha NAmE", tags=[], content="nothing"),
        _resource(gid, "by-tag", name="Other", tags=["ALPHA-tag"], content="nothing"),
        _resource(gid, "by-content", name="Other Two", tags=[], content="prefix aLpHa suffix"),
        _resource(gid, "opaque-name", name="Alpha Diagram", tags=[], attachment_name="secret-alpha.png"),
        _resource(gid, "opaque-tag", name="Diagram", tags=["alpha-map"], attachment_name="map.png"),
        _resource(gid, "opaque-unsearchable", name="Diagram Two", tags=[], attachment_name="alpha-only-in-path.png"),
    )
    body = _search(client(db_engine), gid, creation.seat_tokens["belvar"], "alpha").json()
    assert [item["id"] for item in body["results"]] == [
        "by-name", "opaque-name", "by-tag", "opaque-tag", "by-content"
    ]
    assert [item["snippet"] for item in body["results"]] == [
        "Alpha NAmE", "Alpha Diagram", "ALPHA-tag", "alpha-map", "prefix aLpHa suffix"
    ]
    assert "opaque-unsearchable" not in repr(body)


def test_search_excludes_ids_metadata_attachment_names_hashes_bytes_and_media_type(
    db_engine: Engine,
) -> None:
    creation = _running_game(db_engine)
    marker = "forbiddenneedle"
    row = _resource(
        creation.game_id,
        f"{marker}-id",
        name="Unrelated",
        tags=[],
        metadata={"secret": marker},
        attachment_name=f"{marker}.png",
    )
    row.content_sha256 = marker
    row.source_bytes = marker.encode()
    row.media_type = f"application/{marker}"
    _insert(db_engine, row)
    response = _search(
        client(db_engine), creation.game_id, creation.seat_tokens["belvar"], f"  {marker}  "
    )
    assert response.status_code == 200
    assert response.json() == {"query": marker, "results": []}


def test_search_ranking_limit_tiebreak_and_bounded_centered_snippet(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    gid = creation.game_id
    long_text = "before " * 40 + "needle" + " after" * 40
    rows = [
        _resource(gid, f"tie-{i:02}", name="needle exact", tags=[], content="none")
        for i in range(25)
    ]
    rows.extend([
        _resource(gid, "exact", name="needle", tags=[], content="none"),
        _resource(gid, "content", name="content", tags=[], content=long_text),
    ])
    _insert(db_engine, *rows)
    c = client(db_engine)
    token = creation.seat_tokens["cestia"]
    results = _search(c, gid, token, "needle").json()["results"]
    assert len(results) == 20
    assert [r["id"] for r in results[:3]] == ["exact", "tie-00", "tie-01"]
    snippet = _search(c, gid, token, "before before needle").json()["results"][0]["snippet"]
    assert len(snippet) <= 160
    assert "needle" in snippet.casefold()
    assert snippet.startswith("…") and snippet.endswith("…")


def test_blank_query_uses_standard_validation_envelope(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    response = _search(client(db_engine), creation.game_id, creation.seat_tokens["arkania"], " \t ")
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "validation_error", "message": "Resource search query must not be blank."}
    }


def test_read_text_and_attachment_use_explicit_safe_projections(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    gid = creation.game_id
    _insert(
        db_engine,
        _resource(gid, "readable", name="Readable", tags=["safe"], content="exact content", metadata={"period": "now"}),
        _resource(gid, "opaque", name="Opaque", tags=["map"], attachment_name="map.png", metadata={"source": "team"}),
    )
    c = client(db_engine)
    token = creation.seat_tokens["belvar"]
    assert _read(c, gid, token, "readable").json() == {
        "resource": {
            "id": "readable", "name": "Readable", "type": "open-type",
            "content": "exact content", "tags": ["safe"], "metadata": {"period": "now"},
        }
    }
    attachment = _read(c, gid, token, "opaque").json()
    assert attachment == {
        "resource": {
            "id": "opaque", "name": "Opaque", "type": "open-type",
            "attachment": {"name": "map.png", "media_type": "image/png", "size_bytes": 14},
            "tags": ["map"], "metadata": {"source": "team"},
        }
    }
    forbidden = {
        "game_id", "visibility", "visible_to", "source_kind", "source_bytes",
        "content_sha256", "scenario_content_sha256", "representation_kind", "path",
    }
    assert forbidden.isdisjoint(attachment["resource"])


def test_hidden_missing_and_other_game_reads_have_identical_404_envelopes(db_engine: Engine) -> None:
    own = _running_game(db_engine)
    other = _running_game(db_engine)
    _insert(
        db_engine,
        _resource(own.game_id, "hidden", name="Hidden", tags=[], content="secret", visibility="private", visible_to=["arkania"]),
        _resource(other.game_id, "other-only", name="Other", tags=[], content="other"),
    )
    c = client(db_engine)
    token = own.seat_tokens["belvar"]
    responses = [_read(c, own.game_id, token, rid) for rid in ("hidden", "missing", "other-only")]
    expected = {"error": {"code": "not_found", "message": "Resource not found."}}
    assert {r.status_code for r in responses} == {404}
    assert [r.json() for r in responses] == [expected, expected, expected]


def test_identical_resource_ids_and_search_are_isolated_between_games(db_engine: Engine) -> None:
    first = _running_game(db_engine)
    second = _running_game(db_engine)
    _insert(
        db_engine,
        _resource(first.game_id, "shared-id", name="First Needle", tags=[], content="first"),
        _resource(second.game_id, "shared-id", name="Second Needle", tags=[], content="second"),
    )
    c = client(db_engine)
    token = first.seat_tokens["arkania"]
    assert _read(c, first.game_id, token, "shared-id").json()["resource"]["name"] == "First Needle"
    # Searching the first game for the second game's Resource name reaches only the first
    # game's rows. Under the released `search-v2` default (I-112) the shared "Needle" token
    # matches this game's own Resource — so isolation is stated as "never the other game's
    # content", which is what game scoping actually guarantees, rather than "no results".
    results = _search(c, first.game_id, token, "Second Needle").json()["results"]
    assert [item["name"] for item in results] == ["First Needle"]
    assert "second" not in json.dumps(results)


@pytest.mark.parametrize("status", [GameStatus.lobby, GameStatus.paused, GameStatus.complete])
def test_search_and_read_share_running_only_status_policy(db_engine: Engine, status: GameStatus) -> None:
    creation = seed(db_engine)
    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        game.status = status
        if status is GameStatus.complete:
            game.phase = GamePhase.complete
        session.add(game)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    search = _search(c, creation.game_id, token, "regional")
    read = _read(c, creation.game_id, token, "background")
    expected = {
        "error": {
            "code": "wrong_phase",
            "message": "Resources are available only while the game is running.",
            "details": {"status": status.value},
        }
    }
    assert search.status_code == read.status_code == 409
    assert search.json() == read.json() == expected


def test_resource_routes_preserve_authentication_envelopes(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    missing = c.get(f"/games/{creation.game_id}/resources/search", params={"q": "x"})
    malformed = c.get(
        f"/games/{creation.game_id}/resources/background", headers={"Authorization": "not-bearer"}
    )
    assert missing.status_code == malformed.status_code == 401
    assert missing.json()["error"]["code"] == malformed.json()["error"]["code"] == "invalid_token"


# The append-only Resource-access log (T-050) is the one intended, non-authoritative write these
# reads make (TRD §29); it is excluded here so "unchanged" scopes to the *authoritative* tables.
_OBSERVATIONAL_TABLES = {"resourceaccess"}


def _row_count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one()


def _database_snapshot(engine: Engine) -> dict[str, list[tuple]]:
    snapshot: dict[str, list[tuple]] = {}
    inspector = inspect(engine)
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names()):
            if table in _OBSERVATIONAL_TABLES:
                continue
            columns = [column["name"] for column in inspector.get_columns(table)]
            quoted = ", ".join(f'"{column}"' for column in columns)
            rows = connection.exec_driver_sql(f'SELECT {quoted} FROM "{table}"').fetchall()
            snapshot[table] = sorted((tuple(row) for row in rows), key=repr)
    return snapshot


def test_resource_search_and_read_do_not_mutate_any_authoritative_table(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    before = _database_snapshot(db_engine)
    log_before = _row_count(db_engine, "resourceaccess")
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    for _ in range(3):
        assert _search(c, creation.game_id, token, "regional").status_code == 200
        assert _read(c, creation.game_id, token, "background").status_code == 200
    # No authoritative table changed — reads never mutate game state (T-048, INV-RES-004) ...
    assert _database_snapshot(db_engine) == before
    # ... but the observational access log grew append-only, one row per authorised operation.
    assert _row_count(db_engine, "resourceaccess") == log_before + 6
