"""Normalised extraction errors.

Every failure keeps its category, so the workflow can decide between retry, controlled stop and
terminal failure without parsing messages, and each category maps to exactly one stable
``RunError.code`` (:data:`RUN_ERROR_CODES`).  Category and code therefore cannot drift apart.

Categories mirror the iteration brief:

``input`` / ``artifact`` / ``integrity`` are terminal or blocking,
``quality`` is a controlled stop,
``context`` is a controlled failure,
``structured_output`` and ``model_transient`` are retryable within configured limits,
``model_permanent`` / ``storage`` / ``checkpoint`` / ``contract`` are terminal,
``unexpected`` is the fallback for anything the workflow did not anticipate.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from research_platform.domain.runs.models import RunError, RunStage

__all__ = [
    "RUN_ERROR_CODES",
    "ErrorCategory",
    "ExtractionArtifactError",
    "ExtractionBlockedError",
    "ExtractionCheckpointError",
    "ExtractionContextOverflow",
    "ExtractionContractError",
    "ExtractionError",
    "ExtractionInputError",
    "ExtractionIntegrityError",
    "ExtractionStorageError",
    "ModelPermanentError",
    "ModelTransientError",
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


RUN_ERROR_CODES: Final[dict[ErrorCategory, str]] = {
    ErrorCategory.INPUT: "input_error",
    ErrorCategory.ARTIFACT: "artifact_error",
    ErrorCategory.INTEGRITY: "integrity_error",
    ErrorCategory.QUALITY: "quality_gate_failed",
    ErrorCategory.CONTEXT: "extraction_context_overflow",
    ErrorCategory.STRUCTURED_OUTPUT: "structured_output_invalid",
    ErrorCategory.MODEL_TRANSIENT: "model_transient_error",
    ErrorCategory.MODEL_PERMANENT: "model_permanent_error",
    ErrorCategory.STORAGE: "extraction_storage_error",
    ErrorCategory.CHECKPOINT: "checkpoint_error",
    ErrorCategory.CONTRACT: "contract_error",
    ErrorCategory.UNEXPECTED: "unexpected_error",
}
"""One authoritative category → ``RunError.code`` mapping.  Never build codes ad hoc."""


class ExtractionError(Exception):
    """Base class of the extraction workflow failures."""

    category: ErrorCategory = ErrorCategory.UNEXPECTED
    stage: RunStage = RunStage.EXTRACTION
    retryable: bool = False

    manifest_published: bool | None = None
    """Whether the terminal Run Manifest reached the Artifact Store (``None`` = not attempted)."""

    manifest_publication_note: str | None = None
    """Why the manifest was not published, when :attr:`manifest_published` is ``False``."""

    def __init__(self, message: str, *, correlation_id: str = "corr_unset") -> None:
        super().__init__(message)
        self.message = message
        self.correlation_id = correlation_id

    @property
    def code(self) -> str:
        """Stable ``RunError.code`` of this failure's category."""
        return RUN_ERROR_CODES[self.category]

    def to_run_error(self, *, correlation_id: str | None = None) -> RunError:
        """Convert into the persisted error form (code derived from the category)."""
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

    category = ErrorCategory.INPUT
    stage = RunStage.REGISTRATION


class ExtractionArtifactError(ExtractionError):
    """A required document artefact is missing or unreadable."""

    category = ErrorCategory.ARTIFACT
    stage = RunStage.REGISTRATION


class ExtractionIntegrityError(ExtractionError):
    """Artefacts disagree with the manifest (hash, identifier or version)."""

    category = ErrorCategory.INTEGRITY
    stage = RunStage.REGISTRATION


class ExtractionBlockedError(ExtractionError):
    """The Quality Gate does not allow extraction."""

    category = ErrorCategory.QUALITY
    stage = RunStage.QUALITY_GATE


class ExtractionContextOverflow(ExtractionError):
    """The prepared context does not fit the configured window."""

    category = ErrorCategory.CONTEXT
    stage = RunStage.EXTRACTION


class StructuredOutputError(ExtractionError):
    """The model answer does not satisfy the target schema."""

    category = ErrorCategory.STRUCTURED_OUTPUT
    stage = RunStage.EXTRACTION
    retryable = True


class ModelTransientError(ExtractionError):
    """A retryable model/gateway failure (timeout, rate limit, transient provider error)."""

    category = ErrorCategory.MODEL_TRANSIENT
    stage = RunStage.EXTRACTION
    retryable = True


class ModelPermanentError(ExtractionError):
    """A non-retryable model/gateway failure (auth, invalid request, permanent outage)."""

    category = ErrorCategory.MODEL_PERMANENT
    stage = RunStage.EXTRACTION


class ExtractionStorageError(ExtractionError):
    """An artefact could not be published or read."""

    category = ErrorCategory.STORAGE
    stage = RunStage.EXPORT


class ExtractionCheckpointError(ExtractionError):
    """The checkpoint store could not be initialised, read or written."""

    category = ErrorCategory.CHECKPOINT
    stage = RunStage.EXTRACTION


class ExtractionContractError(ExtractionError):
    """A versioned contract is incompatible with this implementation."""

    category = ErrorCategory.CONTRACT
    stage = RunStage.REGISTRATION
