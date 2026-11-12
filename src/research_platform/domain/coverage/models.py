"""Field Coverage Record: a machine-checkable account of the search for one field.

Why this exists: an empty retrieval result is not proof that the article lacks the
information, so ``not_found`` must never be assigned merely because a search returned
nothing.  The coverage record makes the *search that was performed* auditable:

* which sections and physical page ranges were in scope;
* which blocks were retrieved and handed to the model;
* which attempts ran, with what outcome;
* whether the mandatory fallback for a failed/partial first pass was actually executed;
* whether every required step of the approved search plan completed without a critical
  error.

``coverage_status = complete`` means "the mandatory steps of the approved plan ran
without critical errors".  It is **not** a mathematical proof that the article does not
contain the information, and it must never be presented as one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import CodeStr, ContractModel, NonEmptyStr, ShortStr
from research_platform.domain.evidence.models import FIELD_PATH_PATTERN
from research_platform.domain.runs.models import (
    SEARCH_RETRIEVAL_MODES,
    RetrievalMode,
    RetrievalStrategy,
)

__all__ = [
    "COVERAGE_POLICY_VERSION",
    "CoverageAvailability",
    "CoverageStatus",
    "FieldCoverageRecord",
    "PageRange",
    "SearchAttempt",
    "SearchAttemptOutcome",
]

COVERAGE_POLICY_VERSION: Final = "1.0"
"""Version of the coverage policy the record was produced under."""

FALLBACK_STEP_PREFIX: Final = "fallback_"
"""A fallback step id must start with this prefix to be recognised as a fallback."""

FieldPath = Annotated[str, StringConstraints(pattern=FIELD_PATH_PATTERN, max_length=128)]


class CoverageStatus(StrEnum):
    """How much of the approved search plan for one field actually ran."""

    COMPLETE = "complete"
    """Every mandatory step of the plan finished without a critical error."""

    PARTIAL = "partial"
    """Some mandatory step did not finish; the search must not be read as exhaustive."""

    FAILED = "failed"
    """The search itself failed; no conclusion about absence is allowed."""

    NOT_APPLICABLE = "not_applicable"
    """The field is not applicable to this document, so no search was required."""


class CoverageAvailability(StrEnum):
    """Whether a package carries a coverage trail at all (version negotiation)."""

    RECORDED = "recorded"
    NOT_RECORDED = "not_recorded"


class SearchAttemptOutcome(StrEnum):
    """Outcome of one search step."""

    SUCCEEDED = "succeeded"
    NO_HITS = "no_hits"
    FAILED = "failed"
    SKIPPED = "skipped"


class PageRange(ContractModel):
    """Inclusive range of physical pages (1-based)."""

    start_page: Annotated[int, Field(ge=1)]
    end_page: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _range_is_ordered(self) -> PageRange:
        if self.end_page < self.start_page:
            raise ValueError("end_page must not be smaller than start_page")
        return self


class SearchAttempt(ContractModel):
    """One executed (or explicitly skipped) search step."""

    attempt_id: ShortStr
    step_id: ShortStr
    retrieval_strategy: RetrievalStrategy | None = None
    searched_sections: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=64)] = (
        Field(default_factory=list)
    )
    searched_page_ranges: Annotated[
        list[PageRange], Field(default_factory=list, max_length=200)
    ] = Field(default_factory=list)
    retrieved_block_ids: Annotated[list[ShortStr], Field(default_factory=list, max_length=500)] = (
        Field(default_factory=list)
    )
    outcome: SearchAttemptOutcome
    error_code: CodeStr | None = None

    @model_validator(mode="after")
    def _failed_attempt_has_an_error_code(self) -> SearchAttempt:
        if self.outcome is SearchAttemptOutcome.FAILED and self.error_code is None:
            raise ValueError("outcome 'failed' requires error_code")
        if self.outcome is not SearchAttemptOutcome.FAILED and self.error_code is not None:
            raise ValueError(f"outcome {self.outcome.value!r} must not carry error_code")
        return self

    @property
    def is_fallback(self) -> bool:
        """Whether this attempt is a full-text fallback step."""
        return self.step_id.startswith(FALLBACK_STEP_PREFIX)


class FieldCoverageRecord(ContractModel):
    """Coverage of the search performed for one card field."""

    field_path: FieldPath
    retrieval_mode: RetrievalMode
    retrieval_strategy: RetrievalStrategy | None = None
    coverage_status: CoverageStatus
    search_plan_id: ShortStr
    searched_sections: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=64)] = (
        Field(default_factory=list)
    )
    searched_page_ranges: Annotated[
        list[PageRange], Field(default_factory=list, max_length=200)
    ] = Field(default_factory=list)
    retrieved_block_ids: Annotated[list[ShortStr], Field(default_factory=list, max_length=500)] = (
        Field(default_factory=list)
    )
    search_attempt_ids: Annotated[list[ShortStr], Field(default_factory=list, max_length=64)] = (
        Field(default_factory=list)
    )
    full_text_fallback_performed: bool = False
    required_checks_completed: bool
    not_found_eligible: bool
    coverage_policy_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")] = (
        COVERAGE_POLICY_VERSION
    )
    attempts: Annotated[list[SearchAttempt], Field(default_factory=list, max_length=64)] = Field(
        default_factory=list
    )
    notes: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=20)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _strategy_matches_mode(self) -> FieldCoverageRecord:
        if self.retrieval_mode in SEARCH_RETRIEVAL_MODES:
            if self.retrieval_strategy is None:
                raise ValueError(
                    f"retrieval_mode {self.retrieval_mode.value!r} requires retrieval_strategy"
                )
        elif self.retrieval_strategy is not None:
            raise ValueError(
                f"retrieval_mode {self.retrieval_mode.value!r} must not declare "
                "retrieval_strategy: no search was performed"
            )
        return self

    @model_validator(mode="after")
    def _attempt_ids_reference_real_attempts(self) -> FieldCoverageRecord:
        attempt_ids = [attempt.attempt_id for attempt in self.attempts]
        duplicates = sorted({item for item in attempt_ids if attempt_ids.count(item) > 1})
        if duplicates:
            raise ValueError("duplicate attempt_id values: " + ", ".join(duplicates))
        if list(self.search_attempt_ids) != attempt_ids:
            raise ValueError(
                "search_attempt_ids must list exactly the recorded attempts in order: the "
                "trail must not cite attempts that are not evidenced in this record"
            )
        return self

    @model_validator(mode="after")
    def _status_rules(self) -> FieldCoverageRecord:
        status = self.coverage_status
        if status is CoverageStatus.COMPLETE and not self.required_checks_completed:
            raise ValueError(
                "coverage_status 'complete' requires required_checks_completed: a plan with "
                "unfinished mandatory steps is at most 'partial'"
            )
        if not self.required_checks_completed and status is CoverageStatus.COMPLETE:
            raise ValueError("required_checks_completed=False cannot be 'complete'")
        if self.not_found_eligible and not (
            status is CoverageStatus.COMPLETE and self.required_checks_completed
        ):
            raise ValueError(
                "not_found_eligible requires coverage_status 'complete' with "
                "required_checks_completed=True"
            )
        if status in {CoverageStatus.PARTIAL, CoverageStatus.FAILED} and self.not_found_eligible:
            raise ValueError(f"coverage_status {status.value!r} must not be not_found_eligible")
        return self

    @model_validator(mode="after")
    def _not_applicable_is_empty(self) -> FieldCoverageRecord:
        if self.coverage_status is CoverageStatus.NOT_APPLICABLE:
            if (
                self.searched_sections
                or self.searched_page_ranges
                or self.retrieved_block_ids
                or self.attempts
            ):
                raise ValueError(
                    "coverage_status 'not_applicable' must not report searched content or "
                    "attempts: the search did not happen"
                )
            if self.full_text_fallback_performed:
                raise ValueError(
                    "coverage_status 'not_applicable' must not report a full-text fallback"
                )
            if self.not_found_eligible:
                raise ValueError("coverage_status 'not_applicable' must not be not_found_eligible")
        return self

    @model_validator(mode="after")
    def _complete_search_reports_scope(self) -> FieldCoverageRecord:
        if self.coverage_status is CoverageStatus.COMPLETE:
            if not self.attempts:
                raise ValueError(
                    "a 'complete' coverage record must evidence at least one executed attempt; "
                    "completeness cannot be claimed without a search"
                )
            if self.retrieval_mode in SEARCH_RETRIEVAL_MODES and not (
                self.searched_sections or self.searched_page_ranges
            ):
                raise ValueError(
                    "a 'complete' retrieval coverage record must report the searched sections "
                    "or page ranges"
                )
        return self

    @model_validator(mode="after")
    def _fallback_is_evidenced(self) -> FieldCoverageRecord:
        if self.full_text_fallback_performed:
            if self.retrieval_mode not in SEARCH_RETRIEVAL_MODES:
                raise ValueError(
                    f"full_text_fallback_performed is meaningless for retrieval_mode "
                    f"{self.retrieval_mode.value!r}"
                )
            executed = [
                attempt
                for attempt in self.attempts
                if attempt.is_fallback
                and attempt.outcome
                in {SearchAttemptOutcome.SUCCEEDED, SearchAttemptOutcome.NO_HITS}
            ]
            if not executed:
                raise ValueError(
                    "full_text_fallback_performed=True requires a recorded fallback attempt "
                    f"(step_id starting with {FALLBACK_STEP_PREFIX!r}) that actually executed; "
                    "a flag alone is not evidence"
                )
        return self
