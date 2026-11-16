"""Quality Gate contract: checks, per-page results and the report.

The gate answers one question only: *how reliably was the source content extracted?* It
never judges scientific merit.  Decisions are made by named, versioned checks with their
observed values and thresholds; an aggregate score may exist as an extra number but is
never the basis of a decision.

Statuses are deliberately distinct, and their invariants are enforced here so that a
document cannot be reported as "passed" while a blocking check failed, or as "requires_ocr"
without naming the pages that need it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import Field, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    Sha256Hex,
    ShortStr,
    UtcDatetime,
)

__all__ = [
    "QUALITY_POLICY_VERSION",
    "CheckSeverity",
    "CheckStatus",
    "PageQualityRecord",
    "PageQualityStatus",
    "QualityCheckResult",
    "QualityPolicy",
    "QualityReport",
    "QualityStatus",
]

QUALITY_POLICY_VERSION: Final = "1.0"
"""Version of the check set and the default thresholds."""


class QualityStatus(StrEnum):
    """Outcome of the Quality Gate for a whole document."""

    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    REQUIRES_OCR = "requires_ocr"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    FAILED = "failed"

    @property
    def is_ready(self) -> bool:
        """Whether the document may continue to later stages."""
        return self in {QualityStatus.PASSED, QualityStatus.PASSED_WITH_WARNINGS}


class CheckStatus(StrEnum):
    """Outcome of a single check."""

    PASS = "pass"
    WARNING = "warning"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class CheckSeverity(StrEnum):
    """How much a finding matters."""

    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class PageQualityStatus(StrEnum):
    """Per-page quality verdict."""

    OK = "ok"
    LOW_TEXT = "low_text"
    IMAGE_DOMINANT = "image_dominant"
    SUSPECTED_GARBLED = "suspected_garbled"
    OCR_REQUIRED = "ocr_required"
    BLANK_OR_UNKNOWN = "blank_or_unknown"
    PARSE_FAILED = "parse_failed"

    @property
    def is_ocr_candidate(self) -> bool:
        """Whether this page needs OCR before it can be used."""
        return self in {PageQualityStatus.OCR_REQUIRED, PageQualityStatus.IMAGE_DOMINANT}

    @property
    def is_blocking(self) -> bool:
        """Whether this page blocks automatic continuation."""
        return self in {
            PageQualityStatus.OCR_REQUIRED,
            PageQualityStatus.SUSPECTED_GARBLED,
            PageQualityStatus.PARSE_FAILED,
            PageQualityStatus.BLANK_OR_UNKNOWN,
        }


class QualityCheckResult(ContractModel):
    """One named check with its observed value and (when relevant) its threshold."""

    check_id: ShortStr
    check_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = QUALITY_POLICY_VERSION
    status: CheckStatus
    severity: CheckSeverity
    message: NonEmptyStr
    observed_value: Annotated[int | float | str | None, Field(default=None)] = None
    threshold: Annotated[int | float | str | None, Field(default=None)] = None
    page_number: int | None = Field(default=None, ge=1)
    block_id: Identifier | None = None
    details: Annotated[dict[ShortStr, str], Field(default_factory=dict, max_length=16)] = Field(
        default_factory=dict
    )


class PageQualityRecord(ContractModel):
    """Per-page measurements and verdict."""

    page_number: Annotated[int, Field(ge=1)]
    page_status: PageQualityStatus
    text_char_count: Annotated[int, Field(ge=0)]
    text_block_count: Annotated[int, Field(ge=0)] = 0
    table_count: Annotated[int, Field(ge=0)] = 0
    figure_count: Annotated[int, Field(ge=0)] = 0
    image_area_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    replacement_char_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    control_char_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    has_text_layer: bool | None = None
    scan_suspected: bool = False
    checks: Annotated[list[QualityCheckResult], Field(default_factory=list, max_length=64)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _ocr_requires_missing_text(self) -> PageQualityRecord:
        if (
            self.page_status is PageQualityStatus.OCR_REQUIRED
            and self.text_char_count > 0
            and self.replacement_char_ratio < 0.5
        ):
            # A page with a healthy text layer is not an OCR page; a low-text page that kept
            # some text must be reported as low_text/partial instead.
            raise ValueError(
                f"page {self.page_number}: 'ocr_required' contradicts "
                f"{self.text_char_count} extracted characters with a readable ratio; "
                "use 'low_text' or 'suspected_garbled' instead"
            )
        return self


class QualityPolicy(ContractModel):
    """Versioned thresholds used by the gate.

    Defaults are conservative starting values, explicitly labelled as heuristics; the
    report records the values that were actually applied.
    """

    policy_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = QUALITY_POLICY_VERSION
    minimum_meaningful_text_chars: Annotated[int, Field(ge=1, le=10_000)] = 50
    low_text_chars_per_page: Annotated[int, Field(ge=1, le=10_000)] = 120
    max_replacement_char_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.02
    max_control_char_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.01
    image_dominant_area_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    page_count_mismatch_is_critical: bool = True
    require_page_provenance: bool = True
    verify_character_ranges: bool = True
    verify_table_structure: bool = True
    warn_on_empty_pages: bool = True
    detect_garbled_text: bool = True
    max_parser_warnings: Annotated[int, Field(ge=0, le=1000)] = 20


class QualityReport(ContractModel):
    """Complete, self-explaining result of the Quality Gate."""

    document_id: Identifier
    run_id: ShortStr
    source_sha256: Sha256Hex
    parsed_document_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    quality_policy_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")]
    overall_status: QualityStatus
    page_count: int | None = Field(default=None, ge=0)
    processed_pages: Annotated[int, Field(ge=0)] = 0
    warnings_count: Annotated[int, Field(ge=0)] = 0
    errors_count: Annotated[int, Field(ge=0)] = 0
    ocr_required_pages: Annotated[list[int], Field(default_factory=list, max_length=2000)] = Field(
        default_factory=list
    )
    page_results: Annotated[
        list[PageQualityRecord], Field(default_factory=list, max_length=2000)
    ] = Field(default_factory=list)
    checks: Annotated[list[QualityCheckResult], Field(default_factory=list, max_length=500)] = (
        Field(default_factory=list)
    )
    policy: QualityPolicy = Field(default_factory=QualityPolicy)
    parser_warnings: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=200)] = (
        Field(default_factory=list)
    )
    summary: NonEmptyStr
    created_at: UtcDatetime
    extra: Annotated[dict[ShortStr, Any], Field(default_factory=dict, max_length=16)] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _counts_match_the_checks(self) -> QualityReport:
        failures = sum(1 for check in self.checks if check.status is CheckStatus.FAILED)
        warnings = sum(1 for check in self.checks if check.status is CheckStatus.WARNING)
        if self.errors_count != failures:
            raise ValueError(
                f"errors_count {self.errors_count} does not match {failures} failed checks"
            )
        if self.warnings_count != warnings:
            raise ValueError(
                f"warnings_count {self.warnings_count} does not match {warnings} warnings"
            )
        return self

    @model_validator(mode="after")
    def _status_is_supported_by_the_checks(self) -> QualityReport:
        failures = [check for check in self.checks if check.status is CheckStatus.FAILED]
        critical = [check for check in failures if check.severity is CheckSeverity.CRITICAL]
        if self.overall_status is QualityStatus.PASSED:
            if failures:
                raise ValueError("status 'passed' cannot coexist with failed checks")
            if any(
                check.status is CheckStatus.WARNING and check.severity is CheckSeverity.MAJOR
                for check in self.checks
            ):
                raise ValueError(
                    "status 'passed' cannot coexist with major warnings; use 'passed_with_warnings'"
                )
        if self.overall_status is QualityStatus.PASSED_WITH_WARNINGS and failures:
            raise ValueError("status 'passed_with_warnings' cannot coexist with failed checks")
        if self.overall_status is QualityStatus.FAILED and not critical:
            raise ValueError(
                "status 'failed' requires at least one critical failed check; a document "
                "that parsed but is ambiguous belongs to 'needs_human_review'"
            )
        if self.overall_status is QualityStatus.REQUIRES_OCR and not self.ocr_required_pages:
            raise ValueError(
                "status 'requires_ocr' requires at least one page in ocr_required_pages"
            )
        if self.overall_status is not QualityStatus.REQUIRES_OCR and self.ocr_required_pages:
            raise ValueError("ocr_required_pages is only meaningful for status 'requires_ocr'")
        return self

    @model_validator(mode="after")
    def _page_results_belong_to_the_document(self) -> QualityReport:
        if self.page_count is None:
            if self.page_results:
                raise ValueError("a non-paginated document must not have page results")
            return self
        numbers = [record.page_number for record in self.page_results]
        if numbers and numbers != sorted(set(numbers)):
            raise ValueError("page results must be unique and ordered by page number")
        return self
