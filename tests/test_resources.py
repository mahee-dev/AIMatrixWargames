"""T-046 Resource format validation and deterministic materialisation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app import persistence, scenarios
from app.database import session_scope
from app.models import Resource
from app.resources import (
    MAX_RESOURCE_BYTES,
    MAX_TEXT_CHARS,
    FormatMaterializationError,
    materialize_resource,
)
from tests.api_helpers import FIXTURE_DIR


@pytest.mark.parametrize(
    ("name", "content", "media_type"),
    [
        ("guide.md", b"# Heading\r\nBody\n", "text/markdown; charset=utf-8"),
        ("guide.TXT", b"plain text\n", "text/plain; charset=utf-8"),
        ("data.yaml", b"answer: 42\n", "application/yaml; charset=utf-8"),
        ("data.yml", b"- one\n- two\n", "application/yaml; charset=utf-8"),
        ("data.json", b'{"answer": 42}\n', "application/json; charset=utf-8"),
        ("data.csv", b"name,value\r\nalpha,42\r\n", "text/csv; charset=utf-8"),
    ],
)
def test_required_text_formats_preserve_validated_original_text(
    name: str, content: bytes, media_type: str
) -> None:
    materialized = materialize_resource(
        source_kind="file", source_bytes=content, attachment_name=name
    )

    assert materialized.representation_kind == "text"
    assert materialized.media_type == media_type
    assert materialized.text_content == content.decode("utf-8")
    assert materialized.attachment_name == Path(name).name
    assert materialized.size_bytes == len(content)


def test_inline_content_materialises_as_exact_text() -> None:
    content = "Inline π\r\nunchanged"
    materialized = materialize_resource(
        source_kind="inline", source_bytes=content.encode(), attachment_name=None
    )

    assert materialized.representation_kind == "text"
    assert materialized.media_type == "text/plain; charset=utf-8"
    assert materialized.text_content == content
    assert materialized.attachment_name is None


@pytest.mark.parametrize(
    ("name", "content", "media_type"),
    [
        ("brief.pdf", b"%PDF-1.7\nopaque", "application/pdf"),
        ("map.png", b"\x89PNG\r\n\x1a\nopaque", "image/png"),
        ("photo.jpg", b"\xff\xd8\xff\xe0opaque", "image/jpeg"),
        ("photo.JPEG", b"\xff\xd8\xff\xe1opaque", "image/jpeg"),
        ("borders.geojson", b"{not semantically parsed}", "application/geo+json"),
    ],
)
def test_optional_formats_are_safe_opaque_attachments(
    name: str, content: bytes, media_type: str
) -> None:
    materialized = materialize_resource(
        source_kind="file", source_bytes=content, attachment_name=name
    )

    assert materialized.representation_kind == "attachment"
    assert materialized.media_type == media_type
    assert materialized.text_content is None
    assert materialized.attachment_name == Path(name).name
    assert materialized.size_bytes == len(content)
    assert not hasattr(materialized, "path")


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("broken.yaml", b"value: [unterminated\n", "YAML"),
        ("broken.json", b'{"unterminated": ', "JSON"),
        ("broken.csv", b'name,value\n"unterminated', "CSV"),
        ("uneven.csv", b"a,b\n1,2,3\n", "consistent"),
        ("bad.txt", b"\xff", "UTF-8"),
    ],
)
def test_malformed_required_text_is_rejected(name: str, content: bytes, message: str) -> None:
    with pytest.raises(FormatMaterializationError, match=message):
        materialize_resource(
            source_kind="file", source_bytes=content, attachment_name=name
        )


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("fake.pdf", b"not a PDF", "PDF"),
        ("fake.png", b"not a PNG", "PNG"),
        ("fake.jpeg", b"not JPEG", "JPEG"),
        ("unknown.docx", b"anything", "unsupported"),
        ("extensionless", b"anything", "extension"),
        ("../escape.pdf", b"%PDF-1.7", "basename"),
        (r"..\escape.pdf", b"%PDF-1.7", "basename"),
    ],
)
def test_invalid_or_unsafe_attachments_are_rejected(
    name: str, content: bytes, message: str
) -> None:
    with pytest.raises(FormatMaterializationError, match=message):
        materialize_resource(
            source_kind="file", source_bytes=content, attachment_name=name
        )


def test_resource_byte_and_decoded_text_limits_are_enforced() -> None:
    with pytest.raises(FormatMaterializationError, match="2 MiB"):
        materialize_resource(
            source_kind="file",
            source_bytes=b"x" * (MAX_RESOURCE_BYTES + 1),
            attachment_name="large.txt",
        )

    with pytest.raises(FormatMaterializationError, match="1,000,000"):
        materialize_resource(
            source_kind="file",
            source_bytes=("é" * (MAX_TEXT_CHARS + 1)).encode(),
            attachment_name="long.txt",
        )


def _package(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    resources = package / "resources"
    resources.mkdir(parents=True)
    (resources / "facts.json").write_bytes(b'{"fact": "stable"}\r\n')
    (resources / "map.png").write_bytes(b"\x89PNG\r\n\x1a\nopaque-map")
    (package / "scenario.yaml").write_text(
        textwrap.dedent(
            """\
            scenario: {id: format-demo, version: 1, name: Format Demo}
            game:
              max_turns: 1
              turn_order: [a, b, c]
              seed: 1
            actors:
              a: {name: A, public_description: A, private_brief: A, objectives: [A]}
              b: {name: B, public_description: B, private_brief: B, objectives: [B]}
              c: {name: C, public_description: C, private_brief: C, objectives: [C]}
            resources:
              - {id: inline, name: Inline, type: arbitrary, content: "Inline π", visibility: public}
              - {id: json, name: JSON, type: arbitrary, path: resources/facts.json, visibility: public}
              - {id: map, name: Map, type: arbitrary, path: resources/map.png, visibility: public}
            """
        )
    )
    return package


def test_materialisation_persists_with_snapshot_and_ignores_later_filesystem_mutation(
    tmp_path: Path, db_engine: Engine
) -> None:
    package = _package(tmp_path)
    loaded = scenarios.load_scenario(package)

    assert [(item.id, item.representation_kind) for item in loaded.resources] == [
        ("inline", "text"),
        ("json", "text"),
        ("map", "attachment"),
    ]
    creation = scenarios.create_game_from_scenario(db_engine, loaded)
    (package / "resources" / "facts.json").write_text('{"fact": "changed"}')
    (package / "resources" / "map.png").unlink()

    with session_scope(db_engine) as session:
        rows = {
            row.id: row
            for row in persistence.list_by_game(session, Resource, creation.game_id)
        }
        assert rows["inline"].text_content == "Inline π"
        assert rows["json"].text_content == '{"fact": "stable"}\r\n'
        assert rows["json"].media_type == "application/json; charset=utf-8"
        assert rows["map"].text_content is None
        assert rows["map"].attachment_name == "map.png"
        assert rows["map"].media_type == "image/png"
        assert rows["map"].size_bytes == len(b"\x89PNG\r\n\x1a\nopaque-map")


def test_canonical_fixture_materialises_without_changing_fixture_bytes() -> None:
    before = {path: path.read_bytes() for path in FIXTURE_DIR.rglob("*") if path.is_file()}
    loaded = scenarios.load_scenario(FIXTURE_DIR)
    by_id = {resource.id: resource for resource in loaded.resources}

    assert by_id["background"].media_type == "text/markdown; charset=utf-8"
    assert by_id["trade"].media_type == "text/csv; charset=utf-8"
    assert by_id["arkania-intel"].representation_kind == "text"
    assert all(resource.text_content is not None for resource in loaded.resources)
    assert {path: path.read_bytes() for path in FIXTURE_DIR.rglob("*") if path.is_file()} == before


def test_loader_reports_format_failures_as_scenario_validation_errors(tmp_path: Path) -> None:
    package = _package(tmp_path)
    (package / "resources" / "facts.json").write_text("{")

    with pytest.raises(scenarios.ScenarioValidationError, match="JSON"):
        scenarios.load_scenario(package)
