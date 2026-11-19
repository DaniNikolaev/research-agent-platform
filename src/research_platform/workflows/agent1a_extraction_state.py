"""GraphState of the Agent 1A extraction workflow.

Compact by construction: identifiers, statuses, counters and artefact URIs only.  The parsed
document, the canonical text and the group payloads live in the runtime cache and in the
Artifact Store; putting them here would make every checkpoint a copy of the article.
"""

from __future__ import annotations

import re
from typing import Final, TypedDict

from research_platform.domain.extraction.models import (
    EXTRACTION_WORKFLOW_VERSION,
    ExtractionGroup,
    GroupStatus,
)

__all__ = [
    "AGENT1A_GRAPH_STATE_VERSION",
    "AGENT1A_PROCESSING_STATUSES",
    "Agent1AExtractionState",
    "initial_extraction_state",
    "validate_extraction_state",
]

AGENT1A_GRAPH_STATE_VERSION: Final = "1.0"

AGENT1A_PROCESSING_STATUSES: Final[tuple[str, ...]] = (
    "created",
    "validated",
    "initialized",
    "loaded",
    "quality_checked",
    "planned",
    "extracting",
    "extracted",
    "aggregated",
    "manifest_persisted",
    "completed",
    "requires_ocr",
    "needs_human_review",
    "ignored",
    "failed",
)

_ARTIFACT_URI_RE: Final = re.compile(
    r"^artifact://(?:documents/[a-z0-9][a-z0-9_\-]{2,63}/runs/[A-Za-z0-9][A-Za-z0-9_\-]{2,63}"
    r"|runs/[A-Za-z0-9][A-Za-z0-9_\-]{2,63}|platform)"
    r"/[a-z][a-z0-9_]{2,63}/[a-z0-9][a-z0-9_\-]{2,63}$"
)


class Agent1AExtractionState(TypedDict, total=False):
    """State of one extraction run."""

    # identifiers
    run_id: str
    document_run_id: str
    document_id: str
    source_sha256: str
    agent_id: str
    agent_version: str
    correlation_id: str

    # input artefacts
    source_manifest_uri: str | None
    parsed_document_uri: str | None
    canonical_text_uri: str | None
    quality_report_uri: str | None
    quality_status: str

    # retrieval
    chunk_manifest_uri: str | None
    retrieval_index_manifest_uri: str | None
    retrieval_manifest_uri: str | None
    group_retrieval_uris: dict[str, str]
    group_evidence_uris: dict[str, str]
    evidence_ledger_uri: str | None
    coverage_artifact_uri: str | None
    retrieval_status: str
    embedding_status: str

    # extraction
    retrieval_mode: str
    extraction_plan_version: str
    current_group: str | None
    group_statuses: dict[str, str]
    group_result_uris: dict[str, str]
    completed_groups: list[str]

    # control
    processing_status: str
    retry_counters: dict[str, int]
    warnings: list[str]
    error_reference: str | None
    last_completed_node: str
    interrupt_reason: str | None

    # results
    extraction_plan_uri: str | None
    extraction_result_uri: str | None
    run_manifest_uri: str | None
    created_at: str
    updated_at: str


def initial_extraction_state(
    *,
    document_run_id: str,
    correlation_id: str,
    agent_version: str = "0.3.0",
) -> Agent1AExtractionState:
    """Fresh state with every key present, so a checkpoint is always comparable."""
    return Agent1AExtractionState(
        run_id="",
        document_run_id=document_run_id,
        document_id="",
        source_sha256="",
        agent_id="1A",
        agent_version=agent_version,
        correlation_id=correlation_id,
        source_manifest_uri=None,
        parsed_document_uri=None,
        canonical_text_uri=None,
        quality_report_uri=None,
        quality_status="",
        chunk_manifest_uri=None,
        retrieval_index_manifest_uri=None,
        retrieval_manifest_uri=None,
        group_retrieval_uris={},
        group_evidence_uris={},
        evidence_ledger_uri=None,
        coverage_artifact_uri=None,
        retrieval_status="not_started",
        embedding_status="not_configured",
        retrieval_mode="full_context",
        extraction_plan_version="",
        current_group=None,
        group_statuses={group.value: GroupStatus.PENDING.value for group in ExtractionGroup},
        group_result_uris={},
        completed_groups=[],
        processing_status="created",
        retry_counters={},
        warnings=[],
        error_reference=None,
        last_completed_node="",
        interrupt_reason=None,
        extraction_plan_uri=None,
        extraction_result_uri=None,
        run_manifest_uri=None,
        created_at="",
        updated_at="",
    )


def validate_extraction_state(state: Agent1AExtractionState) -> None:
    """Validate the state before a node acts on it.

    Raises:
        ValueError: a required identifier, status or artefact URI is missing or invalid.
    """
    status = state.get("processing_status")
    if status not in AGENT1A_PROCESSING_STATUSES:
        raise ValueError(
            f"processing_status {status!r} is not one of {list(AGENT1A_PROCESSING_STATUSES)}"
        )
    if not state.get("correlation_id"):
        raise ValueError("correlation_id is required")
    if not state.get("document_run_id"):
        raise ValueError("document_run_id is required")

    run_id = state.get("run_id") or ""
    if run_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,63}", run_id):
        raise ValueError(f"run_id {run_id!r} is not a valid identifier")

    for key in (
        "source_manifest_uri",
        "parsed_document_uri",
        "canonical_text_uri",
        "quality_report_uri",
        "extraction_plan_uri",
        "extraction_result_uri",
        "run_manifest_uri",
        "chunk_manifest_uri",
        "retrieval_index_manifest_uri",
        "retrieval_manifest_uri",
        "evidence_ledger_uri",
        "coverage_artifact_uri",
    ):
        uri = state.get(key)
        if uri is not None and not _ARTIFACT_URI_RE.fullmatch(uri):
            raise ValueError(f"{key} {uri!r} is not a logical artefact URI")

    statuses = state.get("group_statuses") or {}
    allowed = {item.value for item in GroupStatus}
    for group, value in statuses.items():
        if group not in {item.value for item in ExtractionGroup}:
            raise ValueError(f"unknown group key {group!r}")
        if value not in allowed:
            raise ValueError(f"group {group!r} has unknown status {value!r}")

    mode = state.get("retrieval_mode")
    if mode not in {"full_context", "rag", "hybrid", "not_applicable"}:
        raise ValueError(f"retrieval_mode {mode!r} is not supported")

    if state.get("extraction_plan_version") and not re.fullmatch(
        r"^\d+\.\d+$", state["extraction_plan_version"]
    ):
        raise ValueError("extraction_plan_version must look like '1.0'")


def workflow_version() -> str:
    """Version of the extraction workflow contract used by this module."""
    return EXTRACTION_WORKFLOW_VERSION
