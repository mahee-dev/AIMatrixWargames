"""T-060 — benchmark batch smoke test (testing.md §22).

One verification routine walks the §22 checklist line by line against a real batch directory
written by ``amaw benchmark`` — ``batch.json``, ``games/<attempt>/{attempt,export}.json`` and the
five CSV tables — and two tests drive it:

* the offline smoke (default suite, no key): the fixture scenario, FunctionModel players and the
  deterministic Umpire behind the real CLI, with cyclic rotation, two repetitions and one
  injected failure;
* the provider smoke (``@pytest.mark.llm``): the same CLI on ``scenarios/northern-strait`` with
  the configured OpenRouter model(s) and the fixed AI Umpire, on a small budget.

No ``TEST-*`` IDs exist for §22; the tests are named after the task.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import amaw_cli
from benchmark.runner import FAILURE_CATEGORIES, SEED_POLICY
from tests.api_helpers import FIXTURE_DIR
from tests.benchmark_helpers import read_tables

SMOKE_SCENARIO = Path(__file__).parents[1] / "scenarios" / "northern-strait"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ADMIN_TOKEN = "admin-secret-token-for-smoke"
FAKE_KEY = "sk-or-smoke-test-key-never-written"


# --- the shared §22 verification -------------------------------------------------------------


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_section_22(
    batch_dir: Path,
    *,
    models: list[str],
    actor_ids: list[str],
    rotation_policy: str,
    repetitions: int,
    base_seed: int,
    expected_failures: int | None,
    secrets: list[str],
) -> dict[str, Any]:
    """Assert every ``testing.md §22`` verification line against one batch directory.

    ``expected_failures`` is the injected count for the offline smoke; ``None`` for a live batch,
    where any failure must still be retained and reported but is not demanded.
    Returns ``batch.json`` for test-specific follow-up assertions.
    """
    batch = _load(batch_dir / "batch.json")
    schedule = batch["schedule"]

    # --- total games == assignment_count * repetitions_per_assignment ------------------------
    assignment_count = len(models) if rotation_policy == "cyclic" else 1
    assert schedule["assignment_count"] == assignment_count
    assert schedule["repetitions_per_assignment"] == repetitions
    assert schedule["total_games"] == assignment_count * repetitions
    assert len(schedule["attempts"]) == len(batch["attempts"]) == schedule["total_games"]
    assert batch["rotation_policy"] == rotation_policy
    assert batch["rotation"]["policy"] == rotation_policy

    # --- role rotation / role assignment recorded ------------------------------------------
    assignments = {a["assignment_id"]: a for a in schedule["assignments"]}
    assert len(assignments) == assignment_count
    for assignment in assignments.values():
        assert list(assignment["models"]) == actor_ids
        assert set(assignment["models"].values()) <= set(models)
    if rotation_policy == "cyclic":
        # methodology §6: every listed model occupies every Seat exactly once across the set
        for actor_id in actor_ids:
            seat_models = [a["models"][actor_id] for a in schedule["assignments"]]
            assert sorted(seat_models) == sorted(models), (actor_id, seat_models)

    # --- assignment_id / repetition_index / seed recorded (multiple seeds) -------------------
    assert batch["base_seed"] == base_seed and batch["seed_policy"] == SEED_POLICY
    identities = set()
    seeds = set()
    for planned in schedule["attempts"]:
        identity = (planned["assignment_id"], planned["repetition_index"])
        assert identity not in identities, identity
        identities.add(identity)
        assert planned["assignment_id"] in assignments
        assert 0 <= planned["repetition_index"] < repetitions
        assert planned["seed"] == base_seed + planned["assignment_index"] * 1000 + planned["repetition_index"]
        seeds.add(planned["seed"])
    assert len(seeds) == schedule["total_games"], "seeds must be distinct per attempt"
    assert {(r["assignment_id"], r["repetition_index"]) for r in batch["attempts"]} == identities

    # --- every attempt is on disk: attempt.json + export.json ---------------------------------
    completed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    failed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record in batch["attempts"]:
        attempt_dir = batch_dir / "games" / record["directory"]
        assert _load(attempt_dir / "attempt.json") == record
        export = _load(attempt_dir / "export.json")
        assert record["seed"] in seeds
        assert record["models"] == assignments[record["assignment_id"]]["models"]
        assert record["rotation_policy"] == rotation_policy
        assert record["benchmark_mode"] == batch["benchmark_mode"]
        if record["completed"]:
            assert record["status"] == "completed"
            assert record["failure_category"] is None and record["failure_reason"] is None
            completed.append((record, export))
        else:
            # failed run does not abort batch: it is retained, classified, never counted complete
            assert record["status"] == "failed"
            assert record["failure_category"] in FAILURE_CATEGORIES
            assert record["failure_reason"]
            assert export["game"] is None or export["game"]["status"] != "complete"
            failed.append((record, export))
    assert batch["not_run_games"] == 0 and batch["integrity"]["ok"] is True
    assert batch["completed_games"] == len(completed)
    assert batch["failed_games"] == len(failed)
    assert batch["executed_games"] == len(completed) + len(failed) == schedule["total_games"]
    assert batch["completion_rate"] == pytest.approx(len(completed) / schedule["total_games"])
    if expected_failures is not None:
        assert len(failed) == expected_failures
        assert len(completed) == schedule["total_games"] - expected_failures
    assert completed, "a smoke batch must complete at least one game"

    # --- run configuration manifest; scenario/Resource/prompt hashes; AgentRun metadata -------
    umpire_conditions = []
    for record, export in completed:
        game = export["game"]
        assert game["status"] == "complete"
        assert game["seed"] == record["seed"]
        assert game["id"] == record["game_id"]
        manifest = export["run_manifest"]
        assert manifest["game_id"] == game["id"]
        assert manifest["seed"] == record["seed"]
        assert SHA256.match(manifest["manifest_sha256"])
        scenario = manifest["scenario"]
        assert SHA256.match(scenario["content_sha256"])
        assert SHA256.match(scenario["resource_manifest_sha256"])
        assert scenario["resources"], "the scenario's Resources must be listed with hashes"
        for resource in scenario["resources"]:
            assert SHA256.match(resource["content_sha256"]), resource
        assert SHA256.match(manifest["player"]["prompt_hash"])
        assert manifest["player"]["prompt_version"]
        assert SHA256.match(manifest["umpire"]["prompt_hash"])
        assert manifest["umpire"]["prompt_version"] and manifest["umpire"]["model"]
        assert manifest["code_version"]
        benchmark = manifest["benchmark"]
        assert benchmark["mode"] == "batch"
        assert benchmark["experiment_id"] == batch["experiment_id"]
        assert benchmark["assignment_id"] == record["assignment_id"]
        assert benchmark["repetition_index"] == record["repetition_index"]
        assert benchmark["assignment"] == record["models"]
        assert benchmark["rotation_policy"] == rotation_policy
        assert benchmark["benchmark_mode"] == batch["benchmark_mode"]
        assert benchmark["seed_policy"] == SEED_POLICY
        assert manifest["player"]["assignments"] == record["models"]
        # AgentRun metadata: every Seat ran under its assigned model with comparable fields
        runs = export["agent_runs"]
        assert runs
        for actor_id in actor_ids:
            actor_runs = [run for run in runs if run["actor_id"] == actor_id]
            assert actor_runs, actor_id
            for run in actor_runs:
                assert run["model_requested"] == record["models"][actor_id]
                assert run["prompt_version"] == manifest["player"]["prompt_version"]
                assert run["prompt_hash"] == manifest["player"]["prompt_hash"]
                for key in (
                    "provider", "input_tokens", "output_tokens", "tool_calls",
                    "invalid_tool_calls", "stalls", "completed", "model_parameters",
                    "agent_framework", "agent_framework_version", "tool_schema_version",
                ):
                    assert key in run, key
        # fixed Umpire metadata constant: the sealed condition matched, and the manifests agree
        assert record["umpire_condition_match"] is True
        assert record["condition_differences"] == []
        umpire_conditions.append(manifest["umpire"])
    assert all(condition == umpire_conditions[0] for condition in umpire_conditions)
    assert batch["umpire_condition"]["provenance"] == umpire_conditions[0]
    assert SHA256.match(batch["umpire_condition_sha256"])
    assert batch["integrity"]["umpire_fixed"] is True

    # --- CSV/JSON outputs produced -------------------------------------------------------------
    tables = read_tables(batch_dir)
    games = tables["games"][1]
    assert len(games) == schedule["total_games"]
    by_game = {row["game_id"]: row for row in games if row["game_id"]}
    for record, export in completed:
        row = by_game[record["game_id"]]
        assert row["completed"] == "true" and row["status"] == "completed"
        assert row["seed"] == str(record["seed"])
        assert row["assignment_id"] == record["assignment_id"]
        assert row["repetition_index"] == str(record["repetition_index"])
        assert row["scenario_content_hash"] == export["run_manifest"]["scenario"]["content_sha256"]
        assert row["umpire_model"] == export["run_manifest"]["umpire"]["model"]
        assert row["rotation_policy"] == rotation_policy
        assert row["umpire_condition_match"] == "true"
    for record, _ in failed:
        row = next(r for r in games if r["attempt_position"] == str(record["position"]))
        assert row["completed"] == "false" and row["status"] == "failed"
        assert row["failure_category"] == record["failure_category"]
    agents = tables["agents"][1]
    assert len(agents) >= len(completed) * len(actor_ids)
    for record, _ in completed:
        rows = [row for row in agents if row["game_id"] == record["game_id"]]
        assert {row["actor_id"] for row in rows} == set(actor_ids)
        for row in rows:
            assert row["model_requested"] == record["models"][row["actor_id"]]
            assert row["assignment_id"] == record["assignment_id"]
    assert tables["arguments"][1], "completed games must have adjudicated Arguments"

    # --- testing.md §24: no API key or bearer token anywhere on disk ------------------------
    for path in batch_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for secret in secrets:
                assert secret not in text, path
    return batch


# --- the offline smoke: real CLI, faked provider boundary ------------------------------------


def _install_offline_provider(monkeypatch: pytest.MonkeyPatch, *, fail_at_construction: int):
    """Replace the two provider seams the CLI composes — the player model and the Umpire — and
    make the ``fail_at_construction``-th player construction (0-based, over the whole batch)
    raise, so exactly one attempt fails inside a created game."""
    from agents.player import ReferencePlayer
    from agents.scripted import deterministic_umpire
    from tests.agent_fakes import default_policy, model_from_policy

    constructions = {"count": 0}

    def fake_player(tools, *, actor_id, actor_name, config, settings, prompt=None):  # noqa: ANN001
        index = constructions["count"]
        constructions["count"] += 1
        if index == fail_at_construction:
            raise RuntimeError(f"injected player failure for Bearer {ADMIN_TOKEN}")
        return ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_policy(default_policy), config=config,
        )

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            return deterministic_umpire()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.player.ReferencePlayer.for_openrouter", staticmethod(fake_player))
    return constructions


def _run_benchmark_cli(args: list[str]):
    return CliRunner().invoke(amaw_cli.app, ["benchmark", *args])


@pytest.mark.slow
def test_t060_001_offline_batch_smoke_walks_the_section_22_checklist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.scenarios import load_scenario

    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setenv("AMAW_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    actor_ids = list(load_scenario(FIXTURE_DIR).game.turn_order)
    models = ["fake/x", "fake/y"]
    # 2 models × cyclic over 3 Seats → 2 assignments; × 2 repetitions → 4 games. Each game
    # constructs one player per Seat before play, so construction #3 is the first Seat of the
    # second executed attempt: that attempt fails inside a created game, the other three run.
    constructions = _install_offline_provider(
        monkeypatch, fail_at_construction=len(actor_ids)
    )
    out = tmp_path / "batch"

    result = _run_benchmark_cli([
        str(FIXTURE_DIR), "--output-dir", str(out),
        "--models", ",".join(models), "--rotation", "cyclic", "--repetitions", "2",
        "--benchmark-mode", "model", "--base-seed", "60000", "--scheduling-seed", "7",
        "--experiment-id", "t060-offline", "--activation-mode", "tool_loop",
    ])

    assert result.exit_code == 0, result.output
    # methodology §7: the counts are reported before execution
    assert "assignment_count=2 repetitions_per_assignment=2 total_games=4" in result.output
    assert "benchmark complete" in result.output
    assert "completed_games=3 failed_games=1 not_run_games=0" in result.output
    assert result.stdout.strip() == str(out)
    assert constructions["count"] > len(actor_ids)

    batch = _verify_section_22(
        out,
        models=models, actor_ids=actor_ids, rotation_policy="cyclic", repetitions=2,
        base_seed=60000, expected_failures=1, secrets=[ADMIN_TOKEN, FAKE_KEY],
    )
    assert batch["benchmark_mode"] == "model" and batch["experiment_id"] == "t060-offline"
    # the injected failure is retained with its partial export and redacted reason
    (failed,) = [r for r in batch["attempts"] if not r["completed"]]
    assert failed["game_id"] is not None
    assert failed["failure_category"] == "game_incompletion"
    assert failed["failure_reason"] == "RuntimeError: injected player failure for Bearer ***REDACTED***"
    export = _load(out / "games" / failed["directory"] / "export.json")
    assert export["game"]["id"] == failed["game_id"]
    # model mode (I-078): the §5.1 set was declared before the first game and every manifest,
    # the first included, was held to it and to its scheduled seed and assignment
    assert batch["expected_condition_sha256"] and batch["integrity"]["condition_fixed"] is True
    assert batch["integrity"]["attempts_as_scheduled"] is True


# --- the provider smoke: same CLI, same checklist, real models --------------------------------


@pytest.mark.llm
def test_t060_002_provider_batch_smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A small provider-backed batch (WBS: "runnable on a small model budget").

    Default budget: one model, ``--repetitions 2`` — two six-turn games under one assignment.
    ``AMAW_BATCH_SMOKE_MODELS`` (comma-separated, distinct) widens the roster and switches to
    cyclic rotation; ``AMAW_BATCH_SMOKE_REPETITIONS`` overrides the count. With
    ``AMAW_SMOKE_EXPORT_DIR`` set, the batch directory is copied there as run evidence *after*
    the batch. (The copy-in dates from I-076, when a batch's own ``batch.json``/``attempt.json``/
    CSVs under the checkout still drifted ``code_version``; those names are now recognised
    evidence under ``dev/runs`` — D-067 — and the copy is kept only so the test's own
    ``tmp_path`` sandbox stays the thing under test.)
    """
    from app.config import Settings
    from app.scenarios import load_scenario

    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN)
    if settings.openrouter_api_key is None:
        pytest.skip("no OPENROUTER_API_KEY")
    api_key = settings.openrouter_api_key.get_secret_value()
    monkeypatch.setenv("AMAW_ADMIN_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))

    roster_env = os.environ.get("AMAW_BATCH_SMOKE_MODELS", "")
    models = [m.strip() for m in roster_env.split(",") if m.strip()] or [settings.player_model]
    rotation_policy = "cyclic" if len(models) > 1 else "single"
    repetitions = int(os.environ.get("AMAW_BATCH_SMOKE_REPETITIONS", "2"))
    actor_ids = list(load_scenario(SMOKE_SCENARIO).game.turn_order)
    out = tmp_path / "t060-batch-smoke"

    result = _run_benchmark_cli([
        str(SMOKE_SCENARIO), "--output-dir", str(out),
        "--models", ",".join(models), "--rotation", rotation_policy,
        "--repetitions", str(repetitions), "--benchmark-mode", "model",
        "--base-seed", "60000", "--scheduling-seed", "7", "--experiment-id", "t060-provider",
        # the T-044 smoke condition: bounded, low-effort, generous completion budget
        "--activation-mode", "tool_loop",
        "--player-temperature", "0", "--player-reasoning-effort", "low",
        "--player-max-tokens", "4000",
        "--umpire-temperature", "0", "--umpire-reasoning-effort", "low",
        "--umpire-max-tokens", "16000",
        "--request-timeout-seconds", "600",
        "--max-llm-iterations", "8", "--max-tool-calls", "12",
        "--max-messages-per-phase", "2", "--max-activations-per-opportunity", "3",
    ])

    # Retain the evidence first — a failed live batch is exactly the run worth analysing.
    export_dir = os.environ.get("AMAW_SMOKE_EXPORT_DIR")
    if export_dir and out.is_dir():
        shutil.copytree(out, Path(export_dir) / out.name, dirs_exist_ok=True)

    assert result.exit_code == 0, result.output
    batch = _verify_section_22(
        out,
        models=models, actor_ids=actor_ids, rotation_policy=rotation_policy,
        repetitions=repetitions, base_seed=60000, expected_failures=None,
        secrets=[ADMIN_TOKEN, api_key],
    )
    assert batch["umpire"]["model"] == settings.umpire_model
    assert batch["completed_games"] >= 1
