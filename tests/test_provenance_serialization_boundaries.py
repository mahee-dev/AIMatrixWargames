"""I-086 — canonical serialization and untrusted export boundaries.

Four boundaries fail closed with their own error or encode losslessly: ``canonical_json`` refuses
non-finite floats (a hash over ``NaN`` bytes is a hash no standard reader can parse), the manifest
and plan boundaries translate that into their own errors, nested plan mappings are as strict as the
top level (methodology §4.2 "Undeclared keys are rejected"), credential-shaped keys are refused by
shape (testing.md §24, TRD §36), and CSV cells are spreadsheet-safe yet reversible (methodology
§25: the tables are a derived projection; ``export.json`` stays the authority). None of it moves a
sealed hash of a currently valid document.
"""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import select

from amaw_contracts import (
    canonical_json,
    canonical_json_size,
    canonical_sha256,
    is_sensitive_key,
)
from app.api import create_app
from app.database import session_scope
from app.models import RunManifest
from benchmark.analysis_plan import (
    AnalysisPlanError,
    load_analysis_plan,
    plan_sha256,
    validate_analysis_plan,
)
from benchmark.controls import reconcile_plan
from benchmark.outputs import _cell
from tests.test_benchmark_controls import _schedule
from tests.test_provenance_analysis_plan import VALID_PLAN
from tests.test_provenance_precommitment import ADMIN_TOKEN, FIXTURE_DIR, _admin
from tests.test_provenance_condition_projections import _recipe
from tests.test_benchmark_output_mechanics import _export, _schedule as _batch_schedule
from tests.test_run_provenance import _configuration

EXAMPLE_PLAN = Path(__file__).resolve().parents[1] / "docs" / "analysis-plan-example.yaml"
# Pinned before I-086 touched the encoder: the shipped plan's sealed identity (D-054) and the
# bytes for a document exercising every JSON scalar shape. Neither may move.
EXAMPLE_PLAN_SHA256 = "45d09488f2fa8d83ab56fe1f6f83b51a9a9203862be71fdf6c28d5a52f3c632c"
PINNED_DOCUMENT = {
    "z": [1, -0.0, 1e21, 2.5, True, None, "é☃"],
    "a": {"b": {"c": "x"}, "-": ""},
    "0": 0,
}
PINNED_BYTES = (
    b'{"0":0,"a":{"-":"","b":{"c":"x"}},"z":[1,-0.0,1e+21,2.5,true,null,"\xc3\xa9\xe2\x98\x83"]}'
)
PINNED_SHA256 = "7fb1c16c2539fe346d44b85d63884209d908e2928ea0b253587c0022055892bd"


# --- TEST-I086-001 -----------------------------------------------------------------------------


@pytest.mark.parametrize("value", [{"a": float("nan")}, {"b": float("inf")}, [-float("inf")]])
def test_i086_001_canonical_json_refuses_non_finite_floats(value) -> None:
    with pytest.raises(ValueError):
        canonical_json(value)
    with pytest.raises(ValueError):
        canonical_sha256(value)
    with pytest.raises(ValueError):
        canonical_json_size(value)


def test_i086_001_canonical_json_is_otherwise_byte_identical() -> None:
    assert canonical_json(PINNED_DOCUMENT) == PINNED_BYTES
    assert canonical_sha256(PINNED_DOCUMENT) == PINNED_SHA256
    assert canonical_json_size(PINNED_DOCUMENT) == len(PINNED_BYTES)


def test_i086_001_example_plan_hash_is_unchanged() -> None:
    plan = load_analysis_plan(EXAMPLE_PLAN)
    assert plan.plan_id == "example-con-opposition-2026-09"
    assert plan.sha256 == EXAMPLE_PLAN_SHA256


# --- TEST-I086-002 -----------------------------------------------------------------------------


