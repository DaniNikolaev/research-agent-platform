"""Normalised errors of the document pipeline.

Every error carries a stable ``code`` (the same vocabulary the run record and the CLI
report), the stage it belongs to and a human message.  The hierarchy is deliberately small:
the platform's existing ``RunError`` is the storage form, and these exceptions are the
in-process form of the same facts.

Retry policy lives with the error: only transient technical failures are retryable, and
that decision is data, not an ``except`` clause scattered across the workflow.
"""

from __future__ import annotations

from enum import StrEnum

from research_platform.domain.runs.models import RunError, RunStage

__all__ = [
    "DocumentIngestionError",
    "DocumentIntegrityError",
    "DocumentParsingError",
    "DocumentPipelineError",
    "DocumentQualityError",
    "OcrRequiredError",
    "ParserTimeoutError",
    "ParserUnavailableError",
    "ProvenanceError",
    "UnsupportedEncodingError",
]


class DocumentPipelineError(Exception):
    """Base class of document-pipeline failures."""

    code: str = "document_pipeline_error"
    stage: RunStage = RunStage.UNKNOWN
    retryable: bool = False

    def __init__(self, message: str, *, correlation_id: str = "corr_unset") -> None:
        super().__init__(message)
        self.message = message
        self.correlation_id = correlation_id

    def to_run_error(
        self, *, stage: RunStage | None = None, correlation_id: str | None = None
    ) -> RunError:
        """Convert into the persisted error form."""
        return RunError(
            code=self.code,
            message=self.message[:500] or self.code,
            stage=stage or self.stage,
            retryable=self.retryable,
            correlation_id=correlation_id or self.correlation_id,
        )


class DocumentIngestionError(DocumentPipelineError):
    """The source cannot be accepted."""

    stage = RunStage.REGISTRATION

    def __init__(self, code: str, message: str, *, correlation_id: str = "corr_unset") -> None:
        self.code = code
        super().__init__(message, correlation_id=correlation_id)


class DocumentIntegrityError(DocumentPipelineError):
    """Stored bytes do not match the bytes that were read."""

    code: str = "source_integrity_error"
    stage = RunStage.REGISTRATION


class UnsupportedEncodingError(DocumentPipelineError):
    """The text source cannot be decoded reliably."""

    code: str = "text_encoding_error"
    stage = RunStage.PARSING


class DocumentParsingError(DocumentPipelineError):
    """The parser failed technically."""

    code: str = "document_parsing_failed"
    stage = RunStage.PARSING


class ParserUnavailableError(DocumentParsingError):
    """The parsing dependency is not installed or cannot start."""

    code: str = "parser_unavailable"


class ParserTimeoutError(DocumentParsingError):
    """The parser exceeded its time budget."""

    code: str = "parser_timeout"
    retryable = True


class ProvenanceError(DocumentParsingError):
    """Extracted content cannot be attributed to a physical page or range."""

    code: str = "provenance_error"


class DocumentQualityError(DocumentPipelineError):
    """The Quality Gate rejected the document."""

    code: str = "document_quality_failed"
    stage = RunStage.QUALITY_GATE


class OcrRequiredError(DocumentPipelineError):
    """A page has no usable text layer and OCR is not available."""

    code: str = "ocr_required"
    stage = RunStage.QUALITY_GATE


class DocumentStage(StrEnum):
    """Human-readable stages used by the CLI report."""

    INGESTION = "ingestion"
    PARSING = "parsing"
    QUALITY_GATE = "quality_gate"
