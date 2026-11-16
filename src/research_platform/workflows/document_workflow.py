"""Document-processing workflow: ingestion → parsing → quality gate.

Shape (a separate graph from the technical smoke workflow, which stays untouched)::

    START → validate_input → initialize_run → ingest_source → inspect_document
          → route_by_format ─┬→ parse_pdf ─┐
                              └→ parse_txt ─┴→ build_canonical_document
          → run_quality_gate → persist_parsed_artifacts → finalize_run → END

Failure policy: a node that raises stops the graph; the runner normalises the error, marks
the run failed and re-raises.  A controlled stop (``requires_ocr`` / ``needs_human_review``)
is *not* an exception: every artefact is stored first, then the run is completed with the
gate's verdict and the reason is written into the state and the log.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, cast

from langgraph.graph import END, START, StateGraph

from research_platform.application.ports.document_pipeline import DocumentInspectorPort
from research_platform.application.ports.logging import LoggerPort
from research_platform.application.services.artifact_ids import run_artifact_id
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.document_errors import (
    DocumentIngestionError,
    DocumentPipelineError,
)
from research_platform.application.services.document_ingestion import (
    DocumentIngestionService,
    IngestedSource,
)
from research_platform.application.services.document_parsing import (
    DocumentParsingService,
    ParsedOutcome,
)
from research_platform.application.services.quality_gate import QualityGateService
from research_platform.application.services.run_manager import RunManager, StartRunRequest
from research_platform.config import PlatformSettings
from research_platform.domain.documents.identity import DocumentSourceType
from research_platform.domain.documents.quality import QualityReport, QualityStatus
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.domain.runs.models import (
    RunError,
    RunRecord,
    RunStage,
    RunStatus,
    generate_correlation_id,
)
from research_platform.workflows.document_state import (
    DocumentProcessingState,
    initial_document_state,
)

__all__ = [
    "DocumentRunResult",
    "DocumentWorkflow",
    "DocumentWorkflowDependencies",
    "build_document_workflow",
]

_INGESTION: Final = RunStage.REGISTRATION
_PARSING: Final = RunStage.PARSING
_QUALITY: Final = RunStage.QUALITY_GATE
_EXPORT: Final = RunStage.EXPORT


@dataclass
class DocumentRuntimeCache:
    """Per-run in-memory hand-off between nodes.

    Large objects (source bytes, the parsed document, the quality report) move between nodes
    through this cache, never through the graph state: the state stays compact and
    JSON-serialisable, and a checkpoint can be inspected without megabytes of payload.
    """

    ingested: IngestedSource | None = None
    parsed: ParsedOutcome | None = None
    report: QualityReport | None = None
    run_id: str = ""
    """Set by the first node that creates the run, so a later failure can be recorded."""


@dataclass(frozen=True, slots=True)
class DocumentWorkflowDependencies:
    """Services a node may use; supplied by the composition root."""

    settings: PlatformSettings
    run_manager: RunManager
    artifacts: ArtifactService
    ingestion: DocumentIngestionService
    parsing: DocumentParsingService
    quality_gate: QualityGateService
    inspector: DocumentInspectorPort
    logger: LoggerPort
    cache: DocumentRuntimeCache


@dataclass(frozen=True, slots=True)
class DocumentRunResult:
    """Outcome handed back to the CLI."""

    run_id: str
    document_id: str
    status: RunStatus
    quality_report: QualityReport | None
    state: DocumentProcessingState


class DocumentWorkflow:
    """Compiled document workflow plus the runner that owns its lifecycle."""

    def __init__(self, *, graph: Any, dependencies: DocumentWorkflowDependencies) -> None:
        self._graph = graph
        self._deps = dependencies

    @property
    def graph(self) -> Any:
        """Compiled graph (exposed for health checks and tests)."""
        return self._graph

    def run(
        self,
        *,
        input_path: str,
        correlation_id: str | None = None,
        dry_run: bool = False,
    ) -> DocumentRunResult:
        """Execute the workflow once for one file.

        Raises:
            DocumentPipelineError: a normalised pipeline failure (the run is marked failed).
        """
        state = initial_document_state(
            input_path=input_path,
            correlation_id=correlation_id or generate_correlation_id(),
        )
        if dry_run:
            self._deps.ingestion.validate_input(input_path)
            state["processing_status"] = "dry_run_validated"
            return DocumentRunResult(
                run_id="",
                document_id="",
                status=RunStatus.CREATED,
                quality_report=None,
                state=state,
            )
        config = {"recursion_limit": self._deps.settings.runtime.max_graph_steps * 4}
        if dry_run:
            state["processing_status"] = "dry_run"
        try:
            final = self._graph.invoke(state, config=config)
        except DocumentPipelineError as exc:
            self._fail(state=state, error=exc)
            raise
        except Exception as exc:
            wrapped = DocumentPipelineError(f"{type(exc).__name__}: {exc}")
            self._fail(state=state, error=wrapped)
            raise wrapped from exc

        run_id = final.get("run_id") or ""
        record = self._deps.run_manager.get_run(run_id) if run_id else None
        report = self._load_report(final)
        return DocumentRunResult(
            run_id=run_id,
            document_id=final.get("document_id", ""),
            status=record.status if record is not None else RunStatus.CREATED,
            quality_report=report,
            state=final,
        )

    # -------------------------------------------------------------------- helpers

    def _load_report(self, state: DocumentProcessingState) -> QualityReport:
        cached = self._deps.cache.report
        if cached is not None:
            return cached
        uri = state.get("quality_report_uri")
        if not uri:
            raise DocumentPipelineError("the workflow finished without a quality report")
        payload = self._deps.artifacts.read_by_id(uri.rsplit("/", 1)[-1])
        return QualityReport.model_validate_json(payload)

    def _fail(self, *, state: DocumentProcessingState, error: DocumentPipelineError) -> None:
        # ``graph.invoke`` does not mutate the caller's dict, so a node that failed after
        # creating the run is only known through the shared runtime cache.
        run_id = self._deps.cache.run_id or state.get("run_id") or ""
        correlation_id = state.get("correlation_id") or "corr_unset"
        log = self._deps.logger.bind(run_id=run_id or None, correlation_id=correlation_id)
        if run_id:
            try:
                self._deps.run_manager.fail_run(
                    run_id, error.to_run_error(correlation_id=correlation_id)
                )
            except Exception:
                log.event(
                    "runtime_error",
                    level="ERROR",
                    error_code="run_not_marked_failed",
                    stage=error.stage.value,
                )
        log.event(
            "runtime_error",
            level="ERROR",
            error_code=error.code,
            stage=error.stage.value,
            retryable=error.retryable,
            exception_type=type(error).__name__,
        )


# ----------------------------------------------------------------------- nodes


def _validate_input(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    log = deps.logger.bind(correlation_id=state.get("correlation_id"))
    log.event("stage_started", stage=_INGESTION.value)
    path = state.get("input_path") or ""
    if not path:
        raise DocumentPipelineError("no input path was provided")
    deps.ingestion.validate_input(path)
    return {"processing_status": "validated"}


def _initialize_run(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    identity = deps.ingestion.probe_identity(state["input_path"])
    record = deps.run_manager.start_run(
        StartRunRequest(
            document_id=identity.document_id,
            agent_id="1A",
            agent_version="0.2.0",
            contract_version="1.0",
            correlation_id=state.get("correlation_id"),
            metadata={"purpose": "document_processing"},
        )
    )
    deps.cache.run_id = record.run_id
    deps.logger.bind(run_id=record.run_id, correlation_id=record.correlation_id).event(
        "run_started", stage=_INGESTION.value, document_id=identity.document_id
    )
    return {
        "run_id": record.run_id,
        "document_id": identity.document_id,
        "source_sha256": identity.source_sha256,
        "source_type": identity.source_type.value,
        "processing_status": "running",
    }


def _ingest_source(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    ingested: IngestedSource = deps.ingestion.ingest(
        input_path=state["input_path"],
        run_id=state["run_id"],
        correlation_id=state.get("correlation_id"),
    )
    if ingested.manifest.source_sha256 != state.get("source_sha256"):
        raise DocumentIngestionError(
            "source_changed_during_ingestion",
            "the file changed between identity resolution and ingestion",
        )
    deps.cache.ingested = ingested
    deps.run_manager.update_stage(state["run_id"], _INGESTION)
    return {
        "source_artifact_uri": ingested.manifest.source_artifact_uri,
        "manifest_artifact_uri": _latest_artifact_uri(
            deps, state["run_id"], ArtifactType.HANDOFF_PACKAGE
        ),
        "file_name": ingested.manifest.source_file_name,
        "source_size_bytes": ingested.manifest.source_size_bytes,
        "source_type": ingested.manifest.source_type.value,
        "language": ingested.manifest.language.value,
        "page_count": ingested.inspection.page_count if ingested.inspection else None,
        "processing_status": "ingested",
    }


def _inspect_document(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    deps.run_manager.update_stage(state["run_id"], _INGESTION)
    warnings = list(state.get("warnings") or [])
    if state.get("source_type") == DocumentSourceType.TXT.value and state.get("page_count"):
        warnings.append("a text source must not report a page count")
    return {"warnings": warnings, "processing_status": "inspected"}


def _parse_pdf(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    return _parse(state, deps)


def _parse_txt(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    return _parse(state, deps)


def _parse(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    deps.run_manager.update_stage(state["run_id"], _PARSING)
    ingested = deps.cache.ingested
    if ingested is None:  # pragma: no cover - the graph guarantees the order
        raise DocumentPipelineError("internal error: the source was not ingested")
    outcome: ParsedOutcome = deps.parsing.parse(
        ingested=ingested, correlation_id=state.get("correlation_id")
    )
    deps.cache.parsed = outcome
    return {
        "parsed_document_uri": outcome.parsed_document_uri,
        "canonical_text_uri": outcome.document.canonical_text.artifact_uri,
        "parser_name": outcome.document.parser.parser_name,
        "parser_version": outcome.document.parser.parser_version,
        "language": outcome.document.language,
        "used_fallback": outcome.used_fallback,
        "page_count": outcome.document.page_count,
        "warnings": list(dict.fromkeys([*(state.get("warnings") or []), *outcome.warnings])),
        "processing_status": "parsed",
    }


def _build_canonical_document(
    state: DocumentProcessingState, deps: DocumentWorkflowDependencies
) -> dict:
    deps.run_manager.update_stage(state["run_id"], _PARSING)
    # An empty canonical text is a legitimate outcome for a fully scanned document: the
    # Quality Gate decides whether the document requires OCR. Failing here would turn a
    # routable document into a technical error.
    deps.artifacts.read_by_id(_artifact_id(state["canonical_text_uri"]))
    return {"processing_status": "canonicalized"}


def _run_quality_gate(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    deps.run_manager.update_stage(state["run_id"], _QUALITY)
    ingested = deps.cache.ingested
    outcome = deps.cache.parsed
    if ingested is None or outcome is None:  # pragma: no cover - graph order guarantee
        raise DocumentPipelineError("internal error: the document was not parsed")
    report = deps.quality_gate.evaluate(
        document=outcome.document,
        canonical_text=outcome.canonical_text,
        inspection=ingested.inspection,
        used_fallback=outcome.used_fallback,
        correlation_id=state.get("correlation_id"),
    )
    deps.cache.report = report
    return {
        "quality_status": report.overall_status.value,
        "ocr_required_pages": list(report.ocr_required_pages),
        "processing_status": "quality_checked",
    }


def _persist_parsed_artifacts(
    state: DocumentProcessingState, deps: DocumentWorkflowDependencies
) -> dict:
    report = deps.cache.report
    outcome = deps.cache.parsed
    if report is None or outcome is None:  # pragma: no cover - graph order guarantee
        raise DocumentPipelineError("internal error: no quality report to persist")
    stored_report = deps.artifacts.store_bytes(
        data=report.model_dump_json(indent=2).encode("utf-8"),
        artifact_id=run_artifact_id(
            "art_quality", document_id=state["document_id"], run_id=state["run_id"]
        ),
        artifact_type=ArtifactType.QUALITY_REPORT,
        media_type="application/json",
        document_id=state["document_id"],
        run_id=state["run_id"],
        schema_version=report.quality_policy_version,
        correlation_id=state.get("correlation_id"),
    )
    diagnostics = {
        "run_id": state["run_id"],
        "document_id": state["document_id"],
        "source_type": state.get("source_type"),
        "page_count": state.get("page_count"),
        "parser": {
            "name": outcome.document.parser.parser_name,
            "version": outcome.document.parser.parser_version,
            "pipeline": outcome.document.parser.pipeline_version,
            "options_hash": outcome.document.parser.parser_options_hash,
            "ocr_enabled": outcome.document.parser.ocr_enabled,
            "tables": outcome.document.parser.table_extraction_enabled,
        },
        "used_fallback": outcome.used_fallback,
        "warnings": list(state.get("warnings") or []),
        "quality_status": report.overall_status.value,
        "quality_checks": [
            {"id": check.check_id, "status": check.status.value} for check in report.checks
        ],
    }
    stored_diagnostics = deps.artifacts.store_bytes(
        data=json.dumps(diagnostics, indent=2, sort_keys=True).encode("utf-8"),
        artifact_id=run_artifact_id(
            "art_diag", document_id=state["document_id"], run_id=state["run_id"]
        ),
        artifact_type=ArtifactType.RUN_LOG,
        media_type="application/json",
        document_id=state["document_id"],
        run_id=state["run_id"],
        correlation_id=state.get("correlation_id"),
    )
    return {
        "quality_report_uri": stored_report.uri,
        "diagnostics_uri": stored_diagnostics.uri,
        "quality_status": report.overall_status.value,
        "ocr_required_pages": list(report.ocr_required_pages),
        "processing_status": "artifacts_persisted",
    }


def _finalize_run(state: DocumentProcessingState, deps: DocumentWorkflowDependencies) -> dict:
    run_id = state["run_id"]
    status = QualityStatus(state.get("quality_status") or QualityStatus.FAILED.value)
    warnings = list(state.get("warnings") or [])
    if status.is_ready:
        deps.run_manager.complete_run(run_id, warnings=warnings)
        processing = "completed" if status is QualityStatus.PASSED else "completed_with_warnings"
        stop_reason: str | None = None
    elif status is QualityStatus.REQUIRES_OCR:
        deps.run_manager.request_human_review(run_id, reason="ocr engine is not approved")
        processing = "requires_ocr"
        stop_reason = "ocr_required: no approved OCR engine (OI-008)"
    elif status is QualityStatus.NEEDS_HUMAN_REVIEW:
        deps.run_manager.request_human_review(run_id, reason="quality gate flagged the document")
        processing = "needs_human_review"
        stop_reason = "needs_human_review: the quality gate flagged the extraction"
    else:
        deps.run_manager.fail_run(
            run_id,
            RunError(
                code="document_quality_failed",
                message="the quality gate rejected the document",
                stage=_QUALITY,
                retryable=False,
                correlation_id=state.get("correlation_id") or "corr_unset",
            ),
        )
        processing = "failed"
        stop_reason = "failed: the quality gate rejected the extraction"
    deps.logger.bind(run_id=run_id, correlation_id=state.get("correlation_id")).event(
        "run_completed",
        stage=_QUALITY.value,
        status=processing,
        quality_status=status.value,
    )
    return {
        "processing_status": processing,
        "stop_reason": stop_reason,
        "quality_status": status.value,
    }


# ---------------------------------------------------------------------- helpers


def _artifact_id(uri: str | None) -> str:
    if not uri:
        raise DocumentPipelineError("a required artefact URI is missing")
    return uri.rsplit("/", 1)[-1]


def _latest_artifact_uri(
    deps: DocumentWorkflowDependencies, run_id: str, artifact_type: ArtifactType
) -> str | None:
    for metadata in reversed(deps.artifacts.list_for_run(run_id)):
        if metadata.artifact_type is artifact_type:
            return metadata.uri
    return None


# ------------------------------------------------------------------------ graph


def build_document_workflow(
    dependencies: DocumentWorkflowDependencies,
) -> DocumentWorkflow:
    """Assemble and compile the document-processing graph."""

    def node(
        function: Callable[[DocumentProcessingState, DocumentWorkflowDependencies], dict],
    ) -> Callable[[DocumentProcessingState], dict]:
        def invoke(state: DocumentProcessingState) -> dict:
            return function(state, dependencies)

        return invoke

    def route(state: DocumentProcessingState) -> str:
        return "pdf" if state.get("source_type") == DocumentSourceType.PDF.value else "txt"

    # Narrow, documented exception: LangGraph's ``StateT`` bound does not accept a
    # ``total=False`` TypedDict, although the runtime accepts exactly that. The cast is
    # confined to this single construction site; every node keeps its own precise signature.
    builder: Any = StateGraph(cast(Any, DocumentProcessingState))
    builder.add_node("validate_input", node(_validate_input))
    builder.add_node("initialize_run", node(_initialize_run))
    builder.add_node("ingest_source", node(_ingest_source))
    builder.add_node("inspect_document", node(_inspect_document))
    builder.add_node("parse_pdf", node(_parse_pdf))
    builder.add_node("parse_txt", node(_parse_txt))
    builder.add_node("build_canonical_document", node(_build_canonical_document))
    builder.add_node("run_quality_gate", node(_run_quality_gate))
    builder.add_node("persist_parsed_artifacts", node(_persist_parsed_artifacts))
    builder.add_node("finalize_run", node(_finalize_run))

    builder.add_edge(START, "validate_input")
    builder.add_edge("validate_input", "initialize_run")
    builder.add_edge("initialize_run", "ingest_source")
    builder.add_edge("ingest_source", "inspect_document")
    builder.add_conditional_edges(
        "inspect_document", route, {"pdf": "parse_pdf", "txt": "parse_txt"}
    )
    builder.add_edge("parse_pdf", "build_canonical_document")
    builder.add_edge("parse_txt", "build_canonical_document")
    builder.add_edge("build_canonical_document", "run_quality_gate")
    builder.add_edge("run_quality_gate", "persist_parsed_artifacts")
    builder.add_edge("persist_parsed_artifacts", "finalize_run")
    builder.add_edge("finalize_run", END)

    compiled = builder.compile()
    return DocumentWorkflow(graph=compiled, dependencies=dependencies)


_ = (RunRecord, RunStage)
