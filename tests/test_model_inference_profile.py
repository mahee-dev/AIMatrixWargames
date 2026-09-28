"""I-032: a declared per-phase inference profile, sealed as provenance."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine

import amaw_cli
from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.model_client import OpenRouterClient
from agents.inference_profile import (
    INFERENCE_PROFILE_UNIFORM,
    InferenceProfile,
    InferenceProfileError,
)
from agents.player import (
    INFERENCE_PROFILE_SCHEMA,
    PlayerConfig,
    ReferencePlayer,
    player_model_settings,
)
from agents.tools import PlayerTools
from agents.umpire import AIUmpire
from app import export as export_module, game_service, resumption
from app.api import create_app
from app.config import Settings
from app.database import session_scope
from app.models import RunManifest
from app.summary import build_game_summary
from tests.agent_fakes import default_decision_policy, extract_packet
from tests.api_helpers import ACTORS, seed, start_to_diplomacy


ADMIN_TOKEN = "i-032-admin"


def _document(**overrides) -> dict:
    document = {"schema": INFERENCE_PROFILE_SCHEMA, "name": "cheap-diplomacy"}
    document.update(overrides)
    return document


def _player(engine: Engine, creation, model, **config_overrides) -> ReferencePlayer:  # noqa: ANN001
    tools = PlayerTools(
        TestClient(create_app(engine=engine), raise_server_exceptions=False),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    return ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=model,
        config=PlayerConfig(model="fake/i032", **config_overrides),
    )


def test_i032_001_no_profile_is_uniform_and_invalid_profiles_fail_closed() -> None:
    config = PlayerConfig(model="fake/i032", reasoning_effort="high", max_tokens=4000)
    assert config.inference_profile is INFERENCE_PROFILE_UNIFORM
    assert config.inference_profile.name == "uniform"
    for phase in ("diplomacy", "argument", "cons"):
        assert config.resolve_inference(phase) == {
            "reasoning_effort": "high", "max_tokens": 4000,
        }
    # A phase the profile does not mention inherits the flat setting; a mentioned one overrides it.
    profile = InferenceProfile.from_document(_document(
        phases={"diplomacy": {"reasoning_effort": "low", "max_tokens": 800}},
        umpire={"reasoning_effort": "medium"},
    ))
    partial = PlayerConfig(
        model="fake/i032", reasoning_effort="high", max_tokens=4000, inference_profile=profile,
    )
    assert partial.resolve_inference("diplomacy") == {
        "reasoning_effort": "low", "max_tokens": 800,
    }
    assert partial.resolve_inference("argument") == {
        "reasoning_effort": "high", "max_tokens": 4000,
    }
    assert profile.resolve_umpire(reasoning_effort=None, max_tokens=2000) == {
        "reasoning_effort": "medium", "max_tokens": 2000,
    }

    invalid = [
        ({"name": "x"}, "schema"),
        (_document(schema="inference-profile-v0"), "schema"),
        ({"schema": INFERENCE_PROFILE_SCHEMA}, "name"),
        (_document(name=""), "name"),
        (_document(name="uniform"), "reserved"),
        (_document(extra=1), "extra"),
        (_document(phases={"adjudication": {"reasoning_effort": "low"}}), "adjudication"),
        (_document(phases={"cons": {"temperature": 0.1}}), "temperature"),
        (_document(phases={"cons": {"reasoning_effort": "max"}}), "reasoning_effort"),
        (_document(phases={"cons": {"max_tokens": 0}}), "max_tokens"),
        (_document(phases={"cons": {"max_tokens": True}}), "max_tokens"),
        (_document(umpire={"max_tokens": -1}), "max_tokens"),
        (_document(umpire=[]), "umpire"),
    ]
    for document, match in invalid:
        with pytest.raises(InferenceProfileError, match=match):
            InferenceProfile.from_document(document)
    with pytest.raises(InferenceProfileError):
        InferenceProfile.from_document([])  # type: ignore[arg-type]


def test_i032_002_phase_resolved_settings_reach_the_provider_boundary(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    profile = InferenceProfile.from_document(_document(
        phases={"diplomacy": {"reasoning_effort": "low", "max_tokens": 800}},
    ))
    config = PlayerConfig(
        model="fake/i032", reasoning_effort="high", max_tokens=4000, inference_profile=profile,
    )
    seen: dict[str, dict] = {}

    def policy(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        packet = extract_packet(messages)
        seen[packet["phase"]] = dict(info.model_settings or {})
        return ModelResponse(parts=default_decision_policy(packet, 0, info))

    # The fake model carries the same base settings the OpenRouter model is built with, so the
    # merged per-request settings are what the provider would receive.
    model = FunctionModel(policy, settings=player_model_settings(config))
    player = _player(db_engine, creation, model, inference_profile=profile,
                     reasoning_effort="high", max_tokens=4000)

    # Diplomacy: the profile overrides both fields.
    result = player.activate()
    assert result.tool_call_names[-1] == "ready"
    assert seen["diplomacy"]["max_tokens"] == 800
    assert seen["diplomacy"]["openrouter_reasoning"] == {"effort": "low"}

    # Argument (arkania active): the profile leaves it unset — the flat settings apply.
    for actor in ACTORS[1:]:
        game_service.ready(db_engine, creation.game_id, actor)
    result = player.activate()
    assert result.tool_call_names[-1] == "make_argument"
    assert seen["argument"]["max_tokens"] == 4000
    assert seen["argument"]["openrouter_reasoning"] == {"effort": "high"}


def _coordinator(engine: Engine) -> AutonomousGameRunner:
    client = TestClient(
        create_app(
            engine=engine,
            settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
        ),
        raise_server_exceptions=False,
    )
    return AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario="tests/fixtures/deterministic-game",
        player_factory=lambda *_args: None,
    )


def _decision_model() -> FunctionModel:
    def policy(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        return ModelResponse(parts=default_decision_policy(extract_packet(messages), 0, info))

    return FunctionModel(policy)


def test_i032_003_agent_runs_and_umpire_record_phase_resolved_inference(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    profile = InferenceProfile.from_document(_document(
        phases={"diplomacy": {"reasoning_effort": "low", "max_tokens": 800}},
        umpire={"reasoning_effort": "medium"},
    ))
    player = _player(db_engine, creation, _decision_model(), inference_profile=profile,
                     reasoning_effort="high", max_tokens=4000)
    runner = LifecycleRunner(player)
    coordinator = _coordinator(db_engine)

    diplomacy = player.activate()
    coordinator._record_activation(str(creation.game_id), runner, SimpleNamespace(
        activation=diplomacy, context={"turn": 1}, phase="diplomacy",
    ))
    for actor in ACTORS[1:]:
        game_service.ready(db_engine, creation.game_id, actor)
    argument = player.activate()
    coordinator._record_activation(str(creation.game_id), runner, SimpleNamespace(
        activation=argument, context={"turn": 1}, phase="argument",
    ))
    # A failed activation is attributed to the same phase-resolved budget.
    coordinator._record_failure(str(creation.game_id), runner, SimpleNamespace(
        failure=SimpleNamespace(
            turn=1, phase="diplomacy", category="transient", counts={}, detail="boom",
            recovery_action=None, recovery_argument_id=None,
        ),
    ))

    export = export_module.build_game_export(db_engine, creation.game_id)
    by_phase = {
        (record["phase"], record["completed"]): record for record in export["agent_runs"]
    }
    diplomacy_record = by_phase[("diplomacy", True)]
    assert diplomacy_record["reasoning_configuration"] == {"effort": "low"}
    assert diplomacy_record["model_parameters"]["max_tokens"] == 800
    assert diplomacy_record["model_parameters"]["inference_profile"] == "cheap-diplomacy"
    argument_record = by_phase[("argument", True)]
    assert argument_record["reasoning_configuration"] == {"effort": "high"}
    assert argument_record["model_parameters"]["max_tokens"] == 4000
    assert argument_record["model_parameters"]["inference_profile"] == "cheap-diplomacy"
    failed_record = by_phase[("diplomacy", False)]
    assert failed_record["reasoning_configuration"] == {"effort": "low"}
    assert failed_record["model_parameters"]["max_tokens"] == 800
    # T-053's per-phase report keeps reporting *actual* reasoning usage beside the configuration.
    summary = build_game_summary(db_engine, creation.game_id)
    assert "reasoning_tokens" in summary["by_phase"]["diplomacy"]

    # The Umpire's entry resolves over the flat Umpire settings into its sealed configuration.
    umpire = AIUmpire(
        OpenRouterClient(api_key="unused"),
        model="fake/umpire",
        **profile.resolve_umpire(reasoning_effort=None, max_tokens=2000),
    )
    settings = umpire.provenance_configuration()["settings"]
    assert settings["reasoning"] == {"effort": "medium"}
    assert settings["max_tokens"] == 2000


def test_i032_004_manifest_and_resume_seal_the_resolved_profile(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    profile = InferenceProfile.from_document(_document(
        phases={"diplomacy": {"reasoning_effort": "low", "max_tokens": 800}},
        umpire={"reasoning_effort": "medium"},
    ))
    player = _player(db_engine, creation, _decision_model(), inference_profile=profile,
                     reasoning_effort="high", max_tokens=4000)
    runner = LifecycleRunner(player)
    coordinator = _coordinator(db_engine)

    configuration = coordinator._manifest_configuration({"arkania": runner})
    assert configuration["player"]["inference_profile"] == {
        "name": "cheap-diplomacy",
        "phases": {
            "diplomacy": {"reasoning_effort": "low", "max_tokens": 800},
            "argument": {"reasoning_effort": "high", "max_tokens": 4000},
            "cons": {"reasoning_effort": "high", "max_tokens": 4000},
            # I-046: an absent reply entry resolves from argument and is sealed like every phase.
            "reply": {"reasoning_effort": "high", "max_tokens": 4000},
        },
    }
    assert configuration["resume"]["inference_profile"] == profile.to_document()
    assert InferenceProfile.from_document(
        configuration["resume"]["inference_profile"], sealed=True
    ) == profile

    uniform = _player(db_engine, creation, _decision_model(),
                      reasoning_effort="high", max_tokens=4000)
    # I-083: refused through the one shared-condition projection, which names the field.
    with pytest.raises(AutonomousGameError, match="inference_profile"):
        coordinator._manifest_configuration({
            "arkania": runner, "belvar": LifecycleRunner(uniform),
        })
    uniform_configuration = coordinator._manifest_configuration({"arkania": LifecycleRunner(uniform)})
    assert uniform_configuration["player"]["inference_profile"]["name"] == "uniform"
    sealed_uniform = uniform_configuration["resume"]["inference_profile"]
    assert InferenceProfile.from_document(sealed_uniform, sealed=True) is INFERENCE_PROFILE_UNIFORM
    # The reserved name is accepted only as a sealed recipe, and only when it is genuinely flat.
    with pytest.raises(InferenceProfileError, match="reserved"):
        InferenceProfile.from_document(sealed_uniform)
    with pytest.raises(InferenceProfileError, match="uniform"):
        InferenceProfile.from_document(
            {**sealed_uniform, "phases": {"cons": {"reasoning_effort": "low"}}}, sealed=True
        )


def test_i032_004_resumption_refuses_an_invalid_sealed_profile(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    player = _player(db_engine, creation, _decision_model())
    coordinator = _coordinator(db_engine)
    configuration = coordinator._manifest_configuration({
        actor: LifecycleRunner(player) for actor in ACTORS
    })
    configuration["resume"]["player"]["assignments"] = {actor: "fake/i032" for actor in ACTORS}
    configuration["resume"]["umpire"] = {
        "model": "fake/umpire", "temperature": None, "max_tokens": None, "timeout": None,
        "reasoning_effort": None,
    }

    def manifest_with(inference_profile: object) -> dict:
        manifest = json.loads(json.dumps(configuration))
        manifest["resume"]["inference_profile"] = inference_profile
        return manifest

    def seal(manifest: dict) -> None:
        with session_scope(db_engine) as session:
            existing = session.get(RunManifest, creation.game_id)
            if existing is not None:
                session.delete(existing)
                session.flush()
            session.add(RunManifest(
                game_id=creation.game_id,
                manifest=manifest,
                manifest_sha256=hashlib.sha256(
                    json.dumps(manifest, sort_keys=True).encode()
                ).hexdigest(),
            ))

    seal(manifest_with({"schema": "inference-profile-v0", "name": "stale"}))
    with pytest.raises(resumption.ResumeError, match="inference profile"):
        resumption.describe(db_engine, creation.game_id)

    # A legacy manifest without the key resumes as uniform; a valid sealed one resumes as itself.
    legacy = json.loads(json.dumps(configuration))
    del legacy["resume"]["inference_profile"]
    seal(legacy)
    assert resumption.describe(db_engine, creation.game_id).manifest["resume"].get(
        "inference_profile"
    ) is None
    seal(manifest_with(INFERENCE_PROFILE_UNIFORM.to_document()))
    assert resumption.describe(db_engine, creation.game_id).manifest["resume"][
        "inference_profile"
    ]["name"] == "uniform"


def _cli_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    monkeypatch.delenv("AMAW_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AMAW_INFERENCE_PROFILE", raising=False)
    captured: dict[str, object] = {}

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            captured["umpire"] = kwargs
            return cls()

    class FakeRunner:
        last_game_id = None

        def __init__(self, client, **kwargs):  # noqa: ANN001
            captured["runner"] = kwargs
            self.last_game_id = None

        def run(self):
            return SimpleNamespace(game_id="created", export={})

        def resume(self, value: str):
            captured["resumed"] = value
            return SimpleNamespace(game_id=value, export={})

    class FakePlayer:
        @classmethod
        def for_openrouter(cls, tools, *, actor_id, actor_name, config, settings, prompt=None):  # noqa: ANN001
            captured["player"] = config
            return cls()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)
    return captured


def _scenario_package(tmp_path: Path) -> Path:
    package = tmp_path / "example-scenario"
    package.mkdir()
    (package / "scenario.yaml").write_text("scenario: {}\n", encoding="utf-8")
    return package


def test_i032_004_cli_loads_a_profile_and_resolves_the_umpire_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _cli_fakes(monkeypatch, tmp_path)
    package = _scenario_package(tmp_path)
    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        "schema: inference-profile-v1\n"
        "name: cheap-diplomacy\n"
        "phases:\n"
        "  diplomacy: {reasoning_effort: low, max_tokens: 800}\n"
        "  cons: {reasoning_effort: minimal}\n"
        "umpire: {reasoning_effort: medium}\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(amaw_cli.app, [
        "run", str(package), "--inference-profile", str(profile_path),
        "--player-reasoning-effort", "high", "--player-max-tokens", "4000",
        "--umpire-max-tokens", "16000",
    ])

    assert result.exit_code == 0, result.output
    recipe = captured["runner"]["run_configuration"]["resume"]
    assert recipe["inference_profile"]["name"] == "cheap-diplomacy"
    assert recipe["inference_profile"]["phases"]["cons"] == {
        "reasoning_effort": "minimal", "max_tokens": None,
    }
    # The flat Umpire values stay in the recipe; the profile's entry resolves over them.
    assert recipe["umpire"]["reasoning_effort"] is None
    assert recipe["umpire"]["max_tokens"] == 16000
    assert captured["umpire"]["reasoning_effort"] == "medium"
    assert captured["umpire"]["max_tokens"] == 16000
    captured["runner"]["player_factory"](object(), "arkania", "Arkania")
    config = captured["player"]
    assert config.inference_profile.name == "cheap-diplomacy"
    assert config.resolve_inference("diplomacy") == {"reasoning_effort": "low", "max_tokens": 800}
    assert config.resolve_inference("argument") == {"reasoning_effort": "high", "max_tokens": 4000}

    # Same document via the environment.
    captured.clear()
    monkeypatch.setenv("AMAW_INFERENCE_PROFILE", str(profile_path))
    result = CliRunner().invoke(amaw_cli.app, ["run", str(package)])
    assert result.exit_code == 0, result.output
    assert captured["runner"]["run_configuration"]["resume"]["inference_profile"]["name"] == (
        "cheap-diplomacy"
    )

    # No profile at all: the sealed recipe says so explicitly.
    captured.clear()
    monkeypatch.delenv("AMAW_INFERENCE_PROFILE")
    result = CliRunner().invoke(amaw_cli.app, ["run", str(package)])
    assert result.exit_code == 0, result.output
    assert captured["runner"]["run_configuration"]["resume"]["inference_profile"] == (
        INFERENCE_PROFILE_UNIFORM.to_document()
    )
    assert captured["umpire"]["reasoning_effort"] is None


def test_i032_001_cli_rejects_an_invalid_profile_before_any_game_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _cli_fakes(monkeypatch, tmp_path)
    package = _scenario_package(tmp_path)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(
        "schema: inference-profile-v1\nname: bad\nphases:\n  adjudication: {reasoning_effort: low}\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(amaw_cli.app, ["run", str(package), "--inference-profile", str(invalid)])
    assert result.exit_code == 2
    assert "adjudication" in result.output
    assert "runner" not in captured and "umpire" not in captured
    assert not (tmp_path / "amaw.db").exists()

    result = CliRunner().invoke(
        amaw_cli.app, ["run", str(package), "--inference-profile", str(tmp_path / "missing.yaml")]
    )
    assert result.exit_code == 2
    assert "runner" not in captured


def test_i032_004_cli_resume_reconstructs_the_sealed_profile_and_refuses_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    from agents.guardrails import GuardrailConfig

    captured = _cli_fakes(monkeypatch, tmp_path)
    game_id = str(uuid.uuid4())
    profile = InferenceProfile.from_document(_document(
        phases={"diplomacy": {"reasoning_effort": "low"}},
        umpire={"reasoning_effort": "medium", "max_tokens": 9000},
    ))
    recipe = {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "provider/original"},
            "temperature": None, "max_tokens": 4000, "timeout": 222.0,
            "reasoning_effort": "high", "activation_mode": "single_shot",
            "prompt_version": "player-v1.7",
            "guardrails": dataclasses.asdict(GuardrailConfig()),
        },
        "umpire": {
            "model": "provider/umpire", "temperature": None, "max_tokens": 16000,
            "timeout": 222.0, "reasoning_effort": None,
        },
        "inference_profile": profile.to_document(),
        "coordinator": {"max_cycles": 7, "cons_concurrency": 1, "diplomacy_concurrency": 1},
    }
    runtime = {
        "request_timeout_seconds": 222.0, "max_cycles": 7, "log_level": "INFO",
        "stream_game_details": False, "guardrails": recipe["player"]["guardrails"],
    }
    monkeypatch.setattr(
        "app.resumption.describe",
        lambda engine, parsed: SimpleNamespace(manifest={"resume": recipe, "runtime": runtime}),
    )

    result = CliRunner().invoke(
        amaw_cli.app, ["run", "--resume-game", game_id, "--inference-profile", "x.yaml"]
    )
    assert result.exit_code == 2
    assert "--inference-profile" in result.output

    result = CliRunner().invoke(amaw_cli.app, ["run", "--resume-game", game_id])
    assert result.exit_code == 0, result.output
    assert captured["resumed"] == game_id
    assert captured["runner"]["run_configuration"]["resume"]["inference_profile"] == (
        profile.to_document()
    )
    assert captured["umpire"]["reasoning_effort"] == "medium"
    assert captured["umpire"]["max_tokens"] == 9000
    captured["runner"]["player_factory"](object(), "arkania", "Arkania")
    assert captured["player"].inference_profile == profile
    assert captured["player"].resolve_inference("diplomacy") == {
        "reasoning_effort": "low", "max_tokens": 4000,
    }
