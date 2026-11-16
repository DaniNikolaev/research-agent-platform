"""Document-processing workflow state.

Compact by construction: identifiers, logical artefact URIs and counters only.  The PDF,
the canonical text and the parsed document never enter the state — they are stored as
artefacts and referenced.
"""

from __future__ import annotations

import re
from typing import Final, TypedDict

__all__ = ["MUTABLE_STATUS_KEYS", "DocumentProcessingState", "initial_document_state"]

MUTABLE_STATUS_KEYS: Final = (
    "processing_status",
    "quality_status",
    "stop_reason",
)

_ARTIFACT_FIELDS: Final = (
    "source_artifact_uri",
    "manifest_artifact_uri",
    "parsed_document_uri",
    "canonical_text_uri",
    "quality_report_uri",
    "diagnostics_uri",
)

_ARTIFACT_URI_RE: Final = re.compile(r"^artifact://(?:documents/runs|documents|runs|platform)")


class DocumentProcessingState(TypedDict, total=False):
    """State of one document-processing run."""

    run_id: str
    correlation_id: str
    document_id: str

    input_path: str
    file_name: str
    source_type: str
    source_sha256: str
    source_size_bytes: int
    page_count: int | None

    source_artifact_uri: str | None
    manifest_artifact_uri: str | None
    parsed_document_uri: str | None
    canonical_text_uri: str | None
    quality_report_uri: str | None
    diagnostics_uri: str | None

    language: str
    parser_name: str
    parser_version: str
    used_fallback: bool

    processing_status: str
    quality_status: str | None
    stop_reason: str | None

    ocr_required_pages: list[int]
    warnings: list[str]
    retry_counters: dict[str, int]
    error_code: str | None


def initial_document_state(
    *,
    input_path: str,
    correlation_id: str,
    file_name: str = "",
) -> DocumentProcessingState:
    """Fresh state with every key present, so a checkpoint is always comparable."""
    return DocumentProcessingState(
        run_id="",
        correlation_id=correlation_id,
        document_id="",
        input_path=input_path,
        file_name=file_name,
        source_type="",
        source_sha256="",
        source_size_bytes=0,
        page_count=None,
        source_artifact_uri=None,
        manifest_artifact_uri=None,
        parsed_document_uri=None,
        canonical_text_uri=None,
        quality_report_uri=None,
        diagnostics_uri=None,
        language="unknown",
        parser_name="",
        parser_version="",
        used_fallback=False,
        processing_status="created",
        quality_status=None,
        stop_reason=None,
        ocr_required_pages=[],
        warnings=[],
        retry_counters={"ingestion": 0, "parsing": 0, "quality_gate": 0},
        error_code=None,
    )
