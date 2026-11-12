"""Technical validation and the semantic-verification placeholder.

Two different things must never be merged into one boolean:

*technical validation* answers "is this package well-formed, source-grounded and
self-consistent?" and is performed deterministically by Agent 1A;
*semantic verification* answers "does the quote really support the claim?" and is
performed independently by Agent 1B and/or a human.

Agent 1A therefore cannot emit a "verified" semantic status: the enum simply has no
such member.  A receiver that wants a semantic verdict must look at the Agent 1B
verification report, never at this package.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from research_platform.domain.base import (
    CodeStr,
    ContractModel,
    NonEmptyStr,
    ShortStr,
    UtcDatetime,
)

__all__ = [
    "CheckOutcome",
    "SemanticValidation",
    "SemanticVerificationStatus",
    "TechnicalStatus",
    "TechnicalValidation",
    "ValidationCheck",
    "ValidationIssue",
    "ValidationReport",
]


class TechnicalStatus(StrEnum):
    """Handoff-level technical status of an Agent 1A package (ADD §13.5)."""

    VALID = "valid"
    VALID_WITH_WARNINGS = "valid_with_warnings"
    BLOCKED = "blocked"
    FAILED = "failed"


class CheckOutcome(StrEnum):
    """Outcome of a single technical check."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class SemanticVerificationStatus(StrEnum):
    """Semantic status that Agent 1A is allowed to emit.

    Intentionally closed: there is no ``verified`` member, so no code path can
    declare an Agent 1A package semantically verified.
    """

    NOT_PERFORMED = "not_performed"
    PENDING_AGENT_1B = "pending_agent_1b"


class ValidationIssue(ContractModel):
    """A single technical error or warning."""

    code: CodeStr
    message: NonEmptyStr
    field_path: Annotated[str | None, Field(default=None, max_length=256)] = None
    evidence_id: ShortStr | None = None


class ValidationCheck(ContractModel):
    """A named technical check and its outcome."""

    check_id: CodeStr
    description: NonEmptyStr
    outcome: CheckOutcome
    detail: NonEmptyStr | None = None


class TechnicalValidation(ContractModel):
    """Deterministic technical validation results."""

    schema_valid: bool
    validator_name: ShortStr
    validator_version: ShortStr
    performed_at: UtcDatetime
    checks: Annotated[list[ValidationCheck], Field(default_factory=list, max_length=100)]
    errors: Annotated[list[ValidationIssue], Field(default_factory=list, max_length=500)]
    warnings: Annotated[list[ValidationIssue], Field(default_factory=list, max_length=500)]


class SemanticValidation(ContractModel):
    """What Agent 1A is allowed to say about semantic support: nothing yet."""

    status: SemanticVerificationStatus = SemanticVerificationStatus.PENDING_AGENT_1B
    independent_verification_required: Literal[True] = True
    notes: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=50)]


_HANDOFF_CAPABLE: Final = frozenset({TechnicalStatus.VALID, TechnicalStatus.VALID_WITH_WARNINGS})


class ValidationReport(ContractModel):
    """Full validation section of an Agent 1A package."""

    technical_status: TechnicalStatus
    technical: TechnicalValidation
    semantic: SemanticValidation = Field(default_factory=SemanticValidation)

    @model_validator(mode="after")
    def _status_agrees_with_details(self) -> ValidationReport:
        errors = self.technical.errors
        warnings = self.technical.warnings
        status = self.technical_status

        if status in _HANDOFF_CAPABLE and errors:
            raise ValueError(
                f"technical_status {status.value!r} cannot coexist with technical errors; "
                "a package with errors is 'blocked' or 'failed'"
            )
        if status is TechnicalStatus.VALID and warnings:
            raise ValueError(
                "technical_status 'valid' cannot coexist with warnings; use 'valid_with_warnings'"
            )
        if status is TechnicalStatus.VALID_WITH_WARNINGS and not warnings:
            raise ValueError("technical_status 'valid_with_warnings' requires at least one warning")
        if status in {TechnicalStatus.BLOCKED, TechnicalStatus.FAILED} and not errors:
            raise ValueError(
                f"technical_status {status.value!r} requires at least one technical error"
            )
        if not self.technical.schema_valid and status in _HANDOFF_CAPABLE:
            raise ValueError(
                f"a package that failed schema validation cannot be marked {status.value!r}"
            )
        return self

    @property
    def is_handoff_capable(self) -> bool:
        """Whether the package may be handed to Agent 1B automatically."""
        return self.technical_status in _HANDOFF_CAPABLE
