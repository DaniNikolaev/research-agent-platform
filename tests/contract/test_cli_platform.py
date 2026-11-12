"""CLI commands added in Iteration 1: config validate, platform check, workflow-smoke."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_platform.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_CONTRACT_VIOLATION,
    EXIT_OK,
    main,
)

VALID_APPLICATION = """
app:
  environment: ci
  log_level: INFO
paths:
  data_root: data
  log_dir: logs
"""

VALID_MODELS = """
models:
  default_profile: fake-primary
  profiles:
    fake-primary:
      provider: fake_provider
      model_id: fake_model_1
      purpose: extraction
      is_fake: true
      supports_structured_output: true
      supports_json_schema: true
      max_retries: 1
"""


def project(
    tmp_path: Path, *, application: str = VALID_APPLICATION, models: str = VALID_MODELS
) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "application.yaml").write_text(application, encoding="utf-8")
    (config_dir / "models.yaml").write_text(models, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- config validate


def test_config_validate_reports_valid_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    assert main(["config", "validate", "--project-root", str(root)]) == EXIT_OK
    output = capsys.readouterr().out
    assert "valid: environment=ci" in output
    assert "fake-primary" in output


def test_config_validate_rejects_an_unknown_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, application="app:\n  unknown_key: 1\n")
    assert main(["config", "validate", "--project-root", str(root)]) == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "unknown_key" in captured.out + captured.err


def test_config_validate_rejects_a_secret_in_yaml(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(
        tmp_path, models=VALID_MODELS + "\n    literal:\n      api_key: sk-live-abcdef0123456789\n"
    )
    assert main(["config", "validate", "--project-root", str(root)]) == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "api_key" in captured.out + captured.err
    assert "abcdef0123456789" not in captured.out + captured.err


def test_config_validate_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    assert main(["config", "validate", "--project-root", str(root), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["environment"] == "ci"
    assert "fake-primary" in payload["profiles"]


def test_config_validate_without_config_files_uses_defaults(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["config", "validate", "--project-root", str(tmp_path)]) == EXIT_OK
    assert "environment=local" in capsys.readouterr().out


def test_config_requires_a_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config"]) == EXIT_CONTRACT_VIOLATION
    assert "config validate" in capsys.readouterr().err


# ------------------------------------------------------------------ platform check


def test_platform_check_passes_with_fake_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    assert main(["platform", "check", "--project-root", str(root)]) == EXIT_OK
    output = capsys.readouterr().out
    assert "python_version" in output
    assert "artifact_store" in output
    assert "PASS" in output
    # the fake profile must be visible as a warning, never silently accepted
    assert "WARNING" in output
    assert "FAKE" in output


def test_platform_check_json_lists_every_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    assert main(["platform", "check", "--project-root", str(root), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in payload["checks"]}
    assert {"python_version", "package_version", "configuration", "data_directories"} <= names
    assert all(
        item["status"] in {"PASS", "WARNING", "FAILED", "NOT_CONFIGURED"}
        for item in payload["checks"]
    )


def test_platform_requires_a_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["platform"]) == EXIT_CONTRACT_VIOLATION
    assert "platform check" in capsys.readouterr().err


# ---------------------------------------------------------------- workflow-smoke


def test_workflow_smoke_completes_and_says_it_is_a_smoke_test(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    assert main(["agent1a", "workflow-smoke", "--project-root", str(root)]) == EXIT_OK
    output = capsys.readouterr().out
    assert "SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED" in output
    assert "run_id:" in output
    assert "correlation_id:" in output


def test_workflow_smoke_json_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    assert main(["agent1a", "workflow-smoke", "--project-root", str(root), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["smoke"] is True
    assert payload["status"] in {"completed", "completed_with_warnings"}
    assert payload["run_id"].startswith("run_")
    assert payload["correlation_id"].startswith("corr_")
    assert payload["technical_trace_ref"].startswith("artifact://")
    assert "card" not in payload


def test_workflow_smoke_without_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    assert main(["agent1a", "workflow-smoke", "--project-root", str(root), "--no-model"]) == EXIT_OK
    output = capsys.readouterr().out
    assert "warning:" in output


def test_workflow_smoke_writes_an_artifact_under_the_project(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert main(["agent1a", "workflow-smoke", "--project-root", str(root)]) == EXIT_OK
    assert (root / "data" / "artifacts" / "metadata").is_dir()
    assert list((root / "data" / "artifacts" / "metadata").glob("*.json"))


def test_agent1a_requires_a_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["agent1a"]) == EXIT_CONTRACT_VIOLATION
    assert "workflow-smoke" in capsys.readouterr().err


def test_workflow_smoke_rejects_invalid_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, application="app:\n  environment: nope\n")
    assert main(["agent1a", "workflow-smoke", "--project-root", str(root)]) == EXIT_CONFIG_ERROR
    captured = capsys.readouterr()
    assert "environment" in captured.err
    assert "nope" not in captured.err
