"""Evidence contract 2.1: candidate (hints only) vs record (resolver output)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.builders import (
    DOCUMENT_ID,
    evidence_candidate,
    evidence_candidate_list,
    evidence_record,
)

from research_platform.domain.evidence.models import (
    ElementType,
    EvidenceCandidate,
    EvidenceCandidateList,
    EvidenceRecord,
    MatchType,
    ResolutionStatus,
    VerificationMethod,
)


def record(**overrides: Any) -> dict[str, Any]:
    payload = evidence_record()
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ candidate


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("source_sha256", "0" * 64),
        ("char_start", 10),
        ("char_end", 20),
        ("bbox", {"x0": 1.0, "y0": 1.0, "x1": 2.0, "y1": 2.0}),
        ("match_status", "resolved"),
        ("match_type", "exact"),
        ("occurrence_index", 0),
        ("verification_method", "exact_string_match"),
        ("page", 3),
    ],
)
def test_candidate_cannot_carry_resolver_fields(name: str, value: Any) -> None:
    """The model may propose, never certify: resolver fields are structurally absent."""
    with pytest.raises(ValidationError) as excinfo:
        EvidenceCandidate.model_validate(evidence_candidate(**{name: value}))
    assert name in str(excinfo.value)


def test_candidate_requires_a_quote() -> None:
    payload = evidence_candidate()
    del payload["quote"]
    with pytest.raises(ValidationError) as excinfo:
        EvidenceCandidate.model_validate(payload)
    assert "quote" in str(excinfo.value)


def test_candidate_rejects_empty_quote() -> None:
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(evidence_candidate(quote="   "))


def test_candidate_accepts_hints_as_optional() -> None:
    item = EvidenceCandidate.model_validate(
        evidence_candidate(page_hint=None, section_hint=None, block_id_hint=None, confidence=None)
    )
    assert item.quote.startswith("Board Gender")
    assert item.block_id_hint is None


def test_candidate_rejects_oversized_context() -> None:
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(evidence_candidate(context_before="x" * 501))


def test_candidate_rejects_page_hint_zero() -> None:
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(evidence_candidate(page_hint=0))


def test_candidate_list_is_serialisable_and_document_bound() -> None:
    envelope = EvidenceCandidateList.model_validate(evidence_candidate_list())
    assert envelope.contract_version == "2.1"
    assert len(envelope.candidates) == 1
    dumped = envelope.model_dump(mode="json")
    assert EvidenceCandidateList.model_validate(dumped) == envelope


def test_candidate_list_rejects_foreign_document() -> None:
    payload = evidence_candidate_list()
    payload["candidates"][0]["document_id"] = "doc_other_02"
    with pytest.raises(ValidationError) as excinfo:
        EvidenceCandidateList.model_validate(payload)
    assert "foreign document" in str(excinfo.value)


def test_candidate_list_rejects_duplicate_ids() -> None:
    payload = evidence_candidate_list()
    payload["candidates"].append(dict(payload["candidates"][0]))
    with pytest.raises(ValidationError) as excinfo:
        EvidenceCandidateList.model_validate(payload)
    assert "duplicate candidate_id" in str(excinfo.value)


# --------------------------------------------------------------------- record


def test_valid_record_round_trip() -> None:
    item = EvidenceRecord.model_validate(evidence_record())
    dumped = item.model_dump(mode="json")
    assert dumped["match_status"] == ResolutionStatus.RESOLVED.value
    assert dumped["match_type"] == MatchType.EXACT.value
    assert dumped["occurrence_index"] == 0
    assert dumped["parser_name"] == "docling"


@pytest.mark.parametrize("page", [0, -1])
def test_physical_page_must_be_positive(page: int) -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(evidence_record(page=page))


def test_char_span_must_be_provided_together() -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(evidence_record(char_end=None))


def test_char_span_must_be_ordered() -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(evidence_record(char_start=50, char_end=50))


def test_resolved_record_requires_a_verified_span() -> None:
    with pytest.raises(ValidationError) as excinfo:
        EvidenceRecord.model_validate(
            evidence_record(char_start=None, char_end=None, occurrence_index=None)
        )
    assert "character span" in str(excinfo.value)


def test_resolved_record_requires_occurrence_index() -> None:
    with pytest.raises(ValidationError) as excinfo:
        EvidenceRecord.model_validate(evidence_record(occurrence_index=None))
    assert "occurrence_index" in str(excinfo.value)


def test_unresolved_record_must_not_claim_an_occurrence() -> None:
    with pytest.raises(ValidationError) as excinfo:
        EvidenceRecord.model_validate(
            evidence_record(
                match_status="unresolved",
                match_type="none",
                verification_method="no_match_found",
                char_start=None,
                char_end=None,
                occurrence_index=0,
            )
        )
    assert "occurrence_index" in str(excinfo.value)


def test_unresolved_record_without_position_is_valid() -> None:
    item = EvidenceRecord.model_validate(
        evidence_record(
            match_status="unresolved",
            match_type="none",
            verification_method="no_match_found",
            char_start=None,
            char_end=None,
            occurrence_index=None,
        )
    )
    assert item.match_status is ResolutionStatus.UNRESOLVED


@pytest.mark.parametrize(
    ("status", "match_type", "method"),
    [
        ("resolved", "exact", "exact_string_match"),
        ("resolved", "normalized", "normalized_string_match"),
        ("ambiguous", "exact", "exact_string_match"),
        ("ambiguous", "fuzzy", "fuzzy_string_match"),
        ("unresolved", "fuzzy", "fuzzy_string_match"),
        ("unresolved", "none", "no_match_found"),
    ],
)
def test_allowed_resolution_combinations(status: str, match_type: str, method: str) -> None:
    payload = evidence_record(
        match_status=status,
        match_type=match_type,
        verification_method=method,
    )
    if status != "resolved":
        payload["occurrence_index"] = None
    if match_type == "none":
        payload["char_start"] = None
        payload["char_end"] = None
    item = EvidenceRecord.model_validate(payload)
    assert item.match_status.value == status
    assert item.match_type.value == match_type


@pytest.mark.parametrize(
    ("status", "match_type", "method"),
    [
        ("resolved", "fuzzy", "fuzzy_string_match"),
        ("resolved", "none", "no_match_found"),
        ("unresolved", "exact", "exact_string_match"),
        ("ambiguous", "none", "no_match_found"),
    ],
)
def test_forbidden_resolution_combinations(status: str, match_type: str, method: str) -> None:
    """A fuzzy or absent match can never be reported as an exact resolution."""
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(
            evidence_record(match_status=status, match_type=match_type, verification_method=method)
        )


def test_match_type_must_match_verification_method() -> None:
    with pytest.raises(ValidationError) as excinfo:
        EvidenceRecord.model_validate(
            evidence_record(match_type="exact", verification_method="fuzzy_string_match")
        )
    assert "verification_method" in str(excinfo.value)


def test_parser_provenance_is_required() -> None:
    payload = evidence_record()
    del payload["parser_name"]
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(payload)
    payload = evidence_record()
    del payload["resolver_version"]
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(payload)


def test_bbox_requires_a_page() -> None:
    payload = evidence_record(page=None, bbox={"x0": 1.0, "y0": 1.0, "x1": 2.0, "y1": 2.0})
    with pytest.raises(ValidationError) as excinfo:
        EvidenceRecord.model_validate(payload)
    assert "requires page" in str(excinfo.value)


def test_inverted_bbox_is_rejected() -> None:
    payload = evidence_record(page=1, bbox={"x0": 5.0, "y0": 1.0, "x1": 5.0, "y1": 9.0})
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(payload)


def test_table_ref_only_for_table_cells() -> None:
    payload = evidence_record(
        element_type="paragraph",
        table_ref={"table_id": "tbl_0001", "row_index": 0, "column_index": 1, "cell_id": None},
    )
    with pytest.raises(ValidationError) as excinfo:
        EvidenceRecord.model_validate(payload)
    assert "table_cell" in str(excinfo.value)


def test_table_cell_accepts_table_ref() -> None:
    payload = evidence_record(
        element_type=ElementType.TABLE_CELL.value,
        table_ref={"table_id": "tbl_0001", "row_index": 0, "column_index": 1, "cell_id": None},
    )
    item = EvidenceRecord.model_validate(payload)
    assert item.table_ref is not None
    assert item.table_ref.column_index == 1


def test_record_rejects_path_in_document_id() -> None:
    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(evidence_record(document_id="D:\\papers\\a.pdf"))


def test_verification_method_enum_is_closed() -> None:
    assert {item.value for item in VerificationMethod} == {
        "exact_string_match",
        "normalized_string_match",
        "fuzzy_string_match",
        "no_match_found",
    }
    assert {item.value for item in ResolutionStatus} == {"resolved", "ambiguous", "unresolved"}
    assert {item.value for item in MatchType} == {"exact", "normalized", "fuzzy", "none"}


def test_candidate_and_record_share_the_document_identifier() -> None:
    candidate = EvidenceCandidate.model_validate(evidence_candidate())
    assert candidate.document_id == DOCUMENT_ID
