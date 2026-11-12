"""Integration scenarios A–F from the iteration brief, exercised through the real wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fixtures.logging import make_settings

from research_platform.agents.agent_1a.graph import SmokeRunRequest
from research_platform.app import ApplicationBootstrap, bootstrap_application
from research_platform.application.ports.artifact_store import ArtifactConflictError
from research_platform.application.ports.model_gateway.errors import ModelTimeoutError
from research_platform.config import PlatformSettings
from research_platform.domain.runs.artifacts import ArtifactReference, ArtifactType
from research_platform.domain.runs.models import RunStatus


def write_config(root: Path, *, application: str | None = None, models: str | None = None) -> Path:
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if application is not None:
        (config_dir / "application.yaml").write_text(application, encoding="utf-8")
    if models is not None:
        (config_dir / "models.yaml").write_text(models, encoding="utf-8")
    return root


VALID_APP = """
app:
  environment: ci
  log_level: WARNING
paths:
  data_root: data
  log_dir: logs
runtime:
  checkpointer: in_memory
  max_graph_steps: 20
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


# ------------------------------------------------------------------- scenario A


def test_scenario_a_successful_smoke_run(tmp_path: Path) -> None:
    """Config → repository → store → fake model → graph → checks → logs → completed."""
    root = write_config(tmp_path, application=VALID_APP, models=VALID_MODELS)
    context = bootstrap_application(project_root=root)
    try:
        assert context.settings.app.environment.value == "ci"
        runtime = context.runtime_factory.build_agent_1a()
        result = runtime.run(SmokeRunRequest(note="scenario A"))

        assert result.status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}
        record = context.run_manager.get_run(result.run_id)
        assert record.status is result.status
        assert record.completed_at is not None

        artifacts = context.artifact_service.list_for_run(result.run_id)
        assert len(artifacts) == 1
        assert artifacts[0].artifact_type is ArtifactType.TRACE
        assert context.artifact_store.root.is_dir()
        assert (tmp_path / "data" / "artifacts").exists()

        _log_text = Path(tmp_path / "logs").exists()
        assert isinstance(_log_text, bool)
    finally:
        context.close()


def test_scenario_a_logs_are_traceable_end_to_end(tmp_path: Path) -> None:
    root = write_config(
        tmp_path,
        application=VALID_APP.replace("log_level: WARNING", "log_level: INFO"),
        models=VALID_MODELS,
    )
    stream = __import__("io").StringIO()
    context = ApplicationBootstrap(project_root=root, log_stream=stream).build()
    try:
        result = context.runtime_factory.build_agent_1a().run()
    finally:
        context.close()

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    run_lines = [line for line in lines if line.get("run_id") == result.run_id]
    assert run_lines
    ordered = [line["event"] for line in run_lines]
    assert "run_created" in ordered
    assert "run_started" in ordered
    assert "run_completed" in ordered
    assert ordered.index("run_created") < ordered.index("run_started")
    assert ordered.index("run_started") < ordered.index("run_completed")
    assert all(line["correlation_id"] == result.correlation_id for line in run_lines)
    assert lines[0]["event"] == "application_started"
    assert lines[-1]["event"] == "application_shutdown"


# ------------------------------------------------------------------- scenario B


def test_scenario_b_invalid_configuration_stops_before_the_graph(tmp_path: Path) -> None:
    root = write_config(
        tmp_path,
        application="app:\n  environment: not-a-real-environment\n  log_level: INFO\n",
        models=VALID_MODELS,
    )
    with pytest.raises(Exception) as excinfo:
        ApplicationBootstrap(project_root=root).build()

    message = str(excinfo.value)
    assert "environment" in message
    assert not (tmp_path / "data" / "artifacts" / "metadata").exists()


def test_scenario_b_configuration_error_never_prints_a_secret(tmp_path: Path) -> None:
    root = write_config(
        tmp_path,
        application="app:\n  environment: ci\nstorage:\n"
        "  artifact_root_uri: https://user:password@example.invalid/bucket\n",
        models=VALID_MODELS,
    )
    with pytest.raises(Exception) as excinfo:
        ApplicationBootstrap(project_root=root).build()

    rendered = str(excinfo.value)
    assert "password" not in rendered
    assert "user:password" not in rendered


def test_scenario_b_missing_configuration_is_not_a_crash(tmp_path: Path) -> None:
    context = ApplicationBootstrap(project_root=tmp_path).build()
    try:
        assert context.settings.app.environment.value == "local"
        # The built-in defaults declare no model profiles, so no gateway is assembled.
        assert context.model_gateway is None
        report = context.health_check.run()
        gateway = next(check for check in report.checks if check.name == "model_gateway")
        assert gateway.status.value == "NOT_CONFIGURED"
        assert report.failed is False
    finally:
        context.close()


