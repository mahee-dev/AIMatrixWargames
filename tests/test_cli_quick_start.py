"""I-152 acceptance: one command starts any scenario (D-115).

From a checkout, ``amaw run <name>`` finds every package under ``scenarios/``, not only the
bundled ones; a ``bounded_effects_v1`` game no longer needs ``--consequence-model``, because the
resolver defaults to the Umpire model; and every run leaves its administrative export beside the
database without a second command.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

import amaw_cli
from tests.api_helpers import REPO_ROOT
from tests.cli_helpers import capture_run_cli, invoke_run
from tests.test_scenario_rules import _package

BOUNDED = "bounded_effects_v1"
MODELS = ("--player-model", "p/m", "--umpire-model", "u/m", "--seed", "1")


def _run(*args: str):
    return CliRunner().invoke(amaw_cli.app, ["run", *args, *MODELS])


def test_i152_001_every_checkout_scenario_runs_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    checkout = tmp_path / "checkout"
    _package(checkout / "scenarios", None, name="my-crisis")
    monkeypatch.chdir(checkout)

    result = _run("my-crisis")
    assert result.exit_code == 0, result.output
    assert Path(captured["runner_options"]["scenario"]) == (checkout / "scenarios/my-crisis").resolve()

    # A bundled name still resolves to the wheel copy, even where a checkout copy exists.
    _package(checkout / "scenarios", None, name="orison-crisis")
    assert _run("orison-crisis").exit_code == 0
    assert "amaw_resources" in Path(captured["runner_options"]["scenario"]).parts

    # An unknown name is refused before any runner exists, and says what would work.
    captured.clear()
    result = _run("no-such-crisis")
    assert result.exit_code == 2
    assert "runner_options" not in captured
    assert "my-crisis" in result.output and "orison-crisis" in result.output

    # From the repository root, the scenarios that are not bundled run by name. ``ai-2027`` is
    # held back from the public repository for now (D-117), so it is checked only where present.
    monkeypatch.chdir(REPO_ROOT)
    unbundled = [n for n in ("ai-2027", "skaro-crisis") if (REPO_ROOT / "scenarios" / n).is_dir()]
    assert "skaro-crisis" in unbundled
    for name in unbundled:
        result = _run(name, "--consequence-model", "c/m") if name == "skaro-crisis" else _run(name)
        assert result.exit_code == 0, result.output
        assert Path(captured["runner_options"]["scenario"]) == REPO_ROOT / "scenarios" / name


def test_i152_002_the_consequence_resolver_defaults_to_the_umpire_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    package = _package(tmp_path, None)

    result = invoke_run(package, "--consequence-mode", BOUNDED)
    assert result.exit_code == 0, result.output
    resume = captured["runner_options"]["run_configuration"]["resume"]
    assert resume["consequence"]["model"] == "u/m"
    assert captured["resolver_options"]["model"] == "u/m"

    assert invoke_run(package, "--consequence-mode", BOUNDED, "--consequence-model", "c/m").exit_code == 0
    assert captured["runner_options"]["run_configuration"]["resume"]["consequence"]["model"] == "c/m"

    captured.clear()
    result = invoke_run(package, "--consequence-model", "c/m")
    assert result.exit_code == 2
    assert "runner_options" not in captured


def test_i152_003_a_run_writes_its_export_beside_the_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_run_cli(tmp_path, monkeypatch)
    package = _package(tmp_path, None)

    result = CliRunner().invoke(
        amaw_cli.app, ["run", str(package), *MODELS]
    )
    assert result.exit_code == 0, result.output
    default = tmp_path / "exports" / "g.json"
    assert result.stdout.strip() == "g"
    assert str(default) in result.stderr
    assert json.loads(default.read_text()) == {"game": {"id": "g"}}

    shutil.rmtree(tmp_path / "exports")
    explicit = tmp_path / "chosen.json"
    assert invoke_run(package, "--export-path", str(explicit)).exit_code == 0
    assert explicit.is_file() and not (tmp_path / "exports").exists()