def test_i086_002_manifest_boundary_translates_nan_to_invalid_request(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw ``NaN`` literal reaches the handler (Starlette's ``json.loads`` accepts it, pydantic
    passes ``float('nan')`` through ``Any``); the boundary must answer 400 with the path, never a
    500 from the serializer, and must seal nothing."""
    monkeypatch.setenv("AMAW_ADMIN_TOKEN", ADMIN_TOKEN)
    client = TestClient(create_app(engine=db_engine), raise_server_exceptions=False)
    created = client.post("/admin/games", json={"scenario": str(FIXTURE_DIR)}, headers=_admin())
    game_id = created.json()["game_id"]

    body = json.dumps({"configuration": _configuration(commit="nan")})
    body = body.replace('"repetition_index": 2', '"repetition_index": NaN')
    assert "NaN" in body
    response = client.post(
        f"/admin/games/{game_id}/run-manifest",
        content=body.encode("utf-8"),
        headers={**_admin(), "Content-Type": "application/json"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_request"
    assert "configuration.benchmark.repetition_index" in response.json()["error"]["message"]
    with session_scope(db_engine) as session:
        assert session.exec(select(RunManifest)).all() == []

    control = client.post(
        f"/admin/games/{game_id}/run-manifest",
        json={"configuration": _configuration(commit="nan")},
        headers=_admin(),
    )
    assert control.status_code == 201, control.text


# --- TEST-I086-003 -----------------------------------------------------------------------------


def _plan_yaml(plan: dict) -> str:
    return yaml.safe_dump(plan, sort_keys=False)


def test_i086_003_plan_loader_names_an_unquoted_date(tmp_path: Path) -> None:
    text = _plan_yaml(VALID_PLAN)
    assert "authored_at: '2026-09-16'" in text
    quoted = tmp_path / "quoted.yaml"
    quoted.write_text(text, encoding="utf-8")
    unquoted = tmp_path / "unquoted.yaml"
    unquoted.write_text(text.replace("authored_at: '2026-09-16'", "authored_at: 2026-09-16"))

    with pytest.raises(AnalysisPlanError, match="authored_at") as excinfo:
        load_analysis_plan(unquoted)
    assert "quoted" in str(excinfo.value)
    # Reject, do not coerce: the quoted form of the same document is the same plan as before.
    assert load_analysis_plan(quoted).sha256 == plan_sha256(VALID_PLAN)


def test_i086_003_plan_loader_names_a_nested_timestamp_path(tmp_path: Path) -> None:
    plan = copy.deepcopy(VALID_PLAN)
    plan["hypotheses"][0]["statement"] = "PLACEHOLDER"
    text = _plan_yaml(plan).replace("statement: PLACEHOLDER", "statement: 2026-09-16 10:00:00")
    path = tmp_path / "plan.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(AnalysisPlanError, match=r"hypotheses\[0\]\.statement"):
        load_analysis_plan(path)


def test_i086_003_plan_loader_names_a_non_finite_number(tmp_path: Path) -> None:
    # ``yaml.safe_load`` (which also reads the JSON form) turns ``.nan`` into ``float('nan')``;
    # a JSON-literal ``NaN`` is a YAML string and already fails the positive-integer rule.
    plan = copy.deepcopy(VALID_PLAN)
    plan["design"]["planned_attempts"] = 99999
    text = _plan_yaml(plan).replace("planned_attempts: 99999", "planned_attempts: .nan")
    path = tmp_path / "plan.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(AnalysisPlanError, match=r"design\.planned_attempts"):
        load_analysis_plan(path)


# --- TEST-I086-004 -----------------------------------------------------------------------------


def _set(plan: dict, path: tuple, value) -> dict:
    plan = copy.deepcopy(plan)
    target = plan
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value
    return plan


@pytest.mark.parametrize(
    ("path", "value", "named"),
    [
        (("hypotheses", 0, "priority"), "high", ("hypotheses[0]", "priority")),
        (("outcome_measures", 1, "weight"), 2, ("outcome_measures[1]", "weight")),
        (("assurance_checks", 0, "owner"), "me", ("assurance_checks[0]", "owner")),
        (("treatment", "baseline"), "open_cons", ("treatment", "baseline")),
        (("design", "alpha"), 0.05, ("design", "alpha")),
        (("failure_rules", "retries"), 3, ("failure_rules", "retries")),
    ],
)
def test_i086_004_undeclared_nested_keys_are_rejected_by_path(path, value, named) -> None:
    with pytest.raises(AnalysisPlanError) as excinfo:
        validate_analysis_plan(_set(VALID_PLAN, path, value))
    message = str(excinfo.value)
    assert "undeclared" in message
    for fragment in named:
        assert fragment in message, message


@pytest.mark.parametrize(
    ("path", "value", "named"),
    [
        (("treatment", "levels"), [1, 2], ("treatment.levels", "list of non-empty strings")),
        (("fixed_conditions",), [{}], ("fixed_conditions", "list of non-empty strings")),
        (("hypotheses", 0, "id"), 7, ("hypotheses[0].id", "non-empty string")),
        (("failure_rules", "exclusion_criteria"), [" "], ("failure_rules.exclusion_criteria", "list of non-empty strings")),
        (("outcome_measures", 0, "source"), ["a"], ("outcome_measures[0].source", "non-empty string")),
        (("design", "seed_policy"), 5, ("design.seed_policy", "non-empty string")),
        (("authored_at",), 20260916, ("authored_at", "non-empty string")),
        (("descriptive_telemetry",), "tokens", ("descriptive_telemetry", "list of non-empty strings")),
    ],
)
def test_i086_004_nested_scalar_and_list_types_are_enforced_by_path(path, value, named) -> None:
    with pytest.raises(AnalysisPlanError) as excinfo:
        validate_analysis_plan(_set(VALID_PLAN, path, value))
    message = str(excinfo.value)
    for fragment in named:
        assert fragment in message, message


def test_i086_004_valid_plans_are_unchanged_and_still_reconcile() -> None:
    validate_analysis_plan(copy.deepcopy(VALID_PLAN))
    assert plan_sha256(VALID_PLAN) == plan_sha256(copy.deepcopy(VALID_PLAN))
    example = load_analysis_plan(EXAMPLE_PLAN)
    assert example.sha256 == EXAMPLE_PLAN_SHA256
    record = reconcile_plan(
        example, schedule=_schedule(models=("fake/x",), repetitions=10), roster=["fake/x"]
    )
    assert record["plan_scope"] == "cell" and record["planned_attempts"] == 20


# --- TEST-I086-005 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "openrouter_api_key", "OPENROUTER-API-KEY", "access_token", "refresh_token",
        "client_secret", "x_api_key", "db_password", "Authorization", "bearer", "credentials",
        "api_key", "seat_tokens", "passwd",
        # I-095 (TEST-I095-001): bare ``token`` and camelCase spellings are the same shapes.
        "token", "Token", "TOKEN", "accessToken", "authToken", "access-token", "ACCESS_TOKEN",
        "apiToken", "clientSecret", "privateKey", "APIKey", "HTTPToken", "seat.token",
        "openrouter api key",
    ],
)
def test_i086_005_credential_shaped_keys_are_sensitive(name: str) -> None:
    assert is_sensitive_key(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "prompt_hash", "content_sha256", "tool_schema_version", "seed", "model_returned",
        # Contains ``key`` but is not credential-shaped: the substring rules must not match
        # ``key`` alone, and ``_key`` must be a suffix, not an infix.
        "key_points", "keyword_count", "monkey",
        # Token *counts* are telemetry (TRD §28 AgentRun, the inference profile), not credentials.
        "max_tokens", "input_tokens", "output_tokens",
        # I-095 (TEST-I095-002): the camelCase split must not turn counts or ``key`` words into
        # credential shapes — ``_tokens`` stays a non-suffix, ``key`` alone a non-fragment.
        "maxTokens", "inputTokens", "outputTokens", "keyPoints", "keyword", "tokenizer",
        "tokens", "tokenCount", "turnKey_points",
    ],
)
def test_i086_005_ordinary_keys_are_not_sensitive(name: str) -> None:
    assert is_sensitive_key(name) is False


def _walk_keys(value, path: str = "") -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.append(f"{path}.{key}" if path else str(key))
            keys.extend(_walk_keys(nested, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            keys.extend(_walk_keys(nested, f"{path}[{index}]"))
    return keys


def test_i086_005_real_content_has_no_false_positive() -> None:
    from agents.autonomous import expected_manifest_configuration
    from agents.code_version import runtime_fingerprint
    from agents.player import PlayerConfig
    
    config = PlayerConfig(model="fake/a", activation_mode="tool_loop")
    manifest = expected_manifest_configuration(
        config, run_configuration={"runtime": {"host": "x"}, "resume": _recipe(config)}
    )
    documents = {
        "manifest": manifest,
        "example_plan": load_analysis_plan(EXAMPLE_PLAN).content,
        "fingerprint": runtime_fingerprint(),
    }
    flagged = {
        name: [path for path in _walk_keys(document) if is_sensitive_key(path.rsplit(".", 1)[-1])]
        for name, document in documents.items()
    }
    assert flagged == {name: [] for name in documents}, flagged
    # The walk is real: the manifest carried the fields whose names look like traps.
    paths = set(_walk_keys(manifest))
    assert {"player.prompt_hash", "player.tool_schema_version", "environment.uv_lock_sha256"} <= paths


def test_i086_005_both_boundaries_refuse_credential_shaped_keys(
    db_engine: Engine, tmp_path: Path
) -> None:
    from app import provenance, scenarios

    creation = scenarios.create_game_from_scenario(db_engine, scenarios.load_scenario(FIXTURE_DIR))
    configuration = _configuration(commit="c")
    configuration["runtime"] = {"openrouter_api_key": "x"}
    with pytest.raises(provenance.ProvenanceError, match="configuration.runtime.*openrouter_api_key"):
        provenance.create_run_manifest(db_engine, creation.game_id, configuration=configuration)

    plan = copy.deepcopy(VALID_PLAN)
    plan["notes"] = {"client_secret": "x"}
    path = tmp_path / "plan.yaml"
    path.write_text(_plan_yaml(plan), encoding="utf-8")
    with pytest.raises(AnalysisPlanError, match="notes.*client_secret"):
        load_analysis_plan(path)


# --- TEST-I086-006 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["=1+1", "+x", "-x", "@x", "\tx", "\rx", "'x", "=", "'", "-3 (as text)"]
)
def test_i086_006_formula_leading_strings_get_one_quote_prefix(text: str) -> None:
    assert _cell(text) == "'" + text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x=1", "x=1"), ("", ""), ("plain", "plain"), (None, ""), (True, "true"), (False, "false"),
        (-3, -3), (-0.5, -0.5), (0, 0), ({"a": 1}, '{"a": 1}'), (["=a"], '["=a"]'),
    ],
)
def test_i086_006_other_cells_are_unchanged(value, expected) -> None:
    assert _cell(value) == expected


def test_i086_006_a_structure_with_nan_is_refused() -> None:
    with pytest.raises(ValueError):
        _cell({"metric": float("nan")})


def _unescape(cell: str) -> str:
    return cell[1:] if cell.startswith("'") else cell


def test_i086_006_tables_round_trip_losslessly(tmp_path: Path) -> None:
    from benchmark.runner import AttemptFailed, run_batch

    schedule = _batch_schedule(rotate=False, models=["provider/x"], repetitions=2, base_seed=10)
    originals = {
        "action": "=HYPERLINK(\"http://x\",\"click\")",
        "desired_effect": "-lead with a dash",
        "query": "@cmd",
        "failure_reason": "'quoted reason",
    }

    def execute(attempt):
        if attempt.repetition_index == 1:
            raise AttemptFailed(
                category="agent_stall", reason=originals["failure_reason"],
                export=_export(attempt, status="running", turn=2, turns_completed=1),
                turn_reached=2, actor_affected="b",
            )
        export = _export(attempt)
        export["arguments"] = [{
            "id": "arg-1", "turn": 1, "actor_id": "a", "action": originals["action"],
            "desired_effect": originals["desired_effect"], "pros": ["p"], "budget_slot": 1,
            "status": "adjudicated",
        }]
        export["resource_access"] = [{
            "turn": 1, "actor_id": "a", "operation": "search", "query": originals["query"],
            "resource_id": None, "created_at": "2026-09-19T00:00:00Z", "activation_id": "act-1",
        }]
        return export

    run_batch(
        schedule, execute=execute, output_dir=tmp_path / "batch",
        experiment={"name": "i086", "benchmark_mode": None, "scenario_id": "fixture"},
    )

    def read(name: str) -> list[dict[str, str]]:
        with (tmp_path / "batch" / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    arguments = read("arguments")
    assert len(arguments) == 1
    assert arguments[0]["action"].startswith("'") and arguments[0]["desired_effect"].startswith("'")
    assert _unescape(arguments[0]["action"]) == originals["action"]
    assert _unescape(arguments[0]["desired_effect"]) == originals["desired_effect"]
    assert arguments[0]["turn"] == "1"

    access = read("resource_access")
    assert len(access) == 1 and access[0]["query"] == "'@cmd"
    assert _unescape(access[0]["query"]) == originals["query"]

    games = {int(row["repetition_index"]): row for row in read("games")}
    assert games[1]["failure_reason"] == "''quoted reason"
    assert _unescape(games[1]["failure_reason"]) == originals["failure_reason"]
    assert games[0]["failure_reason"] == ""
    # Typed numbers are never prefixed, negative or not.
    assert games[0]["seed"] == "10" and not games[0]["seed"].startswith("'")
    # Nothing in any table starts with a formula character once the one-quote strip is applied.
    for name in ("games", "agents", "arguments", "messages", "resource_access"):
        for row in read(name):
            for cell in row.values():
                assert not (cell and cell[0] in "=+-@\t\r" and not cell.startswith("'")), (name, cell)
