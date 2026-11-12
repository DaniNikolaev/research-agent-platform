"""Coverage Trail contract: the record itself, before any package-level rule."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.builders import (
    CARD_FIELD_PATHS,
    coverage_record,
    coverage_trail,
    search_attempt,
    valid_card,
)

from research_platform.domain.coverage.models import (
    COVERAGE_POLICY_VERSION,
    CoverageStatus,
    FieldCoverageRecord,
    PageRange,
    SearchAttempt,
    SearchAttemptOutcome,
)


def record(**overrides: Any) -> dict[str, Any]:
    payload = coverage_record("bibliography.title", "found")
    payload.update(overrides)
    return payload


def test_valid_record_is_accepted() -> None:
    model = FieldCoverageRecord.model_validate(coverage_record("bibliography.doi", "not_found"))
    assert model.coverage_status is CoverageStatus.COMPLETE
    assert model.not_found_eligible is True
    assert model.coverage_policy_version == COVERAGE_POLICY_VERSION


def test_coverage_status_vocabulary() -> None:
    assert {item.value for item in CoverageStatus} == {
        "complete",
        "partial",
        "failed",
        "not_applicable",
    }


def test_complete_requires_finished_checks() -> None:
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(
            record(coverage_status="complete", required_checks_completed=False)
        )
    assert "required_checks_completed" in str(excinfo.value)


def test_not_found_eligible_requires_complete_coverage() -> None:
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(
            record(
                coverage_status="partial",
                required_checks_completed=False,
                not_found_eligible=True,
            )
        )
    assert "not_found_eligible" in str(excinfo.value)


def test_partial_and_failed_are_never_eligible() -> None:
    for status in ("partial", "failed"):
        with pytest.raises(ValidationError):
            FieldCoverageRecord.model_validate(
                record(
                    coverage_status=status,
                    required_checks_completed=False,
                    not_found_eligible=True,
                )
            )


def test_complete_requires_an_executed_attempt() -> None:
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(record(attempts=[], search_attempt_ids=[]))
    assert "executed attempt" in str(excinfo.value)


def test_attempt_ids_must_match_recorded_attempts() -> None:
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(record(search_attempt_ids=["att_ghost"]))
    assert "search_attempt_ids" in str(excinfo.value)


def test_fallback_flag_requires_an_executed_fallback_step() -> None:
    payload = record(full_text_fallback_performed=True)
    attempts = [
        search_attempt(attempt_id="att_0001", step_id="rag_dense_lexical_1", strategy="dense")
    ]
    payload["attempts"] = attempts
    payload["search_attempt_ids"] = ["att_0001"]
    payload["retrieval_mode"] = "rag"
    payload["retrieval_strategy"] = "dense"
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(payload)
    assert "fallback" in str(excinfo.value)


def test_fallback_flag_is_meaningless_without_retrieval() -> None:
    payload = record(full_text_fallback_performed=True)
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(payload)
    assert "fallback" in str(excinfo.value)


def test_fallback_with_a_recorded_step_is_accepted() -> None:
    payload = coverage_record(
        "bibliography.doi",
        "not_found",
        retrieval_mode="hybrid",
        retrieval_strategy="lexical",
    )
    model = FieldCoverageRecord.model_validate(payload)
    assert model.full_text_fallback_performed is True
    assert any(attempt.is_fallback for attempt in model.attempts)


def test_retrieval_mode_requires_a_strategy_when_searching() -> None:
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(record(retrieval_mode="rag", retrieval_strategy=None))
    assert "retrieval_strategy" in str(excinfo.value)


def test_full_context_forbids_a_strategy() -> None:
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(
            record(retrieval_mode="full_context", retrieval_strategy="dense")
        )
    assert "retrieval_strategy" in str(excinfo.value)


def test_not_applicable_must_be_empty() -> None:
    payload = coverage_record("limitations", "not_applicable")
    payload["searched_sections"] = ["Body"]
    with pytest.raises(ValidationError) as excinfo:
        FieldCoverageRecord.model_validate(payload)
    assert "not_applicable" in str(excinfo.value)


def test_not_applicable_cannot_be_not_found_eligible() -> None:
    payload = coverage_record("limitations", "not_applicable")
    payload["not_found_eligible"] = True
    with pytest.raises(ValidationError):
        FieldCoverageRecord.model_validate(payload)


def test_page_range_is_ordered_and_positive() -> None:
    with pytest.raises(ValidationError):
        PageRange(start_page=5, end_page=2)
    with pytest.raises(ValidationError):
        PageRange(start_page=0, end_page=2)


def test_failed_attempt_requires_an_error_code() -> None:
    with pytest.raises(ValidationError) as excinfo:
        SearchAttempt.model_validate(search_attempt(outcome="failed", error_code=None))
    assert "error_code" in str(excinfo.value)


def test_non_failed_attempt_forbids_an_error_code() -> None:
    with pytest.raises(ValidationError):
        SearchAttempt.model_validate(search_attempt(outcome="succeeded", error_code="boom"))


def test_attempt_outcome_vocabulary() -> None:
    assert {item.value for item in SearchAttemptOutcome} == {
        "succeeded",
        "no_hits",
        "failed",
        "skipped",
    }


def test_coverage_policy_version_is_pinned_in_the_record() -> None:
    model = FieldCoverageRecord.model_validate(
        coverage_record("bibliography.title", "found", coverage_status="complete")
    )
    assert model.coverage_policy_version == "1.0"


def test_full_trail_covers_every_card_field() -> None:
    trail = coverage_trail(valid_card())
    assert [record["field_path"] for record in trail] == list(CARD_FIELD_PATHS)
    assert {record["coverage_status"] for record in trail} == {"complete", "not_applicable"}
