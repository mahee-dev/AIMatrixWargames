"""T-002 — runtime configuration and logging.

Acceptance (no TEST-* ID; exit conditions from dev/WBS.md T-002 and TRD §36):
config loads from the environment with documented defaults, and no API key or bearer
token is ever emitted to logs (testing.md §24: "Do not persist API keys or bearer tokens").
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.config import Settings, configure_logging


def _clear_amaw_env(monkeypatch):
    for var in (
        "OPENROUTER_API_KEY",
        "AMAW_DATABASE_PATH",
        "AMAW_LOG_LEVEL",
        "AMAW_LOG_FILE",
        "AMAW_REQUEST_TIMEOUT_SECONDS",
        "AMAW_PLAYER_MODEL",
        "AMAW_UMPIRE_MODEL",
        "AMAW_DIPLOMACY_CONCURRENCY",
        "AMAW_CONS_CONCURRENCY",
        "AMAW_PLAYER_ACTIVATION_MODE",
        "AMAW_PLAYER_RESEARCH_MODE",
        "AMAW_PLAYER_RESEARCH_HISTORY_MODE",
        "AMAW_MAX_MESSAGES_PER_ACTIVATION",
        "AMAW_MAX_LLM_ITERATIONS",
        "AMAW_MAX_TOOL_CALLS",
        "AMAW_MAX_MESSAGES_PER_PHASE",
        "AMAW_MAX_ACTIVATIONS_PER_OPPORTUNITY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_config_loads_defaults(monkeypatch):
    _clear_amaw_env(monkeypatch)
    # Do not read a developer's real .env during the test.
    settings = Settings(_env_file=None)

    assert settings.openrouter_api_key is None
    assert str(settings.database_path) == "data/amaw.db"
    assert settings.log_level == "INFO"
    assert settings.log_file is None
    assert settings.request_timeout_seconds == 60.0
    assert settings.player_model == "deepseek/deepseek-v4-flash-0731"
    assert settings.umpire_model == "deepseek/deepseek-v4-flash-0731"
    assert settings.diplomacy_concurrency == 4
    assert settings.cons_concurrency == 4
    assert settings.player_activation_mode == "single_shot"
    assert settings.player_research_mode == "sequential-v1"
    assert settings.player_research_history_mode == "full-v1"
    assert settings.max_messages_per_activation == 3
    assert settings.max_llm_iterations == 12
    assert settings.max_tool_calls == 20
    assert settings.max_research_tool_calls == 4
    assert settings.max_messages_per_phase == 10
    assert settings.max_activations_per_opportunity == 6


def test_config_reads_environment(monkeypatch):
    _clear_amaw_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-key-123")
    monkeypatch.setenv("AMAW_DATABASE_PATH", "/tmp/custom.db")
    monkeypatch.setenv("AMAW_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AMAW_LOG_FILE", "/tmp/amaw.log")
    monkeypatch.setenv("AMAW_REQUEST_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("AMAW_PLAYER_MODEL", "test/player")
    monkeypatch.setenv("AMAW_UMPIRE_MODEL", "test/umpire")
    monkeypatch.setenv("AMAW_CONS_CONCURRENCY", "2")
    monkeypatch.setenv("AMAW_DIPLOMACY_CONCURRENCY", "3")
    monkeypatch.setenv("AMAW_PLAYER_ACTIVATION_MODE", "tool_loop")
    monkeypatch.setenv("AMAW_PLAYER_RESEARCH_MODE", "batched-v1")
    monkeypatch.setenv("AMAW_PLAYER_RESEARCH_HISTORY_MODE", "compact-success-v1")
    monkeypatch.setenv("AMAW_MAX_MESSAGES_PER_ACTIVATION", "2")
    monkeypatch.setenv("AMAW_MAX_LLM_ITERATIONS", "24")
    monkeypatch.setenv("AMAW_MAX_TOOL_CALLS", "40")
    monkeypatch.setenv("AMAW_MAX_RESEARCH_TOOL_CALLS", "5")
    monkeypatch.setenv("AMAW_MAX_MESSAGES_PER_PHASE", "30")
    monkeypatch.setenv("AMAW_MAX_ACTIVATIONS_PER_OPPORTUNITY", "12")

    settings = Settings(_env_file=None)

    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "sk-or-secret-key-123"
    assert str(settings.database_path) == "/tmp/custom.db"
    assert settings.log_level == "DEBUG"
    assert str(settings.log_file) == "/tmp/amaw.log"
    assert settings.request_timeout_seconds == 12.5
    assert settings.player_model == "test/player"
    assert settings.umpire_model == "test/umpire"
    assert settings.cons_concurrency == 2
    assert settings.diplomacy_concurrency == 3
    assert settings.player_activation_mode == "tool_loop"
    assert settings.player_research_mode == "batched-v1"
    assert settings.player_research_history_mode == "compact-success-v1"
    assert settings.max_messages_per_activation == 2
    assert settings.max_llm_iterations == 24
    assert settings.max_tool_calls == 40
    assert settings.max_research_tool_calls == 5
    assert settings.max_messages_per_phase == 30
    assert settings.max_activations_per_opportunity == 12


def test_unknown_player_activation_mode_rejected(monkeypatch):
    _clear_amaw_env(monkeypatch)
    monkeypatch.setenv("AMAW_PLAYER_ACTIVATION_MODE", "oneshot")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_cons_concurrency_below_one_rejected(monkeypatch):
    _clear_amaw_env(monkeypatch)
    monkeypatch.setenv("AMAW_CONS_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_i028_010_diplomacy_concurrency_below_one_rejected(monkeypatch):
    _clear_amaw_env(monkeypatch)
    monkeypatch.setenv("AMAW_DIPLOMACY_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_i033_001_negative_message_budget_rejected_but_zero_is_valid(monkeypatch):
    _clear_amaw_env(monkeypatch)
    monkeypatch.setenv("AMAW_MAX_MESSAGES_PER_ACTIVATION", "0")
    assert Settings(_env_file=None).max_messages_per_activation == 0

    monkeypatch.setenv("AMAW_MAX_MESSAGES_PER_ACTIVATION", "-1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_api_key_is_secret(monkeypatch):
    _clear_amaw_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-key-123")
    settings = Settings(_env_file=None)

    # The raw secret must not appear in repr or a model dump.
    assert "sk-or-secret-key-123" not in repr(settings)
    assert "sk-or-secret-key-123" not in str(settings.model_dump())


def test_no_secret_emitted_to_logs(monkeypatch, caplog):
    """The first failing test: secrets must never reach the log output."""
    _clear_amaw_env(monkeypatch)
    api_key = "sk-or-supersecret-abcdef123456"
    bearer = "seat_bearer_9f8e7d6c5b4a3210deadbeefcafebabe"
    monkeypatch.setenv("OPENROUTER_API_KEY", api_key)
    settings = Settings(_env_file=None)

    logger = logging.getLogger("amaw.test")
    configure_logging(settings)

    with caplog.at_level(logging.DEBUG):
        # An accidental leak: code logs a message that embeds both secrets.
        logger.info("calling provider with key=%s and Authorization: Bearer %s", api_key, bearer)
        logger.debug("raw dump: %r", {"OPENROUTER_API_KEY": api_key, "token": bearer})

    emitted = "\n".join(record.getMessage() for record in caplog.records)
    formatted = "\n".join(caplog.handler.format(record) for record in caplog.records)

    assert api_key not in emitted
    assert bearer not in emitted
    assert api_key not in formatted
    assert bearer not in formatted


def test_configured_log_file_is_timestamped_and_redacted(tmp_path, caplog):
    api_key = "sk-or-file-secret-abcdef123456"
    bearer = "seat_file_secret_1234567890abcdef"
    log_path = tmp_path / "overnight" / "run.log"
    settings = Settings(
        OPENROUTER_API_KEY=api_key,
        AMAW_LOG_FILE=log_path,
        _env_file=None,
    )

    try:
        configure_logging(settings)
        logger = logging.getLogger("amaw.file-test")
        with caplog.at_level(logging.DEBUG):
            logger.info("provider key=%s Authorization: Bearer %s", api_key, bearer)
        for handler in logging.getLogger().handlers:
            handler.flush()

        text = log_path.read_text(encoding="utf-8")
        assert "INFO amaw.file-test provider key=***REDACTED*** Authorization: Bearer ***REDACTED***" in text
        assert api_key not in text
        assert bearer not in text
    finally:
        configure_logging(Settings(_env_file=None))


def test_redact_secrets_is_the_shared_scrubber():
    # T-059: the benchmark runner writes failure text to disk through the same scrubber the log
    # filter uses, so there is one redaction path (testing.md §24).
    from app.config import redact_secrets

    text = "key sk-or-abc123456789 Authorization: Bearer tok_0123456789 token seat_0123456789ab"
    scrubbed = redact_secrets(text, ["sk-or-abc123456789"])
    assert scrubbed == (
        "key ***REDACTED*** Authorization: Bearer ***REDACTED*** token ***REDACTED***"
    )
    assert redact_secrets("nothing here", []) == "nothing here"


# --- the admin token is redacted from logs ------------------------------------------------


def test_i011_admin_token_is_redacted(caplog) -> None:
    token = "admin-secret-value-123456"
    configure_logging(Settings(AMAW_ADMIN_TOKEN=token, _env_file=None))
    with caplog.at_level(logging.INFO):
        logging.getLogger("amaw.review").info("admin=%s", token)
    assert token not in "\n".join(record.getMessage() for record in caplog.records)
