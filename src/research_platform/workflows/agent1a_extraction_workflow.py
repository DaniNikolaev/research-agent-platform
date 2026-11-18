"""LangGraph extraction workflow of Agent 1A (iteration 3).

The graph is deliberately explicit: validation, run initialisation, document loading, quality
routing, plan building, four extraction groups (each followed by its own validation/persistence
node), aggregation, manifest and finalisation.  Failures leave the graph through dedicated
nodes, so a run always ends with an explainable status instead of a silent success.

Large objects — parsed document, canonical text, group payloads — never enter the state: they
live in :class:`ExtractionRuntimeCache` for the duration of one invocation and in the Artifact
Store afterwards.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, cast

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from research_platform.application.ports.logging import LoggerPort
from research_platform.application.services.agent1a_extraction import (
    ExtractionService,
    GroupRunContext,
)
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.extraction_errors import (
    RUN_ERROR_CODES,
    ErrorCategory,
    ExtractionArtifactError,
    ExtractionCheckpointError,
    ExtractionError,
    ExtractionInputError,
    ExtractionIntegrityError,
    ExtractionStorageError,
)
from research_platform.application.services.extraction_prompts import (
    PromptTemplate,
)
from research_platform.application.services.run_manager import StartRunRequest
from research_platform.config import PlatformSettings
from research_platform.domain.documents.models import SourceDocumentManifest
from research_platform.domain.documents.parsed import ParsedDocument
from research_platform.domain.documents.quality import (
    QualityReport,
    QualityStatus,
)
from research_platform.domain.extraction.models import (
    ExtractionGroup,
    ExtractionPlan,
    ExtractionResult,
    ExtractionRunManifest,
    GroupExtractionResult,
    GroupStatus,
    groups_in_order,
)
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.domain.runs.models import (
    RunError,
    RunRecord,
    RunStage,
    RunStatus,
)
from research_platform.workflows.agent1a_extraction_state import (
    Agent1AExtractionState,
    initial_extraction_state,
    validate_extraction_state,
)

__all__ = [
    "INTERRUPTABLE_NODES",
    "Agent1AExtractionRunner",
    "ExtractionDependencies",
    "ExtractionOutcome",
    "ExtractionRuntimeCache",
    "build_extraction_graph",
]

INTERRUPTABLE_NODES: Final[tuple[str, ...]] = (
    "validate_and_persist_g1_bibliography",
    "validate_and_persist_g2_research_design",
    "validate_and_persist_g3_methods_data_variables",
    "validate_and_persist_g4_findings_implications",
)
"""Nodes where a technical breakpoint may be placed for checkpoint/resume verification."""

_READY_QUALITY: Final = {
    QualityStatus.PASSED.value,
    QualityStatus.PASSED_WITH_WARNINGS.value,
}


@dataclass
class ExtractionRuntimeCache:
    """Large objects of one invocation; never checkpointed."""

    manifest: SourceDocumentManifest | None = None
    parsed_document: ParsedDocument | None = None
    canonical_text: str = ""
    quality_report: QualityReport | None = None
    plan: ExtractionPlan | None = None
    results: dict[ExtractionGroup, GroupExtractionResult] = field(default_factory=dict)
    aggregate: ExtractionResult | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExtractionDependencies:
    """Everything the nodes need; assembled by the composition root."""

    settings: PlatformSettings
    artifacts: ArtifactService
    extraction: ExtractionService
    run_manager: Any
    logger: LoggerPort
    cache: ExtractionRuntimeCache
    prompts: dict[ExtractionGroup, PromptTemplate]
    clock: Callable[[], datetime] | None = None


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    """Result of one ``run``/``resume`` call."""

    state: Agent1AExtractionState
    run: RunRecord | None
    paused: bool = False
    stop_reason: str | None = None


# --------------------------------------------------------------------------- helpers
_URI_RUN_RE: Final = re.compile(r"^artifact://documents/[^/]+/runs/([^/]+)/")


def _read_uri(deps: ExtractionDependencies, uri: str | None) -> bytes:
    """Read an artefact by its logical URI (resolved through the run namespace)."""
    if not uri:
        raise ExtractionArtifactError("a required artefact URI is missing from the state")
    match = _URI_RUN_RE.match(uri)
    if match is None:
        raise ExtractionArtifactError(f"{uri!r} is not a run-scoped artefact URI")
    for metadata in _artifacts_of_run(deps, match.group(1)):
        if metadata.uri == uri:
            return deps.artifacts.read_by_id(metadata.artifact_id)
    raise ExtractionArtifactError(f"no artefact is published at {uri}")


def _hydrate_cache(deps: ExtractionDependencies, state: Agent1AExtractionState) -> None:
    """Fill the runtime cache from published artefacts.

    A resumed run starts at the breakpoint, so the nodes before it never execute and the
    process-local cache is empty.  Every node calls this helper: it loads only what is
    missing, which makes a warm cache free and a cold (resumed) cache correct.
    """
    cache = deps.cache
    if cache.manifest is None and state.get("source_manifest_uri"):
        cache.manifest = SourceDocumentManifest.model_validate(
            json.loads(_read_uri(deps, state["source_manifest_uri"]))
        )
    if cache.parsed_document is None and state.get("parsed_document_uri"):
        cache.parsed_document = ParsedDocument.model_validate(
            json.loads(_read_uri(deps, state["parsed_document_uri"]))
        )
    if not cache.canonical_text and state.get("canonical_text_uri"):
        cache.canonical_text = _read_uri(deps, state["canonical_text_uri"]).decode("utf-8")
    if cache.quality_report is None and state.get("quality_report_uri"):
        cache.quality_report = QualityReport.model_validate(
            json.loads(_read_uri(deps, state["quality_report_uri"]))
        )
    if cache.plan is None and state.get("extraction_plan_uri"):
        cache.plan = ExtractionPlan.model_validate(
            json.loads(_read_uri(deps, state["extraction_plan_uri"]))
        )
    for group, uri in (state.get("group_result_uris") or {}).items():
        group_enum = ExtractionGroup(group)
        if group_enum not in cache.results:
            cache.results[group_enum] = GroupExtractionResult.model_validate(
                json.loads(_read_uri(deps, uri))
            )


_URI_RUN_RE_DOC = _URI_RUN_RE


def _run_or_none(deps: ExtractionDependencies, run_id: str) -> RunRecord | None:
    """Look up a run, tolerating the in-memory repository not knowing it after a restart."""
    if deps.run_manager is None:
        return None
    try:
        return deps.run_manager.get_run(run_id)
    except Exception:
        return None


def _now(deps: ExtractionDependencies) -> datetime:
    return deps.clock().astimezone(UTC) if deps.clock else datetime.now(UTC)


def _iso(deps: ExtractionDependencies) -> str:
    return _now(deps).isoformat().replace("+00:00", "Z")


def _log(deps: ExtractionDependencies, state: Agent1AExtractionState) -> Any:
    return deps.logger.bind(
        run_id=state.get("run_id") or None,
        document_id=state.get("document_id") or None,
        correlation_id=state.get("correlation_id"),
    )


def _store_json(
    deps: ExtractionDependencies,
    *,
    prefix: str,
    artifact_type: ArtifactType,
    payload: Any,
    document_id: str,
    run_id: str,
) -> str:
    from research_platform.application.services.artifact_ids import run_artifact_id

    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    try:
        metadata = deps.artifacts.store_bytes(
            data=body,
            artifact_id=run_artifact_id(prefix, document_id=document_id, run_id=run_id),
            artifact_type=artifact_type,
            media_type="application/json",
            document_id=document_id,
            run_id=run_id,
        )
    except Exception as exc:
        raise ExtractionStorageError(f"could not store {prefix}: {exc}") from exc
    return metadata.uri


def _read_json(deps: ExtractionDependencies, artifact_id: str) -> Any:
    data = deps.artifacts.read_by_id(artifact_id)
    return json.loads(data.decode("utf-8"))


def _artifacts_of_run(deps: ExtractionDependencies, run_id: str) -> list[Any]:
    return deps.artifacts.list_for_run(run_id)


def _resolve_document_run(
    deps: ExtractionDependencies, document_run_id: str
) -> tuple[SourceDocumentManifest, ParsedDocument, str, QualityReport, list[str], str]:
    """Load the artefact bundle of a completed Document Workflow run."""
    warnings: list[str] = []
    try:
        artifacts = _artifacts_of_run(deps, document_run_id)
    except Exception as exc:
        raise ExtractionArtifactError(
            f"could not list artefacts of document run {document_run_id!r}: {exc}"
        ) from exc
    if not artifacts:
        raise ExtractionArtifactError(
            f"document run {document_run_id!r} has no artefacts; run 'document process' first"
        )

    by_type: dict[str, list[Any]] = {}
    for metadata in artifacts:
        by_type.setdefault(metadata.artifact_type.value, []).append(metadata)

    def _pick(artifact_type: ArtifactType, media_type: str | None = None) -> Any:
        candidates = by_type.get(artifact_type.value, [])
        if media_type is not None:
            candidates = [item for item in candidates if item.media_type == media_type]
        if not candidates:
            raise ExtractionArtifactError(
                f"document run {document_run_id!r} has no {artifact_type.value} artefact"
                + (f" with media type {media_type}" if media_type else "")
            )
        return candidates[-1]

    manifest_meta = _pick(ArtifactType.HANDOFF_PACKAGE)
    parsed_meta = _pick(ArtifactType.PARSED_DOCUMENT, "application/json")
    canonical_meta = _pick(ArtifactType.PARSED_DOCUMENT, "text/plain")
    quality_meta = _pick(ArtifactType.QUALITY_REPORT)

    try:
        manifest = SourceDocumentManifest.model_validate(
            _read_json(deps, manifest_meta.artifact_id)
        )
        parsed = ParsedDocument.model_validate(_read_json(deps, parsed_meta.artifact_id))
        canonical_text = deps.artifacts.read_by_id(canonical_meta.artifact_id).decode("utf-8")
        quality = QualityReport.model_validate(_read_json(deps, quality_meta.artifact_id))
    except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExtractionArtifactError(
            f"document run {document_run_id!r} has an unreadable artefact: {exc}"
        ) from exc
    except Exception as exc:
        raise ExtractionArtifactError(
            f"document run {document_run_id!r} artefacts cannot be read: {exc}"
        ) from exc

    if manifest.document_id != parsed.document_id or parsed.document_id != quality.document_id:
        raise ExtractionIntegrityError(
            "document identifiers disagree between manifest, parsed document and quality report"
        )
    if manifest.source_sha256 != parsed.source_sha256:
        raise ExtractionIntegrityError("the manifest hash and the parsed document hash disagree")
    if quality.source_sha256 != parsed.source_sha256:
        raise ExtractionIntegrityError("the quality report references a different source hash")
    document_run = _run_or_none(deps, document_run_id)
    if document_run is None:
        warnings.append(
            "document run state was reconstructed from published artefacts: the in-memory "
            "RunRepository does not outlive the process (OI-006)"
        )
    elif document_run.status not in {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_WITH_WARNINGS,
        RunStatus.WAITING_FOR_HUMAN,
        RunStatus.FAILED,
    }:
        raise ExtractionInputError(
            f"document run {document_run_id!r} is {document_run.status.value}; the document "
            "workflow must finish before extraction can start"
        )
    warnings.extend(quality.parser_warnings[:5])
    warnings.extend(
        f"{check.check_id}: {check.message}"
        for check in quality.checks
        if check.status.value == "warning"
    )
    return manifest, parsed, canonical_text, quality, warnings, manifest_meta.uri


# ----------------------------------------------------------------------------- nodes


def _as_state(value: object) -> Agent1AExtractionState:
    """Coerce an untyped LangGraph mapping into the typed state.

    LangGraph returns raw mappings from ``invoke``/``get_state``; this is the single place
    where that boundary is crossed, so typed code downstream never sees ``dict[str, Any]``.
    """
    raw: Any = value
    if not isinstance(raw, dict):
        return Agent1AExtractionState()
    return cast(Agent1AExtractionState, dict(raw))


def _ensure_run(deps: ExtractionDependencies, state: Agent1AExtractionState) -> Any:
    """Return the run record, re-materialising it from the checkpoint when needed.

    The RunRepository is in memory (OI-006), so a run resumed in a new process has no record.
    Re-registering it under the *same* ``run_id`` keeps the run identity stable and is the
    documented reconstruction path; it never invents a second run for the same execution.
    """
    run_id = state.get("run_id") or ""
    if not run_id:
        return None
    existing = _run_or_none(deps, run_id)
    if existing is not None:
        return existing
    return deps.run_manager.start_run(
        StartRunRequest(
            document_id=state["document_id"],
            agent_id="1A",
            agent_version=state.get("agent_version") or "0.3.0",
            contract_version="1.0",
            correlation_id=state.get("correlation_id"),
            run_id=run_id,
        )
    )


def _validate_extraction_input(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    validate_extraction_state(state)
    log = _log(deps, state)
    log.event("stage_started", stage=RunStage.REGISTRATION.value, node="validate_extraction_input")
    (
        manifest,
        parsed,
        canonical_text,
        quality,
        warnings,
        manifest_uri,
    ) = _resolve_document_run(deps, state["document_run_id"])
    deps.cache.manifest = manifest
    deps.cache.parsed_document = parsed
    deps.cache.canonical_text = canonical_text
    deps.cache.quality_report = quality
    deps.cache.warnings.extend(warnings)
    return {
        "document_id": manifest.document_id,
        "source_sha256": manifest.source_sha256,
        "source_manifest_uri": manifest_uri,
        "parsed_document_uri": parsed_artifact_uri(deps, state["document_run_id"]),
        "canonical_text_uri": canonical_artifact_uri(deps, state["document_run_id"]),
        "quality_report_uri": quality_artifact_uri(deps, state["document_run_id"]),
        "quality_status": quality.overall_status.value,
        "processing_status": "validated",
        "warnings": list(state.get("warnings") or []) + warnings,
        "last_completed_node": "validate_extraction_input",
        "updated_at": _iso(deps),
    }


def parsed_artifact_uri(deps: ExtractionDependencies, document_run_id: str) -> str:
    """URI of the parsed document artefact of a document run."""
    for metadata in _artifacts_of_run(deps, document_run_id):
        if (
            metadata.artifact_type is ArtifactType.PARSED_DOCUMENT
            and metadata.media_type == "application/json"
        ):
            return metadata.uri
    raise ExtractionArtifactError(f"document run {document_run_id!r} has no parsed document")


def canonical_artifact_uri(deps: ExtractionDependencies, document_run_id: str) -> str:
    """URI of the canonical text artefact of a document run."""
    for metadata in _artifacts_of_run(deps, document_run_id):
        if (
            metadata.artifact_type is ArtifactType.PARSED_DOCUMENT
            and metadata.media_type == "text/plain"
        ):
            return metadata.uri
    raise ExtractionArtifactError(f"document run {document_run_id!r} has no canonical text")


def quality_artifact_uri(deps: ExtractionDependencies, document_run_id: str) -> str:
    """URI of the quality report artefact of a document run."""
    for metadata in _artifacts_of_run(deps, document_run_id):
        if metadata.artifact_type is ArtifactType.QUALITY_REPORT:
            return metadata.uri
    raise ExtractionArtifactError(f"document run {document_run_id!r} has no quality report")


def _initialize_agent1a_run(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    record = deps.run_manager.start_run(
        StartRunRequest(
            document_id=state["document_id"],
            agent_id="1A",
            agent_version=state.get("agent_version") or "0.3.0",
            contract_version="1.0",
            correlation_id=state.get("correlation_id"),
            run_id=state.get("run_id") or None,
        )
    )
    return {
        "run_id": record.run_id,
        "processing_status": "initialized",
        "created_at": _iso(deps),
        "updated_at": _iso(deps),
        "last_completed_node": "initialize_agent1a_run",
    }


def _load_parsed_document(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    # An empty canonical text is a legitimate outcome (a scan); the Quality Gate decides what
    # happens next, so this node only requires that the artefact was loaded.
    if deps.cache.parsed_document is None:
        raise ExtractionArtifactError("the parsed document was not loaded by the input node")
    deps.run_manager.update_stage(state["run_id"], RunStage.EXTRACTION)
    return {
        "processing_status": "loaded",
        "last_completed_node": "load_parsed_document",
        "updated_at": _iso(deps),
    }


def _check_quality_gate(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    quality = deps.cache.quality_report
    if quality is None:  # pragma: no cover - guarded by the previous node
        raise ExtractionError("the quality report is missing")
    return {
        "quality_status": quality.overall_status.value,
        "processing_status": "quality_checked",
        "last_completed_node": "check_quality_gate",
        "updated_at": _iso(deps),
    }


def _route_quality(state: Agent1AExtractionState) -> str:
    status = state.get("quality_status")
    if status in _READY_QUALITY:
        return "proceed"
    if status == QualityStatus.REQUIRES_OCR.value:
        return "requires_ocr"
    if status == QualityStatus.NEEDS_HUMAN_REVIEW.value:
        return "needs_human_review"
    return "failed"


def _prepare_full_context(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    budgets = []
    for group in groups_in_order():
        prompt = deps.prompts[group]
        budget = deps.extraction.check_context(
            context_text=deps.cache.canonical_text, prompt=prompt
        )
        budgets.append(
            f"{group.value}: {budget.context_tokens}/{budget.input_budget_tokens} tokens "
            f"({budget.counter_name})"
        )
    _log(deps, state).event(
        "extraction_planned",
        stage=RunStage.EXTRACTION.value,
        node="prepare_full_context",
        groups=len(budgets),
        context_chars=len(deps.cache.canonical_text),
    )
    return {
        "processing_status": "planned",
        "warnings": list(state.get("warnings") or []) + budgets,
        "last_completed_node": "prepare_full_context",
        "updated_at": _iso(deps),
    }


def _build_extraction_plan(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    plan = deps.extraction.build_plan(
        document_id=state["document_id"],
        run_id=state["run_id"],
        document_run_id=state["document_run_id"],
        prompts=deps.prompts,
    )
    deps.cache.plan = plan
    uri = _store_json(
        deps,
        prefix="art_extraction_plan",
        artifact_type=ArtifactType.TRACE,
        payload=plan.model_dump(mode="json"),
        document_id=state["document_id"],
        run_id=state["run_id"],
    )
    return {
        "extraction_plan_uri": uri,
        "extraction_plan_version": plan.plan_version,
        "last_completed_node": "build_extraction_plan",
        "updated_at": _iso(deps),
    }


def _extract_group(
    deps: ExtractionDependencies, state: Agent1AExtractionState, group: ExtractionGroup
) -> dict[str, Any]:
    plan = deps.cache.plan
    if plan is None:  # pragma: no cover - guarded by the plan node
        raise ExtractionError("the extraction plan is missing")
    step = next(item for item in plan.steps if item.group is group)
    prompt = deps.prompts[group]
    context = GroupRunContext(
        group=group,
        document_id=state["document_id"],
        run_id=state["run_id"],
        document_run_id=state["document_run_id"],
        source_sha256=state["source_sha256"],
    )
    result = deps.extraction.execute_group(
        context=context,
        plan_step=step,
        prompt=prompt,
        context_text=deps.cache.canonical_text,
        correlation_id=state.get("correlation_id"),
    )
    deps.cache.results[group] = result
    statuses = dict(state.get("group_statuses") or {})
    statuses[group.value] = GroupStatus.RUNNING.value
    counters = dict(state.get("retry_counters") or {})
    counters[group.value] = result.attempts - 1
    return {
        "current_group": group.value,
        "group_statuses": statuses,
        "retry_counters": counters,
        "processing_status": "extracting",
        "last_completed_node": f"extract_{group.value}",
        "updated_at": _iso(deps),
    }


def _persist_group(
    deps: ExtractionDependencies, state: Agent1AExtractionState, group: ExtractionGroup
) -> dict[str, Any]:
    result = deps.cache.results.get(group)
    if result is None:
        raise ExtractionError(f"group {group.value!r} has no result to persist")
    uri = _store_json(
        deps,
        prefix=f"art_{group.value}",
        artifact_type=ArtifactType.EXTRACTION_CANDIDATES,
        payload=result.model_dump(mode="json"),
        document_id=state["document_id"],
        run_id=state["run_id"],
    )
    statuses = dict(state.get("group_statuses") or {})
    statuses[group.value] = result.status.value
    uris = dict(state.get("group_result_uris") or {})
    uris[group.value] = uri
    completed = list(state.get("completed_groups") or [])
    if result.status is GroupStatus.COMPLETED and group.value not in completed:
        completed.append(group.value)
    return {
        "group_statuses": statuses,
        "group_result_uris": uris,
        "completed_groups": completed,
        "last_completed_node": f"validate_and_persist_{group.value}",
        "updated_at": _iso(deps),
    }


def _aggregate_extraction_results(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    quality = deps.cache.quality_report
    if quality is None:  # pragma: no cover
        raise ExtractionError("the quality report is missing")
    aggregate = deps.extraction.aggregate(
        results=list(deps.cache.results.values()),
        document_id=state["document_id"],
        document_run_id=state["document_run_id"],
        run_id=state["run_id"],
        source_sha256=state["source_sha256"],
        quality_status=quality.overall_status,
        quality_warnings=list(deps.cache.warnings),
    )
    deps.cache.aggregate = aggregate
    uri = _store_json(
        deps,
        prefix="art_extraction_result",
        artifact_type=ArtifactType.EXTRACTION_CANDIDATES,
        payload=aggregate.model_dump(mode="json"),
        document_id=state["document_id"],
        run_id=state["run_id"],
    )
    return {
        "extraction_result_uri": uri,
        "processing_status": "aggregated",
        "last_completed_node": "aggregate_extraction_results",
        "updated_at": _iso(deps),
    }


def _build_manifest(
    deps: ExtractionDependencies,
    state: Agent1AExtractionState,
    *,
    final_status: str | None = None,
    errors: tuple[str, ...] = (),
    interrupt_reason: str | None = None,
) -> ExtractionRunManifest:
    manifest = deps.cache.manifest
    quality = deps.cache.quality_report
    if manifest is None or quality is None:  # pragma: no cover
        raise ExtractionError("the document manifest or quality report is missing")
    now = _iso(deps)
    warnings = list(state.get("warnings") or [])
    derived_status = (
        "completed"
        if all(
            (state.get("group_statuses") or {}).get(group.value) == GroupStatus.COMPLETED.value
            for group in groups_in_order()
        )
        else (state.get("processing_status") or "unknown")
    )
    return ExtractionRunManifest(
        run_id=state["run_id"],
        document_run_id=state["document_run_id"],
        document_id=state["document_id"],
        source_sha256=state["source_sha256"],
        agent_id="1A",
        agent_version=state.get("agent_version") or "0.3.0",
        workflow_version=deps.settings.agent_1a.workflow_version,
        graph_state_version=deps.settings.agent_1a.graph_state_version,
        retrieval_mode=state.get("retrieval_mode") or "full_context",
        execution_mode=deps.extraction.execution_mode,
        provider_id=deps.extraction.provider_id,
        model_id=deps.extraction.model_id,
        prompt_set_version=deps.settings.agent_1a.prompt_set_version,
        group_statuses=dict(state.get("group_statuses") or {}),
        group_result_uris=dict(state.get("group_result_uris") or {}),
        quality_status=quality.overall_status.value,
        source_manifest_uri=state.get("source_manifest_uri") or manifest.source_artifact_uri,
        parsed_document_uri=str(state.get("parsed_document_uri") or ""),
        canonical_text_uri=str(state.get("canonical_text_uri") or ""),
        quality_report_uri=str(state.get("quality_report_uri") or ""),
        extraction_plan_uri=state.get("extraction_plan_uri"),
        extraction_result_uri=state.get("extraction_result_uri"),
        final_status=final_status or derived_status,
        interrupt_reason=(
            interrupt_reason if interrupt_reason is not None else state.get("interrupt_reason")
        ),
        retry_counts=dict(state.get("retry_counters") or {}),
        warnings=warnings[-30:],
        errors=list(errors)[-20:],
        started_at=state.get("created_at") or now,
        completed_at=now,
        created_at=now,
    )


def _publish_terminal_manifest(
    deps: ExtractionDependencies,
    state: Agent1AExtractionState,
    *,
    final_status: str,
    errors: tuple[str, ...] = (),
    interrupt_reason: str | None = None,
) -> tuple[str | None, str | None]:
    """Publish the canonical terminal manifest once.

    Returns ``(uri, warning)``: the warning is set (and the URI is ``None``) when the Artifact
    Store cannot publish it — the caller must not pretend it succeeded.
    """
    existing = state.get("run_manifest_uri")
    if existing:
        return existing, None
    if deps.cache.manifest is None or deps.cache.quality_report is None:
        # The document context never loaded (missing run, unreadable artefacts, integrity
        # mismatch): there is nothing to describe, and the original error must stay intact.
        return None, "run manifest was not published: the document context was not loaded"
    try:
        manifest = _build_manifest(
            deps,
            state,
            final_status=final_status,
            errors=errors,
            interrupt_reason=interrupt_reason,
        )
        uri = _store_json(
            deps,
            prefix="art_extraction_manifest",
            artifact_type=ArtifactType.RUN_LOG,
            payload=manifest.model_dump(mode="json"),
            document_id=state["document_id"],
            run_id=state["run_id"],
        )
    except ExtractionStorageError as exc:
        return None, f"run manifest was not published: {exc.message}"
    except ExtractionError as exc:
        return None, f"run manifest was not published: {exc.message}"
    return uri, None


def _persist_run_manifest(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    uri, warning = _publish_terminal_manifest(deps, state, final_status="completed")
    if uri is None:  # a successful run must not silently lose its manifest
        raise ExtractionStorageError(warning or "run manifest was not published")
    return {
        "run_manifest_uri": uri,
        "processing_status": "manifest_persisted",
        "last_completed_node": "persist_run_manifest",
        "updated_at": _iso(deps),
    }


def _finalize_run(deps: ExtractionDependencies, state: Agent1AExtractionState) -> dict[str, Any]:
    warnings = list(state.get("warnings") or [])
    _ensure_run(deps, state)
    deps.run_manager.complete_run(state["run_id"], warnings=warnings[-30:])
    _log(deps, state).event(
        "run_completed",
        stage=RunStage.EXPORT.value,
        node="finalize_run",
        groups=len(state.get("completed_groups") or []),
    )
    return {
        "processing_status": "completed",
        "last_completed_node": "finalize_run",
        "updated_at": _iso(deps),
    }


def _controlled_stop(deps: ExtractionDependencies, state: Agent1AExtractionState) -> dict[str, Any]:
    reason = f"requires_ocr: {state.get('quality_status')}"
    uri, manifest_warning = _publish_terminal_manifest(
        deps, state, final_status="requires_ocr", interrupt_reason=reason
    )
    _ensure_run(deps, state)
    deps.run_manager.request_human_review(state["run_id"], reason=reason)
    _log(deps, state).event(
        "run_waiting_for_human",
        level="WARNING",
        stage=RunStage.QUALITY_GATE.value,
        reason=reason,
        stop_kind="requires_ocr",
    )
    warnings = list(state.get("warnings") or [])
    if manifest_warning:
        warnings.append(manifest_warning)
    return {
        "processing_status": "requires_ocr",
        "interrupt_reason": reason,
        "run_manifest_uri": uri,
        "warnings": warnings,
        "last_completed_node": "controlled_stop",
        "updated_at": _iso(deps),
    }


def _prepare_human_review_stop(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    """Persist the stop reason and the manifest *before* the LangGraph interrupt."""
    reason = "needs_human_review: the quality gate requires a human decision"
    uri, manifest_warning = _publish_terminal_manifest(
        deps, state, final_status="needs_human_review", interrupt_reason=reason
    )
    _ensure_run(deps, state)
    deps.run_manager.request_human_review(state["run_id"], reason=reason)
    _log(deps, state).event(
        "run_waiting_for_human",
        level="WARNING",
        stage=RunStage.QUALITY_GATE.value,
        reason=reason,
        stop_kind="needs_human_review",
    )
    warnings = list(state.get("warnings") or [])
    if manifest_warning:
        warnings.append(manifest_warning)
    return {
        "processing_status": "needs_human_review",
        "interrupt_reason": reason,
        "run_manifest_uri": uri,
        "warnings": warnings,
        "last_completed_node": "prepare_human_review_stop",
        "updated_at": _iso(deps),
    }


def _interrupt_for_review(
    deps: ExtractionDependencies, state: Agent1AExtractionState
) -> dict[str, Any]:
    """Park the graph; the reason is already persisted by the previous node."""
    reason = str(state.get("interrupt_reason") or "needs_human_review")
    interrupt({"run_id": state["run_id"], "reason": reason})
    return {}  # pragma: no cover - execution stops inside interrupt()


def _fail_run(deps: ExtractionDependencies, state: Agent1AExtractionState) -> dict[str, Any]:
    reason = state.get("interrupt_reason") or "quality gate rejected the document"
    run_id = state.get("run_id") or ""
    error_code = RUN_ERROR_CODES[ErrorCategory.QUALITY]
    if run_id:
        _ensure_run(deps, state)
        deps.run_manager.fail_run(
            run_id,
            RunError.model_validate(
                {
                    "code": error_code,
                    "message": reason[:500],
                    "stage": RunStage.QUALITY_GATE,
                    "retryable": False,
                    "correlation_id": state.get("correlation_id") or "corr_unset",
                }
            ),
        )
    uri, manifest_warning = _publish_terminal_manifest(
        deps,
        state,
        final_status="failed",
        errors=(f"quality:{error_code}: {reason}",),
        interrupt_reason=reason,
    )
    _log(deps, state).event(
        "run_failed",
        level="ERROR",
        stage=RunStage.QUALITY_GATE.value,
        reason=reason,
        error_code=error_code,
    )
    warnings = list(state.get("warnings") or [])
    if manifest_warning:
        warnings.append(manifest_warning)
    return {
        "processing_status": "failed",
        "interrupt_reason": reason,
        "run_manifest_uri": uri,
        "warnings": warnings,
        "last_completed_node": "fail_run",
        "updated_at": _iso(deps),
    }


# ------------------------------------------------------------------------- assembly


def build_extraction_graph(
    deps: ExtractionDependencies,
    *,
    checkpointer: object | None = None,
    interrupt_after: tuple[str, ...] = (),
) -> Any:
    """Build the extraction graph; ``interrupt_after`` is used by resume tests."""

    def node(fn: Callable[..., dict[str, Any]], group: ExtractionGroup | None = None) -> Any:
        def run(state: Agent1AExtractionState) -> dict[str, Any]:
            validate_extraction_state(state)
            _hydrate_cache(deps, state)
            try:
                if group is None:
                    return fn(deps, state)
                return fn(deps, state, group)
            except ExtractionError as exc:
                published = _record_failure(deps, state, exc)
                exc.manifest_published = published is not None  # type: ignore[attr-defined]
                raise

        return run

    def route_after_group(
        finished: ExtractionGroup, next_group: ExtractionGroup | None
    ) -> Callable[[Agent1AExtractionState], str]:
        """Continue only when every group up to ``finished`` completed."""

        def route(state: Agent1AExtractionState) -> str:
            statuses = state.get("group_statuses") or {}
            for group in groups_in_order():
                if group.order > finished.order:
                    break
                if statuses.get(group.value) != GroupStatus.COMPLETED.value:
                    return "fail"
            return "next" if next_group is not None else "aggregate"

        return route

    # LangGraph's `StateT` bound rejects `total=False` TypedDicts at type level while
    # accepting them at runtime; the cast is confined to this single construction site.
    builder: Any = StateGraph(cast(Any, Agent1AExtractionState))
    builder.add_node("validate_extraction_input", node(_validate_extraction_input))
    builder.add_node("initialize_agent1a_run", node(_initialize_agent1a_run))
    builder.add_node("load_parsed_document", node(_load_parsed_document))
    builder.add_node("check_quality_gate", node(_check_quality_gate))
    builder.add_node("prepare_full_context", node(_prepare_full_context))
    builder.add_node("build_extraction_plan", node(_build_extraction_plan))

    sequence = groups_in_order()
    for group in sequence:
        builder.add_node(f"extract_{group.value}", node(_extract_group, group))
        builder.add_node(f"validate_and_persist_{group.value}", node(_persist_group, group))

    builder.add_node("aggregate_extraction_results", node(_aggregate_extraction_results))
    builder.add_node("persist_run_manifest", node(_persist_run_manifest))
    builder.add_node("finalize_run", node(_finalize_run))
    builder.add_node("controlled_stop", node(_controlled_stop))
    builder.add_node("prepare_human_review_stop", node(_prepare_human_review_stop))
    builder.add_node("interrupt_for_review", node(_interrupt_for_review))
    builder.add_node("fail_run", node(_fail_run))

    builder.add_edge(START, "validate_extraction_input")
    builder.add_edge("validate_extraction_input", "initialize_agent1a_run")
    builder.add_edge("initialize_agent1a_run", "load_parsed_document")
    builder.add_edge("load_parsed_document", "check_quality_gate")
    builder.add_conditional_edges(
        "check_quality_gate",
        _route_quality,
        {
            "proceed": "prepare_full_context",
            "requires_ocr": "controlled_stop",
            "needs_human_review": "prepare_human_review_stop",
            "failed": "fail_run",
        },
    )
    builder.add_edge("prepare_full_context", "build_extraction_plan")

    for index, group in enumerate(sequence):
        previous = (
            "build_extraction_plan"
            if index == 0
            else f"validate_and_persist_{sequence[index - 1].value}"
        )
        builder.add_edge(previous, f"extract_{group.value}")
        builder.add_edge(f"extract_{group.value}", f"validate_and_persist_{group.value}")
        following = sequence[index + 1] if index + 1 < len(sequence) else None
        builder.add_conditional_edges(
            f"validate_and_persist_{group.value}",
            route_after_group(group, following),
            {
                "next": f"extract_{following.value}"
                if following
                else "aggregate_extraction_results",
                "aggregate": "aggregate_extraction_results",
                "fail": "fail_run",
            },
        )

    builder.add_edge("aggregate_extraction_results", "persist_run_manifest")
    builder.add_edge("persist_run_manifest", "finalize_run")
    for terminal in ("finalize_run", "controlled_stop", "fail_run"):
        builder.add_edge(terminal, END)
    builder.add_edge("prepare_human_review_stop", "interrupt_for_review")
    builder.add_edge("interrupt_for_review", END)

    compiled = builder.compile(
        checkpointer=checkpointer,  # type: ignore[arg-type]
        interrupt_after=list(interrupt_after),
    )
    return compiled


def _interrupt_reason(interrupts: object) -> str | None:
    """Best-effort reason carried by a LangGraph interrupt payload."""
    items = interrupts if isinstance(interrupts, (list, tuple)) else [interrupts]
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict) and value.get("reason"):
            return str(value["reason"])
        if isinstance(value, str) and value:
            return value
    return None


def _record_failure(
    deps: ExtractionDependencies,
    state: Agent1AExtractionState,
    exc: ExtractionError,
) -> str | None:
    """Register one terminal failure exactly once and publish its manifest.

    ``fail_run`` both appends the ``RunError`` and transitions the run, so calling
    ``record_error`` here as well would duplicate the error.  The return value is the manifest
    URI (``None`` when the Artifact Store is unavailable), which the caller exposes to the CLI.
    """
    run_id = state.get("run_id") or ""
    if not run_id:
        return None
    error = exc.to_run_error(correlation_id=state.get("correlation_id"))
    try:
        _ensure_run(deps, state)
        run = _run_or_none(deps, run_id)
        if run is None or run.status is not RunStatus.FAILED:
            deps.run_manager.fail_run(run_id, error)
    except Exception:
        pass
    uri, manifest_warning = _publish_terminal_manifest(
        deps,
        state,
        final_status="failed",
        errors=(f"{exc.category.value}:{error.code}: {error.message}",),
    )
    exc.manifest_publication_note = manifest_warning  # type: ignore[attr-defined]
    log = _log(deps, state)
    log.event(
        "run_failed",
        level="ERROR",
        stage=exc.stage.value,
        error_category=exc.category.value,
        error_code=error.code,
        node=state.get("last_completed_node") or None,
    )
    if manifest_warning:
        log.event(
            "run_manifest_publish_failed",
            level="ERROR",
            stage=exc.stage.value,
            reason=manifest_warning,
        )
    return uri


# -------------------------------------------------------------------------- runner


class Agent1AExtractionRunner:
    """Drives one extraction run and exposes its checkpointed state."""

    def __init__(
        self,
        *,
        graph: Any,
        deps: ExtractionDependencies,
        thread_id_for_run: Callable[[str], str],
    ) -> None:
        self._graph = graph
        self._deps = deps
        self._thread_id_for_run = thread_id_for_run

    @property
    def dependencies(self) -> ExtractionDependencies:
        """Dependencies of the runner (used by tests and the CLI)."""
        return self._deps

    def run(
        self,
        *,
        document_run_id: str,
        correlation_id: str,
        agent_version: str = "0.3.0",
    ) -> ExtractionOutcome:
        """Start a new extraction run for a finished document run."""
        state = initial_extraction_state(
            document_run_id=document_run_id,
            correlation_id=correlation_id,
            agent_version=agent_version,
        )
        # Allocate the id first: the checkpoint thread must be the run id from step one.
        state["run_id"] = self._deps.run_manager.new_run_id()
        validate_extraction_state(state)
        return self._invoke(state, new_run=True)

    def resume(self, *, run_id: str, correlation_id: str = "corr_resume") -> ExtractionOutcome:
        """Continue a checkpointed run from its last completed node."""
        config = self._config_for(run_id)
        snapshot = self._checkpoint(lambda: self._graph.get_state(config))
        checkpoint_state = _as_state(getattr(snapshot, "values", None))
        if not checkpoint_state:
            raise ExtractionInputError(f"no checkpoint found for run {run_id!r}; nothing to resume")
        run = _run_or_none(self._deps, run_id)
        if run is not None and run.status is RunStatus.WAITING_FOR_HUMAN:
            reason = str(checkpoint_state.get("interrupt_reason") or "manual review required")
            return ExtractionOutcome(
                state=checkpoint_state,
                run=run,
                paused=True,
                stop_reason=reason,
            )
        pending_nodes = tuple(getattr(snapshot, "next", ()) or ())
        if not pending_nodes:
            return ExtractionOutcome(state=checkpoint_state, run=run)
        self._deps.logger.bind(run_id=run_id, correlation_id=correlation_id).event(
            "workflow_resumed", stage=RunStage.EXTRACTION.value
        )
        record = _ensure_run(self._deps, checkpoint_state)
        if record is not None and record.status is RunStatus.CREATED:
            # a re-materialised record starts as ``created``; a live one is already running
            self._deps.run_manager.resume_run(run_id)
        # ``invoke(None)`` continues from the checkpoint; passing the state again would
        # restart the graph from START and re-run finished groups.
        return self._invoke(None, new_run=False, config=config)

    def state_for_run(self, run_id: str) -> Agent1AExtractionState | None:
        """Read the checkpointed state of a run, if any."""
        snapshot = self._checkpoint(lambda: self._graph.get_state(self._config_for(run_id)))
        values = _as_state(getattr(snapshot, "values", None))
        return values or None

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _checkpoint(operation: Callable[[], Any]) -> Any:
        """Run a checkpoint-store operation, translating storage failures into CHECKPOINT.

        Only ``OSError``/``sqlite3.Error`` (the adapter failure surface) are translated;
        business errors raised by graph nodes keep their own category.
        """
        try:
            return operation()
        except (OSError, sqlite3.Error) as exc:
            raise ExtractionCheckpointError(f"checkpoint store operation failed: {exc}") from exc

    def _config_for(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": self._thread_id_for_run(run_id)}}

    def _invoke(
        self,
        state: Agent1AExtractionState | None,
        *,
        new_run: bool,
        config: dict[str, Any] | None = None,
    ) -> ExtractionOutcome:
        from langgraph.errors import GraphInterrupt

        config = config or self._config_for((state or {}).get("run_id") or "")
        try:
            final_state = self._checkpoint(lambda: self._graph.invoke(state, config=config))
        except GraphInterrupt:
            snapshot = self._graph.get_state(config)
            values = _as_state(getattr(snapshot, "values", None))
            run = _run_or_none(self._deps, str(values.get("run_id") or ""))
            return ExtractionOutcome(
                state=values,
                run=run,
                paused=True,
                stop_reason=str(values.get("interrupt_reason") or "graph interrupted"),
            )
        # LangGraph returns the state after the last executed node.  An ``interrupt()`` parked
        # inside a node comes back in the private ``__interrupt__`` channel, not as an
        # exception, so it has to be detected here.
        merged = _as_state(state)
        payload = dict(final_state or {})
        interrupts = payload.pop("__interrupt__", None) or ()
        merged.update(cast(Any, payload))
        run = _run_or_none(self._deps, merged.get("run_id") or "") if merged.get("run_id") else None
        if interrupts:
            return ExtractionOutcome(
                state=merged,
                run=run,
                paused=True,
                stop_reason=_interrupt_reason(interrupts)
                or str(merged.get("interrupt_reason") or "graph interrupted"),
            )
        return ExtractionOutcome(state=merged, run=run)
