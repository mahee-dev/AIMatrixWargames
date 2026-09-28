"""I-093 / I-097 — untrusted-boundary hardening, second external review (2026-09-19).

I-093: mapping keys are part of the canonical-JSON contract. ``first_non_json_scalar`` reports a
non-``str`` key (never coerced — ``{1: …}`` and ``{"1": …}`` must not seal to one identity) and a
circular structure (a recursive YAML alias) as its own finding, so both untrusted boundaries —
``load_analysis_plan`` and ``create_run_manifest`` — raise their own error naming the path rather
than the serializer's ``TypeError`` or a raw ``RecursionError`` (D-075 (2): reject, do not coerce).
``canonical_json`` bytes are untouched.

I-097: ``amaw run --export-path`` refuses the SQLite database file and its engine-owned siblings
before any Game exists, and the single-run export goes through the I-084 atomic writer, now shared
from ``app.export.write_json_atomic``.

The I-095 cases extend ``TEST-I086-005`` in ``test_provenance_serialization_boundaries.py``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine

from amaw_contracts import first_non_json_scalar
from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan, plan_sha256
from tests.test_provenance_analysis_plan import VALID_PLAN
from tests.test_provenance_precommitment import FIXTURE_DIR
from tests.test_run_provenance import _configuration


def _plan_yaml(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False)


def _self_referencing_list() -> list:
    cycle: list = []
    cycle.append(cycle)
    return cycle


# --- TEST-I093-001 — the walker reports non-string keys and cycles by path --------------------


@pytest.mark.parametrize(
    "document, path, key",
    [
        ({2: "x", "a": "y"}, "", 2),
        ({"design": {2: "x"}}, "design", 2),
        ({"design": {True: "x"}}, "design", True),
        ({"design": {None: "x"}}, "design", None),
        ({"design": {(1, 2): "x"}}, "design", (1, 2)),
        ({"hypotheses": [{"id": "h1"}, {3: "x"}]}, "hypotheses[1]", 3),
        # Mixed: the first non-string key wins, whichever value type sits beside it.
        ({"a": {"ok": 1, 1: "x", "later": float("nan")}}, "a", 1),
    ],
)
def test_i093_001_non_string_keys_are_reported_by_path(document, path, key) -> None:
    found = first_non_json_scalar(document)
    assert found is not None
    assert (found.path, found.reason) == (path, "key")
    assert found.value is key


def test_i093_001_a_cycle_is_reported_where_the_container_recurs() -> None:
    document = {"design": _self_referencing_list()}
    found = first_non_json_scalar(document)
    assert found is not None
    assert (found.path, found.reason) == ("design[0]", "cycle")
    assert found.value is document["design"]


def test_i093_001_shared_but_acyclic_structure_is_not_a_cycle() -> None:
    # A non-recursive YAML anchor reused twice is a DAG ``json.dumps`` encodes fine; an ancestor
    # set (not a global visited set) must let it through.
    shared = {"k": [1, 2, {"deep": "x"}]}
    document = {"a": shared, "b": shared, "c": [shared, shared]}
    assert first_non_json_scalar(document) is None


def test_i093_001_deeply_nested_acyclic_document_is_plain_json() -> None:
    document: dict = {"leaf": 1}
    for depth in range(200):
        document = {f"level{depth}": [document]}
    assert first_non_json_scalar(document) is None


def test_i093_001_scalar_findings_keep_their_shape() -> None:
    found = first_non_json_scalar({"a": [1, {"b": float("inf")}]})
    assert found is not None
    assert (found.path, found.reason) == ("a[1].b", "scalar")
    assert found.value == float("inf")
    assert first_non_json_scalar({"ok": [1, "x", None, True, 2.5]}) is None


# --- TEST-I093-002 — the plan loader refuses bad keys and recursive aliases by name ------------


@pytest.mark.parametrize(
    "text_key, path, named",
    [
        ("2", "plan", "2"),
        ("true", "plan", "True"),
        ("null", "plan", "None"),
    ],
)
def test_i093_002_plan_loader_names_a_non_string_top_level_key(
    tmp_path: Path, text_key: str, path: str, named: str
) -> None:
    source = tmp_path / "plan.yaml"
    source.write_text(_plan_yaml(VALID_PLAN) + f"{text_key}: x\n", encoding="utf-8")

    with pytest.raises(AnalysisPlanError) as excinfo:
        load_analysis_plan(source)
    message = str(excinfo.value)
    assert "non-string mapping key" in message
    assert f"'{path}'" in message
    assert named in message


def test_i093_002_plan_loader_names_a_nested_non_string_key(tmp_path: Path) -> None:
    plan = copy.deepcopy(VALID_PLAN)
    plan["design"]["PLACEHOLDER"] = "x"
    text = _plan_yaml(plan).replace("PLACEHOLDER: x", "7: x")
    source = tmp_path / "plan.yaml"
    source.write_text(text, encoding="utf-8")

    with pytest.raises(AnalysisPlanError, match=r"'design'.*non-string mapping key 7"):
        load_analysis_plan(source)


def test_i093_002_plan_loader_refuses_a_recursive_alias(tmp_path: Path) -> None:
    plan = copy.deepcopy(VALID_PLAN)
    plan["design"] = "PLACEHOLDER"
    text = _plan_yaml(plan).replace("design: PLACEHOLDER", "design: &a [*a]")
    source = tmp_path / "plan.yaml"
    source.write_text(text, encoding="utf-8")
    assert isinstance(yaml.safe_load(text)["design"][0], list)  # the fixture really recurses

    with pytest.raises(AnalysisPlanError, match=r"'design\[0\]'.*circular"):
        load_analysis_plan(source)


def test_i093_002_plan_loader_accepts_a_reused_non_recursive_alias(tmp_path: Path) -> None:
    plan = copy.deepcopy(VALID_PLAN)
    statement = plan["hypotheses"][0]["statement"]
    text = _plan_yaml(plan)
    assert text.count("statement: ") >= 1
    # Share the first hypothesis statement via an anchor: the same document, spelled with an alias.
    aliased = text.replace("statement: ", "statement: &s ", 1) + "notes: *s\n"
    source = tmp_path / "plan.yaml"
    source.write_text(aliased, encoding="utf-8")
    expected = copy.deepcopy(plan)
    expected["notes"] = statement

    assert load_analysis_plan(source).sha256 == plan_sha256(expected)


def test_i093_002_a_bare_recursive_document_still_fails_the_root_shape_check(
    tmp_path: Path,
) -> None:
    source = tmp_path / "plan.yaml"
    source.write_text("&a [*a]\n", encoding="utf-8")
    with pytest.raises(AnalysisPlanError, match="must be a mapping"):
        load_analysis_plan(source)


# --- TEST-I093-003 — the manifest boundary refuses the same shapes with its own error ---------


def test_i093_003_manifest_boundary_refuses_a_non_string_key(db_engine: Engine) -> None:
    from app import provenance, scenarios

    creation = scenarios.create_game_from_scenario(db_engine, scenarios.load_scenario(FIXTURE_DIR))
    configuration = _configuration(commit="c")
    configuration["runtime"] = {1: "x"}
    with pytest.raises(provenance.ProvenanceError, match=r"mapping key at configuration\.runtime: 1"):
        provenance.create_run_manifest(db_engine, creation.game_id, configuration=configuration)


def test_i093_003_manifest_boundary_refuses_a_cycle_without_recursion_error(
    db_engine: Engine,
) -> None:
    from app import provenance, scenarios

    creation = scenarios.create_game_from_scenario(db_engine, scenarios.load_scenario(FIXTURE_DIR))
    configuration = _configuration(commit="c")
    configuration["runtime"] = {"loop": _self_referencing_list()}
    with pytest.raises(provenance.ProvenanceError, match=r"circular structure at configuration\.runtime\.loop\[0\]"):
        provenance.create_run_manifest(db_engine, creation.game_id, configuration=configuration)


# --- TEST-I097-001 — ``--export-path`` never names the database file -------------------------


def _cli_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, database: str) -> Path:
    """A ``run`` invocation that must never get as far as the engine: any runner is a failure."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", database)
    monkeypatch.delenv("AMAW_ADMIN_TOKEN", raising=False)

    class NeverRunner:
        def __init__(self, client, **kwargs):  # noqa: ANN001
            raise AssertionError("the run reached the engine and the runner")

    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", NeverRunner)
    package = tmp_path / "example-scenario"
    package.mkdir()
    (package / "scenario.yaml").write_text("scenario: {}\n", encoding="utf-8")
    return package