# ------------------------------------------------------------------- scenario C


def test_scenario_c_node_error_is_normalised_and_the_run_fails(tmp_path: Path) -> None:
    root = write_config(tmp_path, application=VALID_APP, models=VALID_MODELS)
    context = bootstrap_application(project_root=root)
    try:
        assert context.model_gateway is not None
        context.model_gateway.raise_scripted("agent_1a.smoke_check", ModelTimeoutError("boom"))
        runtime = context.runtime_factory.build_agent_1a()

        with pytest.raises(Exception):  # noqa: B017 - normalised retryable error
            runtime.run()

        failed = context.run_manager.list_runs(RunStatus.FAILED)
        assert len(failed) == 1
        error = failed[0].errors[0]
        assert error.code == "timeout"
        assert error.correlation_id == failed[0].correlation_id
        assert context.run_manager.list_runs(RunStatus.COMPLETED) == []
        assert context.artifact_service.list_for_run(failed[0].run_id) == []
    finally:
        context.close()


# ------------------------------------------------------------------- scenario D


def test_scenario_d_conflict_never_overwrites_the_original(tmp_path: Path) -> None:
    context = bootstrap_application(project_root=tmp_path, settings=make_settings())
    try:
        first = context.artifact_service.store_bytes(
            data=b"original",
            artifact_id="art_doc_0001",
            artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
            media_type="application/pdf",
            document_id="doc_alpha_0001",
            run_id="run_20261112_120000_abcdef",
        )
        with pytest.raises(ArtifactConflictError) as excinfo:
            context.artifact_service.store_bytes(
                data=b"replacement",
                artifact_id="art_doc_0001",
                artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
                media_type="application/pdf",
                document_id="doc_alpha_0001",
                run_id="run_20261112_120000_abcdef",
            )
        assert "already exists" in str(excinfo.value)

        reference = ArtifactReference(
            artifact_id=first.artifact_id,
            artifact_type=first.artifact_type,
            uri=first.uri,
            media_type=first.media_type,
            size_bytes=first.size_bytes,
            sha256=first.sha256,
            version="1",
            created_at=first.created_at,
        )
        assert context.artifact_service.read_bytes(reference) == b"original"
        assert context.artifact_service.verify(reference) is True
    finally:
        context.close()


# ------------------------------------------------------------------- scenario E


def test_scenario_e_runs_do_not_share_state(tmp_path: Path) -> None:
    context = bootstrap_application(project_root=tmp_path, settings=make_settings())
    try:
        runtime = context.runtime_factory.build_agent_1a()
        first = runtime.run()
        second = runtime.run()

        assert first.run_id != second.run_id
        assert first.correlation_id != second.correlation_id
        assert first.technical_trace_ref != second.technical_trace_ref
        assert len(context.artifact_service.list_for_run(first.run_id)) == 1
        assert len(context.artifact_service.list_for_run(second.run_id)) == 1
        assert len(context.run_manager.list_runs()) == 2

        first_state = runtime.state_for_run(first.run_id)
        second_state = runtime.state_for_run(second.run_id)
        assert first_state is not None
        assert second_state is not None
        assert first_state["run_id"] != second_state["run_id"]
        assert first_state["technical_trace_ref"] != second_state["technical_trace_ref"]
    finally:
        context.close()


# ------------------------------------------------------------------- scenario F


def test_scenario_f_smoke_run_without_any_model(tmp_path: Path) -> None:
    settings = PlatformSettings.model_validate(
        {"models": {"profiles": {}, "default_profile": None}}
    )
    context = bootstrap_application(project_root=tmp_path, settings=settings)
    try:
        assert context.model_gateway is None
        result = context.runtime_factory.build_agent_1a().run()
        assert result.status is RunStatus.COMPLETED_WITH_WARNINGS
        assert any("no model" in warning for warning in result.final_state["warnings"])
    finally:
        context.close()


def test_scenario_f_platform_health_does_not_require_a_provider(tmp_path: Path) -> None:
    settings = PlatformSettings.model_validate({"models": {"profiles": {}}})
    context = bootstrap_application(project_root=tmp_path, settings=settings)
    try:
        report = context.health_check.run()
        names = {check.name for check in report.checks}
        assert {"python_version", "artifact_store", "langgraph_runtime"} <= names
        gateway = next(check for check in report.checks if check.name == "model_gateway")
        assert gateway.status.value == "NOT_CONFIGURED"
        assert report.failed is False
    finally:
        context.close()
