"""I-087: wheels carry the prompts and supported scenarios required at runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROMPT_NAMES = tuple(
    path.name
    for path in sorted((ROOT / "docs" / "prompts").glob("*.md"))
    if path.name != "README.md" and not path.name.startswith("evaluator-")
)
SCENARIO_NAMES = ("northern-strait", "orison-crisis", "swedish-government")


def _runtime_asset_map() -> dict[str, Path]:
    assets = {
        f"amaw_resources/prompts/{name}": ROOT / "docs" / "prompts" / name
        for name in PROMPT_NAMES
    }
    for scenario_name in SCENARIO_NAMES:
        source = ROOT / "scenarios" / scenario_name
        runtime_files = [source / "scenario.yaml", *sorted((source / "resources").glob("*"))]
        for path in runtime_files:
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            assets[f"amaw_resources/scenarios/{scenario_name}/{relative}"] = path
    return assets


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("i087-dist")
    source = output / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(".git", ".venv", "build", "dist", "*.egg-info"),
    )
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output)],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(output.glob("amaw-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def test_i087_001_wheel_contains_required_runtime_assets(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as archive:
        names = set(archive.namelist())

    assert set(_runtime_asset_map()) <= names
    assert "amaw_resources/__init__.py" in names
    assert not any(name.startswith("dev/") for name in names)


@pytest.mark.slow
def test_i087_002_isolated_wheel_install_loads_runtime_assets(
    built_wheel: Path, tmp_path: Path
) -> None:
    from app.scenarios import load_scenario

    source = load_scenario(ROOT / "scenarios" / "orison-crisis")
    expected = {
        "scenario_hash": source.scenario_content_sha256,
        "resource_hash": source.resource_manifest_sha256,
        "turn_order": source.game.turn_order,
    }

    environment = tmp_path / "venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(environment)],
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    amaw = environment / ("Scripts/amaw.exe" if os.name == "nt" else "bin/amaw")
    dependency_paths = subprocess.run(
        [sys.executable, "-c", "import json, site; print(json.dumps(site.getsitepackages()))"],
        capture_output=True,
        text=True,
        check=True,
    )
    isolated_site = subprocess.run(
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    Path(isolated_site, "test-dependencies.pth").write_text(
        "".join(f"{path}\n" for path in json.loads(dependency_paths.stdout)), encoding="utf-8"
    )
    installed = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(python),
            str(built_wheel),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr

    clean_env = os.environ.copy()
    clean_env.pop("PYTHONPATH", None)
    help_result = subprocess.run(
        [str(amaw), "--help"], cwd=tmp_path, env=clean_env, capture_output=True, text=True
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "AMAW administrative CLI" in help_result.stdout

    smoke = """
import json
import sys
from pathlib import Path

from amaw_resources import bundled_scenario
import amaw_resources
import amaw_cli
from agents.player import PLAYER_PROMPT_VERSIONS, load_player_prompt
from agents.umpire import UMPIRE_PROMPT_VERSIONS, load_umpire_prompt
from app.database import engine_for_path, initialize_database
from app.scenarios import create_game_from_scenario, load_scenario

expected = json.loads(sys.argv[1])
assert Path(amaw_resources.__file__).is_relative_to(Path(sys.prefix))
assert Path(amaw_cli.__file__).is_relative_to(Path(sys.prefix))
for version in set(PLAYER_PROMPT_VERSIONS.values()):
    assert load_player_prompt("Actor", version=version)
for version in UMPIRE_PROMPT_VERSIONS:
    assert load_umpire_prompt(version=version)
with bundled_scenario("orison-crisis") as scenario_path:
    scenario = load_scenario(scenario_path)
    assert scenario.scenario_content_sha256 == expected["scenario_hash"]
    assert scenario.resource_manifest_sha256 == expected["resource_hash"]
    assert scenario.game.turn_order == expected["turn_order"]
    engine = engine_for_path(Path.cwd() / "isolated.db")
    initialize_database(engine)
    created = create_game_from_scenario(engine, scenario, seed=87001)
    assert created.game_id
    engine.dispose()
"""
    smoke_result = subprocess.run(
        [str(python), "-c", smoke, json.dumps(expected)],
        cwd=tmp_path,
        env=clean_env,
        capture_output=True,
        text=True,
    )
    assert smoke_result.returncode == 0, smoke_result.stdout + smoke_result.stderr


def test_i087_003_explicit_external_scenario_wins_over_bundled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from amaw_resources import resolve_scenario

    external = tmp_path / "orison-crisis"
    external.mkdir()
    marker = b"external bytes are authoritative\n"
    (external / "scenario.yaml").write_bytes(marker)
    monkeypatch.chdir(tmp_path)

    with resolve_scenario("orison-crisis") as resolved:
        assert resolved == external.resolve()
        assert (resolved / "scenario.yaml").read_bytes() == marker


def test_i087_004_packaged_asset_bytes_and_identities_are_stable() -> None:
    from amaw_resources import read_prompt, scenario_files
    from agents.player import PLAYER_PROMPT_VERSIONS, PlayerConfig, prompt_hash_for
    from app.scenarios import load_scenario

    for archive_name, source_path in _runtime_asset_map().items():
        parts = Path(archive_name).parts
        if parts[1] == "prompts":
            packaged = read_prompt(Path(parts[-1]).stem).encode()
        else:
            packaged = scenario_files(parts[2])[Path(*parts[3:]).as_posix()]
        assert packaged == source_path.read_bytes(), archive_name

    for mode, version in PLAYER_PROMPT_VERSIONS.items():
        expected = hashlib.sha256((ROOT / "docs" / "prompts" / f"{version}.md").read_bytes()).hexdigest()
        assert prompt_hash_for(PlayerConfig(model="test/model", activation_mode=mode)) == expected

    for name in SCENARIO_NAMES:
        source = load_scenario(ROOT / "scenarios" / name)
        from amaw_resources import bundled_scenario

        with bundled_scenario(name) as packaged_path:
            packaged = load_scenario(packaged_path)
        assert packaged.scenario_content_sha256 == source.scenario_content_sha256
        assert packaged.resource_manifest_sha256 == source.resource_manifest_sha256
