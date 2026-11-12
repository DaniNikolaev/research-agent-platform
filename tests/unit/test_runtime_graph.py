"""LangGraph runtime: construction, node order, state flow, failure handling, isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.fixtures.logging import capturing_logger, make_settings

from research_platform.agents.agent_1a.contracts.graph_state import validate_state
from research_platform.agents.agent_1a.graph import (
    SMOKE_DOCUMENT_ID,
    GraphDependencies,
    SmokeRunRequest,
    build_smoke_state,
    create_agent_1a_graph,
)
from research_platform.agents.agent_1a.graph.workflow import Agent1ASmokeGraph
from research_platform.application.ports.model_gateway.errors import (
    ModelTimeoutError,
    ModelUnsupportedCapabilityError,
)
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.run_manager import RunManager
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.domain.runs.models import RunStatus
from research_platform.platform.artifact_store import LocalFileArtifactStore
from research_platform.platform.checkpointing import InMemoryCheckpointProvider
from research_platform.platform.models import FakeModelGateway, capabilities_from_profile
from research_platform.platform.persistence.in_memory_run_repository import (
    InMemoryRunRepository,
)


def build_runtime(
    tmp_path: Path, *, with_model: bool = True, settings=None, checkpointer: bool = True
):
    resolved = settings or make_settings()
    logger, stream = capturing_logger()
    repository = InMemoryRunRepository()
    store = LocalFileArtifactStore(root=tmp_path / "artifacts")
    run_manager = RunManager(repository=repository, logger=logger)
    artifact_service = ArtifactService(store=store, logger=logger)
    gateway = None
    if with_model:
        profiles = {
            name: capabilities_from_profile(profile)
            for name, profile in resolved.models.profiles.items()
        }
        gateway = FakeModelGateway(profiles=profiles, default_profile="fake-primary")
    dependencies = GraphDependencies(
        run_manager=run_manager,
        artifact_service=artifact_service,
        logger=logger,
        settings=resolved,
        model_gateway=gateway,
    )
    provider = InMemoryCheckpointProvider() if checkpointer else None
    runtime = create_agent_1a_graph(dependencies, checkpoint_provider=provider)
    return runtime, {
        "repository": repository,
        "store": store,
        "logger": logger,
        "stream": stream,
        "gateway": gateway,
        "provider": provider,
        "settings": resolved,
    }


# ------------------------------------------------------------------ construction


def test_graph_compiles_with_four_nodes(tmp_path: Path) -> None:
    runtime, _ = build_runtime(tmp_path)
    assert isinstance(runtime, Agent1ASmokeGraph)
    assert type(runtime.graph).__name__ == "CompiledStateGraph"
    nodes = set(runtime.graph.get_graph().nodes)
    assert {
        "initialize_run",
        "validate_runtime_state",
        "execute_smoke_step",
        "complete_run",
    } <= nodes


def test_graph_works_without_a_checkpointer(tmp_path: Path) -> None:
    runtime, _ = build_runtime(tmp_path, checkpointer=False)
    result = runtime.run()
    assert result.status is RunStatus.COMPLETED_WITH_WARNINGS


def test_graph_works_without_a_model(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path, with_model=False)
    result = runtime.run()
    assert result.status is RunStatus.COMPLETED_WITH_WARNINGS
    assert any("no model gateway" in warning for warning in result.final_state["warnings"])
    assert parts["gateway"] is None


# --------------------------------------------------------------------- happy path


def test_smoke_run_completes_and_records_a_trace(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    result = runtime.run()

    assert result.status is RunStatus.COMPLETED_WITH_WARNINGS
    assert result.run_id.startswith("run_")
    assert result.correlation_id.startswith("corr_")
    assert result.technical_trace_ref is not None
    assert result.technical_trace_ref.startswith("artifact://documents/doc_smoke_0001/runs/")

    record = parts["repository"].get_by_id(result.run_id)
    assert record.status is RunStatus.COMPLETED_WITH_WARNINGS
    assert record.document_id == SMOKE_DOCUMENT_ID
    assert record.correlation_id == result.correlation_id
    assert record.started_at is not None
    assert record.completed_at is not None


def test_trace_artifact_is_present_and_contains_no_card(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    result = runtime.run()

    stored = parts["store"].list_by_run(result.run_id)
    assert len(stored) == 1
    metadata = stored[0]
    assert metadata.artifact_type is ArtifactType.TRACE
    assert metadata.media_type == "application/json"

    from research_platform.domain.runs.artifacts import ArtifactReference

    payload = parts["store"].get(
        ArtifactReference(
            artifact_id=metadata.artifact_id,
            artifact_type=metadata.artifact_type,
            uri=metadata.uri,
            media_type=metadata.media_type,
            size_bytes=metadata.size_bytes,
            sha256=metadata.sha256,
            version="1",
            created_at=metadata.created_at,
        )
    )
    body = json.loads(payload)
    assert body["smoke"] is True
    assert "SMOKE TEST ONLY" in body["note"]
    assert "card" not in body
    assert "fields" not in body


def test_state_stays_compact_and_valid(tmp_path: Path) -> None:
    runtime, _ = build_runtime(tmp_path)
    result = runtime.run()
    validate_state(result.final_state)
    assert result.final_state["processing_status"] == "completed"
    assert isinstance(result.final_state["warnings"], list)


def test_logs_carry_run_and_correlation_ids(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    result = runtime.run()
    lines = [json.loads(line) for line in parts["stream"].getvalue().splitlines() if line.strip()]
    events = {line["event"] for line in lines}
    assert {"run_created", "run_started", "stage_started", "stage_completed"} <= events
    with_ids = [line for line in lines if line.get("run_id") == result.run_id]
    assert with_ids
    assert all(line.get("correlation_id") == result.correlation_id for line in with_ids)


def test_model_call_is_labelled_as_fake(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    runtime.run()
    lines = [json.loads(line) for line in parts["stream"].getvalue().splitlines() if line.strip()]
    fake_events = [line for line in lines if line["event"] == "fake_model_request_completed"]
    assert fake_events
    assert fake_events[0]["fake"] is True
    assert not [line for line in lines if line["event"] == "model_request_completed"]


# ---------------------------------------------------------------- failure handling


def test_permanent_model_error_fails_the_run(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    parts["gateway"].raise_scripted("agent_1a.smoke_check", ValueError("bad request"))

    with pytest.raises(Exception):  # noqa: B017 - the normalised model error
        runtime.run()

    runs = parts["repository"].list_by_status(RunStatus.FAILED)
    assert len(runs) == 1
    assert runs[0].errors
    assert runs[0].errors[0].correlation_id == runs[0].correlation_id


def test_retryable_error_is_retried_then_succeeds(tmp_path: Path) -> None:
    settings = make_settings()
    runtime, parts = build_runtime(tmp_path, settings=settings)
    calls = {"count": 0}
    original = parts["gateway"].generate_structured

    def flaky(request, schema):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ModelTimeoutError("temporary")
        return original(request, schema)

    parts["gateway"].generate_structured = flaky  # type: ignore[method-assign]
    result = runtime.run()

    assert result.status is RunStatus.COMPLETED_WITH_WARNINGS
    record = parts["repository"].get_by_id(result.run_id)
    assert record.retry_count == 1


def test_unsupported_capability_fails_fast(tmp_path: Path) -> None:
    from research_platform.config import PlatformSettings

    payload = make_settings().model_dump()
    payload["models"]["profiles"]["fake-primary"]["supports_structured_output"] = False
    payload["models"]["profiles"]["fake-primary"]["supports_json_schema"] = False
    settings = PlatformSettings.model_validate(payload)

    runtime, parts = build_runtime(tmp_path, settings=settings)
    with pytest.raises(ModelUnsupportedCapabilityError):
        runtime.run()
    failed = parts["repository"].list_by_status(RunStatus.FAILED)
    assert failed
    assert failed[0].errors[0].code == "unsupported_capability"


def test_failed_run_is_not_marked_completed(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    parts["gateway"].raise_scripted("agent_1a.smoke_check", ModelTimeoutError("down"))
    with pytest.raises(Exception):  # noqa: B017 - normalised timeout
        runtime.run()
    assert parts["repository"].list_by_status(RunStatus.COMPLETED) == []
    assert parts["repository"].list_by_status(RunStatus.COMPLETED_WITH_WARNINGS) == []


def test_invalid_state_is_rejected_before_any_model_call(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    state = build_smoke_state(SmokeRunRequest())
    state["document_id"] = ""
    with pytest.raises(ValueError):
        runtime.graph.invoke(state, config={"recursion_limit": 50})
    assert parts["gateway"].calls == []


# --------------------------------------------------------------------- isolation


def test_two_runs_are_isolated(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    first = runtime.run(SmokeRunRequest(note="first"))
    second = runtime.run(SmokeRunRequest(note="second"))

    assert first.run_id != second.run_id
    assert first.correlation_id != second.correlation_id
    assert first.technical_trace_ref != second.technical_trace_ref
    assert len(parts["store"].list_by_run(first.run_id)) == 1
    assert len(parts["store"].list_by_run(second.run_id)) == 1
    assert len(parts["repository"].list_by_status()) == 2
    assert parts["provider"] is not None
    assert len(parts["provider"].known_threads()) == 2


def test_checkpoints_are_isolated_per_run(tmp_path: Path) -> None:
    runtime, _ = build_runtime(tmp_path)
    first = runtime.run()
    second = runtime.run()

    first_state = runtime.state_for_run(first.run_id)
    second_state = runtime.state_for_run(second.run_id)
    assert first_state is not None
    assert second_state is not None
    assert first_state["run_id"] == first.run_id
    assert second_state["run_id"] == second.run_id
    assert first_state["correlation_id"] != second_state["correlation_id"]
    assert runtime.state_for_run("run_20990101_000000_ffffff") is None


def test_state_for_run_is_none_without_a_checkpointer(tmp_path: Path) -> None:
    runtime, _ = build_runtime(tmp_path, checkpointer=False)
    result = runtime.run()
    assert runtime.state_for_run(result.run_id) is None


def test_thread_ids_are_derived_from_run_ids(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    result = runtime.run()
    assert parts["provider"] is not None
    assert parts["provider"].thread_id_for_run(result.run_id) in parts["provider"].known_threads()


# ------------------------------------------------------------------- dependencies


def test_no_global_service_registry_is_used() -> None:
    import research_platform.app as app_module

    assert not hasattr(app_module, "_CONTEXT")
    assert not hasattr(app_module, "_GLOBAL_CONTEXT")


def test_runtime_factory_returns_a_fresh_graph_each_time(tmp_path: Path) -> None:
    runtime, parts = build_runtime(tmp_path)
    first = runtime.run()
    second_graph = create_agent_1a_graph(
        runtime.dependencies, checkpoint_provider=parts["provider"]
    )
    assert second_graph is not runtime
    assert runtime.state_for_run(first.run_id) is not None