@pytest.mark.parametrize(
    "database, export",
    [
        ("data/amaw.db", "data/amaw.db"),  # the same relative spelling
        ("data/amaw.db", "data/../data/amaw.db"),  # a different relative spelling
        ("data/amaw.db", "{tmp}/data/amaw.db"),  # absolute against relative
        ("{tmp}/data/amaw.db", "data/amaw.db"),  # relative against absolute
        ("data/amaw.db", "data/amaw.db-wal"),  # engine-owned siblings
        ("data/amaw.db", "data/amaw.db-shm"),
        ("data/amaw.db", "data/amaw.db-journal"),
    ],
)
def test_i097_001_export_path_refuses_the_database_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: str, export: str
) -> None:
    from typer.testing import CliRunner

    import amaw_cli

    database = database.format(tmp=tmp_path)
    export = export.format(tmp=tmp_path)
    package = _cli_environment(monkeypatch, tmp_path, database)

    result = CliRunner().invoke(amaw_cli.app, ["run", str(package), "--export-path", export])

    assert result.exit_code == 2, result.output
    assert "error:" in result.output and "--export-path" in result.output
    assert "database" in result.output
    # Refused before the engine existed: nothing created the database file or its directory.
    assert not (tmp_path / "data").exists()


def test_i097_001_export_path_refusal_applies_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uuid

    from typer.testing import CliRunner

    import amaw_cli

    _cli_environment(monkeypatch, tmp_path, "data/amaw.db")
    result = CliRunner().invoke(
        amaw_cli.app,
        ["run", "--resume-game", str(uuid.uuid4()), "--export-path", "data/amaw.db"],
    )

    assert result.exit_code == 2, result.output
    assert "error:" in result.output and "--export-path" in result.output
    assert not (tmp_path / "data").exists()


