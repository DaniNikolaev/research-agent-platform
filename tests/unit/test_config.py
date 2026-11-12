"""Configuration contract: safe placeholders, no secrets, bounded retries."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from research_platform.agents.agent_1a.contracts.graph_state import RETRY_COUNTER_KEYS
from research_platform.config import (
    ENVIRONMENT_OVERRIDES,
    Environment,
    PlatformSettings,
    SettingsError,
    StorageSettings,
    WorkflowSettings,
    load_settings,
)


def test_example_configuration_files_are_valid(repo_root: Path) -> None:
    settings = load_settings(
        application_path=repo_root / "config" / "application.example.yaml",
        models_path=repo_root / "config" / "models.example.yaml",
    )
    assert settings.app.environment is Environment.LOCAL
    assert "extraction-primary" in settings.models.profiles
    assert settings.privacy.allow_document_content_in_trace is False
    assert settings.observability.exporter == "none"


def test_retry_limits_match_the_graph_state_counters() -> None:
    limits = WorkflowSettings().retry_limits
    assert tuple(limits) == RETRY_COUNTER_KEYS
    assert all(value >= 0 for value in limits.values())


def test_dsn_reference_must_be_a_variable_name_not_a_dsn() -> None:
    with pytest.raises(ValidationError) as excinfo:
        StorageSettings(database_dsn_reference="postgresql://user:password@localhost:5432/db")
    assert "leak the password" in str(excinfo.value)


def test_artifact_root_must_use_a_uri_scheme() -> None:
    with pytest.raises(ValidationError):
        StorageSettings(artifact_root_uri="D:\\MultiAgentPipeline\\storage")


def test_document_content_requires_prompt_content_permission() -> None:
    with pytest.raises(ValidationError):
        PlatformSettings.model_validate(
            {
                "privacy": {
                    "allow_prompt_content_in_trace": False,
                    "allow_document_content_in_trace": True,
                }
            }
        )


def test_environment_overrides_are_applied(tmp_path: Path) -> None:
    application = tmp_path / "application.yaml"
    application.write_text("app:\n  environment: local\n  log_level: INFO\n", encoding="utf-8")
    settings = load_settings(
        application_path=application,
        environ={
            "RESEARCH_PLATFORM_ENVIRONMENT": "ci",
            "RESEARCH_PLATFORM_LOG_LEVEL": "WARNING",
            "RESEARCH_PLATFORM_ARTIFACT_ROOT": "artifact://ci/artifacts",
        },
    )
    assert settings.app.environment is Environment.CI
    assert settings.app.log_level.value == "WARNING"
    assert settings.storage.artifact_root_uri == "artifact://ci/artifacts"


def test_unknown_environment_override_name_is_not_documented() -> None:
    assert set(ENVIRONMENT_OVERRIDES) == {
        "RESEARCH_PLATFORM_ENVIRONMENT",
        "RESEARCH_PLATFORM_LOG_LEVEL",
        "RESEARCH_PLATFORM_ARTIFACT_ROOT",
        "RESEARCH_PLATFORM_DB_DSN_REFERENCE",
        "RESEARCH_PLATFORM_DATA_ROOT",
        "RESEARCH_PLATFORM_LOG_DIR",
    }


def test_missing_configuration_file_reports_a_settings_error(tmp_path: Path) -> None:
    with pytest.raises(SettingsError):
        load_settings(application_path=tmp_path / "absent.yaml")


def test_non_mapping_configuration_file_is_rejected(tmp_path: Path) -> None:
    broken = tmp_path / "list.yaml"
    broken.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(application_path=broken)


def test_unknown_configuration_key_is_rejected(tmp_path: Path) -> None:
    extra = tmp_path / "application.yaml"
    extra.write_text("app:\n  unknown_key: 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(application_path=extra)


def test_example_env_file_contains_no_real_secret(repo_root: Path) -> None:
    text = (repo_root / ".env.example").read_text(encoding="utf-8")
    for marker in ("sk-live", "-----BEGIN", "AKIA"):
        assert marker not in text
    assert "CHANGE_ME" in text


def test_example_model_profiles_have_no_literal_key(repo_root: Path) -> None:
    text = (repo_root / "config" / "models.example.yaml").read_text(encoding="utf-8")
    assert "api_key:" not in text
    assert "api_key_env:" in text
