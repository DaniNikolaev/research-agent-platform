"""Package-level coverage rules shared by the Agent 1A package and the Agent 1B input.

The rules are the contract of the ``not_found`` decision:

* ``not_found`` is legal only with ``coverage_status = complete`` and
  ``not_found_eligible = true``;
* for retrieval modes ``rag``/``hybrid`` the mandatory full-text fallback must have been
  *executed* (an attempt is recorded), not merely declared;
* ``partial``/``failed`` coverage never means "the article lacks the information";
* every one of the twenty-seven card fields has exactly one coverage record.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from research_platform.domain.cards.models import (
    CARD_FIELD_PATHS,
    Agent1ACardV2,
    FieldStatus,
    iter_card_fields,
)
from research_platform.domain.coverage.models import (
    CoverageAvailability,
    CoverageStatus,
    FieldCoverageRecord,
)
from research_platform.domain.documents.models import DocumentManifest
from research_platform.domain.runs.models import (
    SEARCH_RETRIEVAL_MODES,
    RetrievalMode,
    RetrievalStrategy,
)

__all__ = [
    "CARD_FIELD_PATHS",
    "validate_coverage_is_not_claimed",
    "validate_coverage_trail",
]

_EXPECTED_FIELD_COUNT: Final = len(CARD_FIELD_PATHS)


def validate_coverage_trail(
    *,
    document: DocumentManifest,
    card: Agent1ACardV2,
    coverage: Sequence[FieldCoverageRecord],
    retrieval_mode: RetrievalMode,
    retrieval_strategy: RetrievalStrategy | None,
) -> None:
    """Validate the coverage trail of a package against its card and run.

    Raises:
        ValueError: the trail is incomplete, points at fields that do not exist,
            contradicts the run's retrieval mode, exceeds the document pagination, or
            lets a field claim ``not_found`` without complete coverage and (for
            retrieval runs) an executed full-text fallback.
    """
    if len(coverage) != _EXPECTED_FIELD_COUNT:
        raise ValueError(
            f"coverage_trail must contain exactly one record per card field "
            f"({_EXPECTED_FIELD_COUNT}), got {len(coverage)}"
        )

    index: dict[str, FieldCoverageRecord] = {}
    for record in coverage:
        if record.field_path in index:
            raise ValueError(f"duplicate coverage record for field {record.field_path!r}")
        index[record.field_path] = record

    unknown = sorted(set(index) - set(CARD_FIELD_PATHS))
    if unknown:
        raise ValueError("coverage_trail references unknown card fields: " + ", ".join(unknown))
    missing = sorted(set(CARD_FIELD_PATHS) - set(index))
    if missing:
        raise ValueError("coverage_trail is missing records for card fields: " + ", ".join(missing))

    paginated = document.page_count is not None
    for path, field in iter_card_fields(card):
        record = index[path]

        if record.retrieval_mode is not retrieval_mode:
            raise ValueError(
                f"coverage record for {path!r} declares retrieval_mode "
                f"{record.retrieval_mode.value!r} but the run used {retrieval_mode.value!r}"
            )
        if record.retrieval_strategy is not retrieval_strategy:
            raise ValueError(
                f"coverage record for {path!r} declares retrieval_strategy "
                f"{record.retrieval_strategy!r} but the run used {retrieval_strategy!r}"
            )

        for page_range in record.searched_page_ranges:
            if not paginated:
                raise ValueError(
                    f"coverage record for {path!r} reports searched page ranges, but the "
                    "document has no physical pagination"
                )
            if page_range.end_page > (document.page_count or 0):
                raise ValueError(
                    f"coverage record for {path!r} reports searched page "
                    f"{page_range.end_page}, beyond the document page count "
                    f"{document.page_count}"
                )

        if (
            field.status is FieldStatus.NOT_FOUND
            and record.coverage_status is not CoverageStatus.COMPLETE
        ):
            raise ValueError(
                f"field {path!r} is 'not_found' but its coverage is "
                f"{record.coverage_status.value!r}: absence is not established by an "
                "incomplete or failed search"
            )
        if field.status is FieldStatus.NOT_FOUND and not record.not_found_eligible:
            raise ValueError(f"field {path!r} is 'not_found' but not_found_eligible is false")
        if (
            field.status is FieldStatus.NOT_FOUND
            and retrieval_mode in SEARCH_RETRIEVAL_MODES
            and not record.full_text_fallback_performed
        ):
            raise ValueError(
                f"field {path!r} is 'not_found' under retrieval_mode "
                f"{retrieval_mode.value!r} without a recorded full-text fallback"
            )

        if field.status in {FieldStatus.FOUND, FieldStatus.UNCLEAR} and record.not_found_eligible:
            raise ValueError(
                f"field {path!r} has status {field.status.value!r} and must not be "
                "not_found_eligible"
            )

        if field.status is FieldStatus.NOT_APPLICABLE and (
            record.coverage_status is not CoverageStatus.NOT_APPLICABLE
        ):
            raise ValueError(
                f"field {path!r} is 'not_applicable' but its coverage is "
                f"{record.coverage_status.value!r}"
            )
        if (
            record.coverage_status is CoverageStatus.NOT_APPLICABLE
            and field.status is not FieldStatus.NOT_APPLICABLE
        ):
            raise ValueError(
                f"coverage for {path!r} is 'not_applicable' but the field status is "
                f"{field.status.value!r}"
            )


def validate_coverage_is_not_claimed(
    *,
    availability: CoverageAvailability,
    records: Sequence[FieldCoverageRecord],
    source_contract_version: str,
    recorded_contract_versions: Sequence[str],
) -> None:
    """Validate availability of a coverage trail in a received package.

    A package published under a contract version that predates the coverage trail cannot
    carry one.  Claiming coverage for it — or, conversely, shipping records under an
    older version — is a contract violation, not a judgement call.

    Raises:
        ValueError: availability and payload contradict the source contract version.
    """
    if availability is CoverageAvailability.RECORDED:
        if source_contract_version not in recorded_contract_versions:
            raise ValueError(
                f"contract version {source_contract_version!r} predates the coverage trail; "
                "coverage must be reported as 'not_recorded', never fabricated"
            )
        if len(records) != _EXPECTED_FIELD_COUNT:
            raise ValueError(
                f"a recorded coverage trail must contain {_EXPECTED_FIELD_COUNT} records, "
                f"got {len(records)}"
            )
        unknown = sorted({record.field_path for record in records} - set(CARD_FIELD_PATHS))
        if unknown:
            raise ValueError(
                "coverage records reference unknown card fields: " + ", ".join(unknown)
            )
        return

    if source_contract_version in recorded_contract_versions:
        raise ValueError(
            f"contract version {source_contract_version!r} always carries a coverage trail "
            "and must be reported as 'recorded'"
        )
    if records:
        raise ValueError(
            "coverage availability is 'not_recorded' but records were supplied: a historical "
            "package has no coverage trail and none may be invented"
        )
