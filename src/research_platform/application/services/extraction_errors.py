"""Normalised extraction errors.

Every failure keeps its category, so the workflow can decide between retry, controlled stop and
terminal failure without parsing messages.  The categories mirror the iteration brief:

``input`` / ``artifact`` / ``integrity`` / ``quality`` are terminal or blocking,
``context`` is a controlled failure,
``structured_output`` and ``model_transient`` are retryable within configured limits,
``model_permanent`` / ``storage`` / ``checkpoint`` / ``contract`` are terminal,
``unexpected`` is the fallback for anything the workflow did not anticipate.
"""

from __future__ import annotations

from enum import StrEnum

from research_platform.domain.runs.models import RunError, RunStage

__all__ = [
    "ErrorCategory",
    "ExtractionArtifactError",
    "ExtractionBlockedError",
    "ExtractionContextOverflow",
    "ExtractionError",
    "ExtractionInputError",
    "ExtractionIntegrityError",
    "ExtractionStorageError",
    "StructuredOutputError",
]


class ErrorCategory(StrEnum):
    """Allowed failure categories of the extraction workflow."""

    INPUT = "input"
    ARTIFACT = "artifact"
    INTEGRITY = "integrity"
    QUALITY = "quality"
    CONTEXT = "context"
    STRUCTURED_OUTPUT = "structured_output"
    MODEL_TRANSIENT = "model_transient"
    MODEL_PERMANENT = "model_permanent"
    STORAGE = "storage"
    CHECKPOINT = "checkpoint"
    CONTRACT = "contract"
    UNEXPECTED = "unexpected"


class ExtractionError(Exception):
    """Base class of the extraction workflow failures."""

    code: str = "extraction_error"
    category: ErrorCategory = ErrorCategory.UNEXPECTED
    stage: RunStage = RunStage.EXTRACTION
    retryable: bool = False

    def __init__(self, message: str, *, correlation_id: str = "corr_unset") -> None:
        super().__init__(message)
        self.message = message
        self.correlation_id = correlation_id

    def to_run_error(self, *, correlation_id: str | None = None) -> RunError:
        """Convert into the persisted error form."""
        return RunError.model_validate(
            {
                "code": self.code,
                "message": self.message[:500] or self.code,
                "stage": self.stage,
                "retryable": self.retryable,
                "correlation_id": correlation_id or self.correlation_id,
            }
        )


class ExtractionInputError(ExtractionError):
    """The referenced document run or its identifiers are unusable."""

    code = "extraction_input_invalid"
    category = ErrorCategory.INPUT
    stage = RunStage.REGISTRATION


class ExtractionArtifactError(ExtractionError):
    """A required document artefact is missing or unreadable."""

    code = "extraction_artifact_missing"
    category = ErrorCategory.ARTIFACT
    stage = RunStage.REGISTRATION


class ExtractionIntegrityError(ExtractionError):
    """Artefacts disagree with the manifest (hash, identifier or version)."""

    code = "extraction_integrity_error"
    category = ErrorCategory.INTEGRITY
    stage = RunStage.REGISTRATION


class ExtractionBlockedError(ExtractionError):
    """The Quality Gate does not allow extraction."""

    code = "extraction_blocked_by_quality"
    category = ErrorCategory.QUALITY
    stage = RunStage.QUALITY_GATE


class ExtractionContextOverflow(ExtractionError):
    """The prepared context does not fit the configured window."""

    code = "extraction_context_overflow"
    category = ErrorCategory.CONTEXT
    stage = RunStage.EXTRACTION


class StructuredOutputError(ExtractionError):
    """The model answer does not satisfy the target schema."""

    code = "structured_output_invalid"
    category = ErrorCategory.STRUCTURED_OUTPUT
    stage = RunStage.EXTRACTION
    retryable = True


class ExtractionStorageError(ExtractionError):
    """An artefact could not be stored."""

    code = "extraction_storage_error"
    category = ErrorCategory.STORAGE
    stage = RunStage.EXPORT
