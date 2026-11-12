"""Coverage Trail contracts: evidence that the search for a field actually happened."""

from __future__ import annotations

from research_platform.domain.coverage.models import (
    COVERAGE_POLICY_VERSION,
    CoverageAvailability,
    CoverageStatus,
    FieldCoverageRecord,
    PageRange,
    SearchAttempt,
    SearchAttemptOutcome,
)
from research_platform.domain.coverage.rules import (
    validate_coverage_is_not_claimed,
    validate_coverage_trail,
)

__all__ = [
    "COVERAGE_POLICY_VERSION",
    "CoverageAvailability",
    "CoverageStatus",
    "FieldCoverageRecord",
    "PageRange",
    "SearchAttempt",
    "SearchAttemptOutcome",
    "validate_coverage_is_not_claimed",
    "validate_coverage_trail",
]
