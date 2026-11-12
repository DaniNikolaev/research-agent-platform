"""Graph nodes of the technical Agent 1A workflow.

Every node is a pure function of ``(state, dependencies)``: dependencies are injected by
the factory, so a node never builds a connection, reads a file path or looks up a global.
Each node returns a *partial* state update; LangGraph merges it into the running state.

The nodes are deliberately technical.  ``execute_smoke_step`` writes a trace artefact and
optionally calls the model gateway to prove the wiring works — it never produces a card
field, an evidence record or a scientific verdict.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from research_platform.agents.agent_1a.contracts.graph_state import (
    Agent1AState,
    ProcessingStatus,
    validate_state,
)
from research_platform.application.ports.logging import (
    FAKE_MODEL_REQUEST_COMPLETED,
    MODEL_REQUEST_STARTED,
    RETRY_STARTED,
    RUN_COMPLETED,
    STAGE_COMPLETED,
    STAGE_FAILED,
    STAGE_STARTED,
    LoggerPort,
)
from research_platform.application.ports.model_gateway import ModelGatewayPort
from research_platform.application.ports.model_gateway.errors import ModelError
from research_platform.application.ports.model_gateway.types import (
    GenerationParameters,
    Message,
    ModelRequest,
    Role,
    SchemaReference,
)
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.run_manager import RunManager, StartRunRequest
from research_platform.config import LogLevel, PlatformSettings
from research_platform.domain.runs.artifacts import ArtifactType, parse_artifact_uri
from research_platform.domain.runs.models import RunStage, generate_correlation_id

__all__ = [
    "SMOKE_SCHEMA",
    "SMOKE_TASK",
    "GraphDependencies",
    "complete_run",
    "execute_smoke_step",
    "initialize_run",
    "validate_runtime_state",
]

SMOKE_TASK = "agent_1a.smoke_check"
"""Task name of the technical check; it is not an extraction task."""

SMOKE_SCHEMA = SchemaReference(schema_id="agent_1a.smoke", version="1.0")
"""Schema id of the fixed technical payload returned by the fake model."""


@dataclass(frozen=True, slots=True)
class GraphDependencies:
    """Everything a node is allowed to use, supplied by the runtime factory."""

    run_manager: RunManager
    artifact_service: ArtifactService
    logger: LoggerPort
    settings: PlatformSettings
    model_gateway: ModelGatewayPort | None = None
    clock: Callable[[], datetime] | None = field(default=None)

    @property
    def contract_version(self) -> str | None:
        """Contract version recorded on a run, when the workflow produces a package.

        The smoke workflow produces no handoff package, so a run honestly records none.
        """
        return None


def _log(deps: GraphDependencies, state: Agent1AState) -> LoggerPort:
    return deps.logger.bind(
        run_id=state.get("run_id") or None,
        correlation_id=state.get("correlation_id") or None,
        agent_id=state.get("agent_id") or None,
    )


def _profile_for(deps: GraphDependencies) -> str:
    """The model profile used by the smoke step (configured default first)."""
    settings = deps.settings
    if settings.models.default_profile:
        return settings.models.default_profile
    if settings.models.profiles:
        return next(iter(sorted(settings.models.profiles)))
    raise ModelError("no model profiles configured")


def initialize_run(state: Agent1AState, deps: GraphDependencies) -> dict[str, Any]:
    """Validate the minimum inputs and create the run."""
    log = _log(deps, state)
    log.event(STAGE_STARTED, stage=RunStage.REGISTRATION.value)

    document_id = state.get("document_id")
    if not document_id:
        raise ValueError("smoke workflow requires a document_id")
    source_sha256 = state.get("source_sha256")
    if not source_sha256:
        raise ValueError("smoke workflow requires a source_sha256")

    run_id = state.get("run_id") or deps.run_manager.new_run_id()
    correlation_id = state.get("correlation_id") or generate_correlation_id()
    record = deps.run_manager.start_run(
        StartRunRequest(
            run_id=run_id,
            document_id=str(document_id),
            agent_id=state.get("agent_id") or "1A",
            agent_version=state.get("agent_version") or "0.1.0",
            contract_version=deps.contract_version,
            correlation_id=correlation_id,
            metadata={"purpose": "smoke", "input_sha256": str(source_sha256)},
        )
    )
    deps.logger.bind(
        run_id=record.run_id, correlation_id=correlation_id, agent_id=record.agent_id
    ).event(STAGE_COMPLETED, stage=RunStage.REGISTRATION.value, status=record.status.value)
    return {
        "run_id": record.run_id,
        "correlation_id": correlation_id,
        "processing_status": ProcessingStatus.REGISTERED.value,
    }


def validate_runtime_state(state: Agent1AState, deps: GraphDependencies) -> dict[str, Any]:
    """Prove that the state and the wired services are mutually consistent."""
    log = _log(deps, state)
    log.event(STAGE_STARTED, stage=RunStage.VALIDATION.value)

    validate_state(state)

    run_id = state.get("run_id")
    if not run_id:
        raise ValueError("initialize_run did not assign a run_id")
    record = deps.run_manager.get_run(run_id)
    if record.document_id != state.get("document_id"):
        raise ValueError(
            f"state document_id {state.get('document_id')!r} does not match the stored run "
            f"{record.document_id!r}"
        )
    if record.correlation_id and record.correlation_id != state.get("correlation_id"):
        raise ValueError("state correlation_id does not match the stored run")

    deps.run_manager.update_stage(run_id, RunStage.VALIDATION)
    log.event(STAGE_COMPLETED, stage=RunStage.VALIDATION.value)
    return {"processing_status": ProcessingStatus.REGISTERED.value}


def execute_smoke_step(state: Agent1AState, deps: GraphDependencies) -> dict[str, Any]:
    """Run one deterministic technical step: an optional fake model call and a trace artefact."""
    run_id = state["run_id"]
    log = _log(deps, state)
    log.event(STAGE_STARTED, stage=RunStage.SMOKE.value)

    warnings = list(state.get("warnings") or [])
    model_result: dict[str, Any] | None = None
    if deps.model_gateway is None:
        warnings.append("no model gateway configured; the smoke step ran without a model")
    elif not deps.settings.models.profiles:
        warnings.append("no model profile configured; the smoke step ran without a model")
    else:
        model_result = _call_model(state=state, deps=deps)
        deps.run_manager.update_stage(run_id, RunStage.SMOKE)

    report = {
        "smoke": True,
        "run_id": run_id,
        "document_id": state.get("document_id"),
        "source_sha256": state.get("source_sha256"),
        "model_call": model_result,
        "note": "SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED",
    }
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    metadata = deps.artifact_service.store_bytes(
        data=payload,
        artifact_id=f"art_smoke_{run_id}",
        artifact_type=ArtifactType.TRACE,
        media_type="application/json",
        document_id=str(state["document_id"]),
        run_id=run_id,
        schema_version="1.0",
        correlation_id=state.get("correlation_id") or None,
    )
    parse_artifact_uri(metadata.uri)  # the state must carry a valid logical URI

    log.event(
        STAGE_COMPLETED,
        stage=RunStage.SMOKE.value,
        artifact_id=metadata.artifact_id,
        sha256=metadata.sha256,
        model_invoked=model_result is not None,
    )
    return {
        "technical_trace_ref": metadata.uri,
        "warnings": warnings,
        "processing_status": ProcessingStatus.REGISTERED.value,
    }


def complete_run(state: Agent1AState, deps: GraphDependencies) -> dict[str, Any]:
    """Finish the run successfully and record the terminal event."""
    run_id = state["run_id"]
    log = _log(deps, state)
    record = deps.run_manager.complete_run(run_id, warnings=state.get("warnings") or [])
    log.event(
        RUN_COMPLETED,
        stage=RunStage.SMOKE.value,
        status=record.status.value,
        technical_trace_ref=state.get("technical_trace_ref"),
    )
    return {"processing_status": ProcessingStatus.COMPLETED.value}


def _call_model(*, state: Agent1AState, deps: GraphDependencies) -> dict[str, Any]:
    """Call the configured model once, with bounded retries taken from configuration."""
    gateway = deps.model_gateway
    if gateway is None:  # pragma: no cover - checked by the caller
        raise ValueError("model gateway is not configured")
    profile_name = _profile_for(deps)
    log = _log(deps, state)
    request = ModelRequest(
        request_id=f"req_smoke_{state['run_id']}",
        model_profile=profile_name,
        task=SMOKE_TASK,
        messages=(
            Message(role=Role.SYSTEM, content="Technical smoke check; return no content."),
            Message(role=Role.USER, content="ping"),
        ),
        output_schema=SMOKE_SCHEMA,
        generation=GenerationParameters(max_output_tokens=64, temperature=0.0),
    )
    attempts = deps.settings.models.profiles[profile_name].max_retries + 1
    for attempt in range(1, attempts + 1):
        log.event(
            MODEL_REQUEST_STARTED,
            provider=getattr(gateway, "provider_id", None),
            attempt=attempt,
        )
        try:
            response = gateway.generate_structured(request, SMOKE_SCHEMA)
        except ModelError as exc:
            if not exc.retryable or attempt >= attempts:
                log.event(
                    STAGE_FAILED,
                    level=LogLevel.ERROR,
                    stage=RunStage.SMOKE.value,
                    error_code=exc.code,
                    attempt=attempt,
                )
                raise
            deps.run_manager.record_retry(state["run_id"], RunStage.SMOKE, attempt)
            log.event(
                RETRY_STARTED, stage=RunStage.SMOKE.value, attempt=attempt, error_code=exc.code
            )
            continue
        log.event(
            FAKE_MODEL_REQUEST_COMPLETED,
            provider=response.provider_id,
            model_id=response.model_id,
            attempt=attempt,
            input_tokens=(response.usage.input_tokens if response.usage else None),
            output_tokens=(response.usage.output_tokens if response.usage else None),
            latency_ms=response.latency_ms,
            fake=getattr(gateway, "is_fake", False),
        )
        return {
            "provider": response.provider_id,
            "model_id": response.model_id,
            "task": SMOKE_TASK,
            "schema_id": SMOKE_SCHEMA.schema_id,
            "attempts": attempt,
        }
    raise ModelError("smoke model call failed without a retryable error")
