"""T-073: first-class scenario adjudication rules (D-090, scenario-format §11.1, agents.md §21).

Rules are public, non-executable scenario input: validated and hashed with ``scenario.yaml``,
snapshotted onto the Game, handed to the Umpire verbatim as ``scenario_rules`` and recorded in
the administrative export. A rule-free scenario is unchanged. Keyed to T-072 §8.1.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy import Engine
from sqlmodel import select

from agents.model_client import OpenRouterClient
from agents.scripted import build_scripted_players, deterministic_umpire, run_deterministic_game
from agents.umpire import (
    UMPIRE_PROMPT_VERSION_RULES,
    UMPIRE_PROMPT_VERSIONS,
    AIUmpire,
    FakeUmpire,
    load_umpire_prompt,
)
from app import context, export, game_service, scenarios
from app.database import session_scope
from app.models import AdjudicationRecord, Argument, Effect, Game
from app.umpire import AdjudicationContext, UmpireUnavailable
from tests.api_helpers import FIXTURE_DIR, REPO_ROOT
from tests.cli_helpers import capture_run_cli, invoke_run

RULES = [
    {
        "id": "no-overflight",
        "text": "Belvar cannot authorise foreign military overflight without a parliamentary vote.",
    },
    {
        "id": "port-authority",
        "text": "Arkania's port authority may close its own ports without further approval.",
    },
]


def _rules_yaml(rules: list[dict]) -> str:
    lines = ["", "rules:"]
    for rule in rules:
        lines.append(f"  - id: {json.dumps(rule['id'])}")
        lines.append(f"    text: {json.dumps(rule['text'])}")
    return "\n".join(lines) + "\n"


def _package(tmp_path: Path, rules: list[dict] | None = RULES, *, name: str = "pkg") -> Path:
    """A copy of the canonical fixture, optionally with a ``rules`` block appended."""
    package = tmp_path / name
    shutil.copytree(FIXTURE_DIR, package)
    if rules is not None:
        manifest = package / "scenario.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + _rules_yaml(rules), encoding="utf-8"
        )
    return package


def _play(engine: Engine, package: Path) -> tuple[scenarios.GameCreation, list[AdjudicationContext]]:
    """Create, start and play a full deterministic game, recording every Umpire context."""
    loaded = scenarios.load_scenario(package)
    creation = scenarios.create_game_from_scenario(engine, loaded)
    game_service.start_game(engine, creation.game_id)
    inner = deterministic_umpire()
    seen: list[AdjudicationContext] = []

    def responder(ctx: AdjudicationContext):
        seen.append(ctx)
        return inner.adjudicate(ctx)

    players = build_scripted_players(loaded.game.turn_order)
    run_deterministic_game(engine, creation.game_id, players, FakeUmpire(responder=responder))
    return creation, seen


# --- TEST-T073-001: a scenario declares bounded rules ------------------------------------


def test_t073_001_scenario_declares_bounded_rules(tmp_path: Path) -> None:
    loaded = scenarios.load_scenario(_package(tmp_path))
    assert [rule.model_dump() for rule in loaded.rules] == RULES
    assert scenarios.load_scenario(FIXTURE_DIR).rules == []


@pytest.mark.parametrize(
    ("rules_block", "fragment"),
    [
        (_rules_yaml([RULES[0], RULES[0]]), "duplicate rule id"),
        (_rules_yaml([{"id": "Bad Id", "text": "x"}]), "id"),
        (_rules_yaml([{"id": "blank", "text": "   "}]), "text"),
        (_rules_yaml([{"id": "marker", "text": "TODO decide this"}]), "unresolved"),
        (_rules_yaml([{"id": "long", "text": "x" * 1001}]), "text"),
        (
            _rules_yaml([{"id": f"r{i}", "text": "A rule."} for i in range(33)]),
            "rules",
        ),
        (
            "\nrules:\n  - id: secret\n    text: Hidden.\n    visibility: private\n"
            "    visible_to: [arkania]\n",
            "visibility",
        ),
        ("\nrules:\n  - text: No id.\n", "id"),
    ],
)
def test_t073_001_invalid_rules_are_rejected(
    tmp_path: Path, rules_block: str, fragment: str
) -> None:
    package = _package(tmp_path, rules=None)
    manifest = package / "scenario.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + rules_block, encoding="utf-8")
    with pytest.raises(scenarios.ScenarioValidationError) as excinfo:
        scenarios.load_scenario(package)
    assert fragment in str(excinfo.value)


# --- TEST-T073-002: every adjudication receives exactly the snapshotted rules ------------


def test_t073_002_umpire_receives_the_snapshotted_rules_on_every_adjudication(
    db_engine: Engine, tmp_path: Path
) -> None:
    package = _package(tmp_path)
    loaded = scenarios.load_scenario(package)
    creation = scenarios.create_game_from_scenario(db_engine, loaded)
    # The snapshot, not the package, is authoritative: editing the file after creation changes
    # nothing the Umpire sees.
    manifest = package / "scenario.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("parliamentary vote", "royal decree"),
        encoding="utf-8",
    )
    with session_scope(db_engine) as session:
        assert session.get(Game, creation.game_id).scenario_rules == RULES
    game_service.start_game(db_engine, creation.game_id)
    seen: list[AdjudicationContext] = []
    inner = deterministic_umpire()
    players = build_scripted_players(loaded.game.turn_order)
    run_deterministic_game(
        db_engine,
        creation.game_id,
        players,
        FakeUmpire(responder=lambda ctx: seen.append(ctx) or inner.adjudicate(ctx)),
    )
    assert len(seen) == 18  # 6 turns x 3 Actors
    for ctx in seen:
        # Arguments whose Pros never mention any rule still receive every rule, in order.
        assert not any(rule["id"] in " ".join(ctx.argument["pros"]) for rule in RULES)
        assert ctx.scenario_rules == RULES
        # A separate field: the summary is exactly what a rule-free scenario produces.
        assert ctx.scenario_summary == loaded.scenario.description


# --- TEST-T073-003: rules are part of scenario identity and provenance ------------------


def test_t073_003_rules_are_hash_affecting_and_exported(
    db_engine: Engine, tmp_path: Path
) -> None:
    base = scenarios.load_scenario(_package(tmp_path, name="a"))
    text_changed = scenarios.load_scenario(
        _package(tmp_path, [RULES[0] | {"text": "Belvar may authorise overflight."}, RULES[1]], name="b")
    )
    id_changed = scenarios.load_scenario(
        _package(tmp_path, [RULES[0] | {"id": "overflight"}, RULES[1]], name="c")
    )
    rule_free = scenarios.load_scenario(FIXTURE_DIR)
    hashes = {
        s.scenario_content_sha256 for s in (base, text_changed, id_changed, rule_free)
    }
    assert len(hashes) == 4

    creation = scenarios.create_game_from_scenario(db_engine, base)
    record = export.build_game_export(db_engine, creation.game_id)
    # Format 14 introduced game.scenario_rules; later formats keep it (T-074 moved to 15).
    assert int(export.EXPORT_FORMAT_VERSION) >= 14
    assert record["export_format_version"] == export.EXPORT_FORMAT_VERSION
    assert record["game"]["scenario_rules"] == RULES

    plain = scenarios.create_game_from_scenario(db_engine, rule_free)
    assert export.build_game_export(db_engine, plain.game_id)["game"]["scenario_rules"] == []


# --- TEST-T073-004: rules are not executable -------------------------------------------


def _outcome(engine: Engine, game_id) -> dict:
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        effects = session.exec(
            select(Effect).where(Effect.game_id == game_id).order_by(Effect.created_turn, Effect.description)
        ).all()
        records = session.exec(
            select(Argument, AdjudicationRecord)
            .join(AdjudicationRecord, AdjudicationRecord.argument_id == Argument.id)
            .where(Argument.game_id == game_id)
            .order_by(Argument.turn, Argument.actor_id)
        ).all()
        return {
            "status": game.status,
            "turn": game.turn,
            "rng_draw_index": game.rng_draw_index,
            "effects": [
                (e.created_turn, e.description, e.visibility, e.active) for e in effects
            ],
            "adjudications": [
                (a.turn, a.actor_id, r.mode, r.probability, r.roll, r.success)
                for a, r in records
            ],
        }


def test_t073_004_rules_alone_create_no_effect_probability_or_draw(
    db_engine: Engine, tmp_path: Path
) -> None:
    with_rules, contexts = _play(db_engine, _package(tmp_path, name="rules"))
    without_rules, plain_contexts = _play(db_engine, _package(tmp_path, None, name="plain"))
    assert _outcome(db_engine, with_rules.game_id) == _outcome(db_engine, without_rules.game_id)
    # The only difference the Umpire ever sees is the rules field itself.
    for ruled, plain in zip(contexts, plain_contexts, strict=True):
        assert ruled.scenario_rules == RULES and plain.scenario_rules is None
        assert (ruled.argument, ruled.cons, ruled.relevant_hidden_information) == (
            plain.argument, plain.cons, plain.relevant_hidden_information
        )


# --- TEST-T073-005: behaviour unchanged without rules ------------------------------------


_RELEASED_PAYLOAD_KEYS = [
    "scenario_summary",
    "turn",
    "turn_length",
    "argument",
    "cons",
    "relevant_effects",
    "relevant_hidden_information",
]


def _offline_umpire(prompt_version: str, requests: list | None = None) -> AIUmpire:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(500)

    client_ = OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler))
    return AIUmpire(client_, model="test/umpire", prompt_version=prompt_version)


def test_t073_005_rule_free_scenarios_are_byte_identical(db_engine: Engine) -> None:
    creation, contexts = _play(db_engine, FIXTURE_DIR)
    assert contexts and all(ctx.scenario_rules is None for ctx in contexts)
    for version in ("umpire-v1.3", "umpire-v1.4"):
        messages = _offline_umpire(version)._render_messages(contexts[0])
        payload = json.loads(messages[1]["content"])
        assert list(payload) == _RELEASED_PAYLOAD_KEYS
    packet = context.actor_context(db_engine, creation.game_id, "arkania")
    assert set(packet["scenario"]) == {"id", "version", "summary"}


# --- TEST-T073-006: the rules-aware Umpire prompt condition ------------------------------


def _rules_context() -> AdjudicationContext:
    return AdjudicationContext(
        scenario_summary="A regional standoff.",
        turn=1,
        turn_length="1 week",
        argument={"actor_id": "arkania", "action": "Act", "desired_effect": "Effect", "pros": []},
        cons=[],
        relevant_effects=[],
        relevant_hidden_information=[],
        scenario_rules=RULES,
    )


def test_t073_006_rules_require_the_rules_aware_umpire_prompt() -> None:
    assert UMPIRE_PROMPT_VERSION_RULES == "umpire-v1.5"
    assert UMPIRE_PROMPT_VERSION_RULES in UMPIRE_PROMPT_VERSIONS
    prompt = load_umpire_prompt(version="umpire-v1.5")
    assert prompt == (REPO_ROOT / "docs/prompts/umpire-v1.5.md").read_text(encoding="utf-8")
    assert "scenario_rules" in prompt and "owner_reply" in prompt
    for released in ("umpire-v1.3", "umpire-v1.4"):
        assert "scenario_rules" not in load_umpire_prompt(version=released)

    # v1.5 renders the rules verbatim as their own field.
    payload = json.loads(_offline_umpire("umpire-v1.5")._render_messages(_rules_context())[1]["content"])
    assert payload["scenario_rules"] == RULES
    assert payload["scenario_summary"] == "A regional standoff."

    # A rules context under a rules-unaware prompt is refused before any provider call.
    for released in ("umpire-v1.3", "umpire-v1.4"):
        requests: list = []
        umpire = _offline_umpire(released, requests)
        with pytest.raises(UmpireUnavailable, match="scenario_rules"):
            umpire.adjudicate(_rules_context())
        result = umpire.adjudicate_recorded(_rules_context())
        assert result.proposal is None and "scenario_rules" in (result.failure or "")
        assert requests == []


def test_t073_006_cli_resolves_and_refuses_the_umpire_prompt_for_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    ruled = _package(tmp_path, name="ruled")
    plain = _package(tmp_path, None, name="plain")

    # The rules default is the rules-aware umpire-v1.7, as for every game (D-114).
    result = invoke_run(ruled)
    assert result.exit_code == 0, result.output
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"
    assert captured["runner_options"]["run_configuration"]["resume"]["umpire"][
        "prompt_version"
    ] == "umpire-v1.7"

    result = invoke_run(ruled, "--con-reply-mode", "visible_reply", "--reply-max-points", "2")
    assert result.exit_code == 0, result.output
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"

    # A rule-free scenario shares the default and may pin v1.5 explicitly.
    result = invoke_run(plain)
    assert result.exit_code == 0, result.output
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.7"
    result = invoke_run(plain, "--umpire-prompt-version", "umpire-v1.5")
    assert result.exit_code == 0, result.output
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.5"

    for released in ("umpire-v1.3", "umpire-v1.4"):
        captured.clear()
        result = invoke_run(ruled, "--umpire-prompt-version", released)
        assert result.exit_code == 2, result.output
        assert "rules" in result.output
        assert "runner_options" not in captured


# --- TEST-T073-007: rules are public and leak nothing private ----------------------------


def test_t073_007_rules_are_public_and_leak_no_private_content(
    db_engine: Engine, tmp_path: Path
) -> None:
    package = _package(tmp_path)
    creation, contexts = _play(db_engine, package)
    for actor_id in ("arkania", "belvar", "cestia"):
        packet = context.actor_context(db_engine, creation.game_id, actor_id)
        assert packet["scenario"]["rules"] == RULES

    private_text = (package / "resources" / "arkania-intel.md").read_text(encoding="utf-8")
    snippets = [line.strip() for line in private_text.splitlines() if len(line.strip()) > 20]
    assert snippets
    payload = _offline_umpire("umpire-v1.5")._render_messages(contexts[0])[1]["content"]
    rules_json = json.dumps(contexts[0].scenario_rules)
    for snippet in snippets:
        assert snippet not in payload
        assert snippet not in rules_json