def test_i097_001_an_export_beside_the_database_is_still_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import amaw_cli

    package = _cli_environment(monkeypatch, tmp_path, "data/amaw.db")
    monkeypatch.setattr(
        "agents.autonomous.AutonomousGameRunner",
        lambda client, **kwargs: (_ for _ in ()).throw(RuntimeError("reached the runner")),
    )

    result = CliRunner().invoke(
        amaw_cli.app, ["run", str(package), "--export-path", "data/export.json"]
    )

    # Not the refusal: the run got past the guard (and then died in the stand-in runner).
    assert result.exit_code != 2, result.output
    assert "--export-path" not in result.output


# --- TEST-I097-002 — the single-run export is written atomically, through the shared writer ---


def test_i097_002_write_json_atomic_keeps_the_previous_export_on_a_torn_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from app import export as export_module
    from app.export import write_json_atomic

    target = tmp_path / "export.json"
    write_json_atomic(target, {"game": {"turn": 1}})
    assert json.loads(target.read_text(encoding="utf-8")) == {"game": {"turn": 1}}

    real_replace = os.replace

    def crash(src, dst, *args, **kwargs):
        raise OSError("killed mid-write")

    monkeypatch.setattr(export_module.os, "replace", crash)
    with pytest.raises(OSError, match="killed"):
        write_json_atomic(target, {"game": {"turn": 2}})
    assert json.loads(target.read_text(encoding="utf-8")) == {"game": {"turn": 1}}
    assert [p.name for p in tmp_path.iterdir()] == ["export.json"]

    monkeypatch.setattr(export_module.os, "replace", real_replace)
    write_json_atomic(target, {"game": {"turn": 2}})
    assert json.loads(target.read_text(encoding="utf-8")) == {"game": {"turn": 2}}
    assert [p.name for p in tmp_path.iterdir()] == ["export.json"]


def test_i097_002_write_json_atomic_matches_the_previous_export_encoding(tmp_path: Path) -> None:
    from app.export import write_json_atomic

    payload = {"z": [1, 2.5, None], "a": {"when": Path("x")}}
    target = tmp_path / "export.json"
    write_json_atomic(target, payload)
    # The bytes ``Path.write_text(json.dumps(..., indent=2, sort_keys=True, default=str) + "\n")``
    # produced before I-097 — readers of earlier exports see no change.
    assert target.read_text(encoding="utf-8") == (
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    )


def _spy_on_writer(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Path, object]]:
    from app import export as export_module

    real = export_module.write_json_atomic
    calls: list[tuple[Path, object]] = []

    def spy(path, payload):  # noqa: ANN001
        calls.append((Path(path), payload))
        return real(path, payload)

    monkeypatch.setattr(export_module, "write_json_atomic", spy)
    return calls


def test_i097_002_final_run_export_goes_through_the_atomic_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from typer.testing import CliRunner

    import amaw_cli

    package = _cli_environment(monkeypatch, tmp_path, "data/amaw.db")
    export_path = tmp_path / "out" / "export.json"

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            return cls()

    class FakeRunner:
        def __init__(self, client, **kwargs):  # noqa: ANN001
            self.last_game_id = None

        def run(self):
            return SimpleNamespace(game_id="completed-game", export={"game": {"id": "completed-game"}})

    class FakePlayer:
        @classmethod
        def for_openrouter(cls, tools, **kwargs):  # noqa: ANN001
            return cls()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)
    calls = _spy_on_writer(monkeypatch)

    result = CliRunner().invoke(
        amaw_cli.app, ["run", str(package), "--export-path", str(export_path)]
    )

    assert result.exit_code == 0, result.output
    assert calls == [(export_path, {"game": {"id": "completed-game"}})]
    assert json.loads(export_path.read_text(encoding="utf-8")) == {"game": {"id": "completed-game"}}


def test_i097_002_best_effort_partial_export_goes_through_the_atomic_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import amaw_cli
    from agents.autonomous import AutonomousGameError

    package = _cli_environment(monkeypatch, tmp_path, "data/amaw.db")
    export_path = tmp_path / "failed" / "export.json"
    game_id = "27b9418d-6a85-49c8-a477-5cf6e9f4c1d7"

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            return cls()

    class FailingRunner:
        def __init__(self, client, **kwargs):  # noqa: ANN001
            self.last_game_id = game_id

        def run(self):
            raise AutonomousGameError("provider exhausted")

    expected = {"game": {"id": game_id, "status": "paused"}}
    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FailingRunner)
    monkeypatch.setattr("app.export.build_game_export", lambda engine, parsed: expected)
    calls = _spy_on_writer(monkeypatch)

    result = CliRunner().invoke(
        amaw_cli.app, ["run", str(package), "--export-path", str(export_path)]
    )

    assert result.exit_code == 1, result.output
    assert f"autonomous run for game {game_id} failed" in result.output
    assert calls == [(export_path, expected)]
    assert json.loads(export_path.read_text(encoding="utf-8")) == expected
