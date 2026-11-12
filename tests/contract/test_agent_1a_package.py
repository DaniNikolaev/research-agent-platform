"""Contract tests for Agent 1A packages: current 2.1 and frozen 2.0."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.builders import (
    ORIGINAL_URI,
    RUN_ID,
    SOURCE_SHA256,
    artifact,
    coverage_trail,
    evidence_record,
    valid_card,
    valid_package,
    valid_package_rag,
    valid_package_v20,
)

from research_platform.agents.agent_1a.contracts.package import (
    Agent1AExtractionPackageV2,
)
from research_platform.agents.agent_1a.contracts.package_v21 import (
    SUPPORTED_CONTRACT_VERSIONS,
    Agent1AExtractionPackageV21,
)
from research_platform.domain.validation.models import (
    SemanticValidation,
    SemanticVerificationStatus,
)


def package(**overrides: Any) -> dict[str, Any]:
    payload = valid_package()
    payload.update(overrides)
    return payload


# --------------------------------------------------------------- 2.1 happy path


def test_valid_package_is_accepted() -> None:
    model = Agent1AExtractionPackageV21.model_validate(valid_package())
    assert model.contract_version == "2.1"
    assert len(model.evidence_ledger) == 3
    assert len(model.coverage_trail) == 27
    assert model.validation.semantic.status is SemanticVerificationStatus.PENDING_AGENT_1B


def test_package_round_trips_through_json() -> None:
    model = Agent1AExtractionPackageV21.model_validate(valid_package())
    dumped = model.model_dump(mode="json")
    again = Agent1AExtractionPackageV21.model_validate(dumped)
    assert again == model


def test_supported_contract_versions_are_declared() -> None:
    assert SUPPORTED_CONTRACT_VERSIONS == ("2.0", "2.1")


# ------------------------------------------------------------- structural rules


def test_unknown_root_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(package(extra_section={}))
    assert "extra_section" in str(excinfo.value)


@pytest.mark.parametrize("section", ["artifacts", "coverage_trail", "validation", "run"])
def test_missing_required_section_is_rejected(section: str) -> None:
    payload = valid_package()
    del payload[section]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert section in str(excinfo.value)


def test_unsupported_contract_version_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Agent1AExtractionPackageV21.model_validate(package(contract_version="2.2"))


def test_run_schema_version_must_equal_contract_version() -> None:
    payload = valid_package()
    payload["run"]["schema_version"] = "2.0"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "contract_version" in str(excinfo.value)


# ------------------------------------------------------------------- card links


def test_found_field_without_evidence_reference_is_rejected() -> None:
    payload = valid_package()
    payload["card"]["bibliography"]["title"]["evidence_refs"] = []
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "at least one evidence" in str(excinfo.value)


def test_reference_to_absent_evidence_id_is_rejected() -> None:
    payload = valid_package()
    payload["card"]["bibliography"]["title"]["evidence_refs"] = [{"evidence_id": "ev_999999"}]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "unknown evidence_id" in str(excinfo.value)


def test_duplicate_evidence_id_is_rejected() -> None:
    payload = valid_package()
    payload["evidence_ledger"].append(evidence_record(evidence_id="ev_000001"))
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "duplicate evidence_id" in str(excinfo.value)


def test_denormalized_quote_must_match_the_ledger() -> None:
    payload = valid_package()
    payload["card"]["bibliography"]["title"]["evidence_refs"] = [
        {"evidence_id": "ev_000001", "quote": "a paraphrased title"}
    ]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "source of truth" in str(excinfo.value)


def test_denormalized_quote_copying_the_ledger_is_accepted() -> None:
    payload = valid_package()
    payload["card"]["bibliography"]["title"]["evidence_refs"] = [
        {
            "evidence_id": "ev_000001",
            "quote": "Board Gender Diversity and Firm Financial Performance",
            "page": 1,
            "section_path": ["Title"],
        }
    ]
    Agent1AExtractionPackageV21.model_validate(payload)


# -------------------------------------------------------------------- evidence


@pytest.mark.parametrize("page", [0, -3])
def test_non_positive_page_is_rejected(page: int) -> None:
    payload = valid_package()
    payload["evidence_ledger"][0]["page"] = page
    with pytest.raises(ValidationError):
        Agent1AExtractionPackageV21.model_validate(payload)


def test_page_beyond_document_is_rejected() -> None:
    payload = valid_package()
    payload["evidence_ledger"][0]["page"] = 99
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "beyond the document page count" in str(excinfo.value)


def test_paginated_document_requires_a_page() -> None:
    payload = valid_package()
    payload["evidence_ledger"][0]["page"] = None
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "paginated" in str(excinfo.value)


def test_unpaginated_text_document_forbids_a_page() -> None:
    payload = valid_package()
    payload["document"]["source_media_type"] = "text/plain"
    payload["document"]["source_file_name"] = "article.txt"
    payload["document"]["page_count"] = None
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "no physical pagination" in str(excinfo.value)


def test_unpaginated_text_document_without_pages_is_accepted() -> None:
    payload = valid_package()
    payload["document"]["source_media_type"] = "text/plain"
    payload["document"]["source_file_name"] = "article.txt"
    payload["document"]["page_count"] = None
    for record in payload["evidence_ledger"]:
        record["page"] = None
        record["bbox"] = None
    for record in payload["coverage_trail"]:
        record["searched_page_ranges"] = []
        if record["coverage_status"] != "not_applicable":
            record["searched_sections"] = ["Whole document"]
            for attempt in record["attempts"]:
                attempt["searched_page_ranges"] = []
    Agent1AExtractionPackageV21.model_validate(payload)


def test_naive_datetime_is_rejected() -> None:
    payload = valid_package()
    payload["run"]["started_at"] = "2026-11-11T12:00:00"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "timezone-aware" in str(excinfo.value)


@pytest.mark.parametrize("bad_hash", ["", "abc123", "z" * 64, SOURCE_SHA256[:10]])
def test_invalid_source_hash_is_rejected(bad_hash: str) -> None:
    payload = valid_package()
    payload["document"]["source_sha256"] = bad_hash
    with pytest.raises(ValidationError):
        Agent1AExtractionPackageV21.model_validate(payload)


def test_evidence_carried_over_from_another_version_is_rejected() -> None:
    payload = valid_package()
    payload["evidence_ledger"][0]["source_sha256"] = "a" * 64
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "source version" in str(excinfo.value)


def test_evidence_from_another_document_is_rejected() -> None:
    payload = valid_package()
    payload["evidence_ledger"][0]["document_id"] = "doc_other_02"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "belongs to document" in str(excinfo.value)


def test_evidence_parser_provenance_must_match_the_run() -> None:
    payload = valid_package()
    payload["evidence_ledger"][0]["parser_name"] = "pymupdf"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "parser" in str(excinfo.value)


# ------------------------------------------------------------------- artefacts


def test_absolute_windows_original_uri_is_rejected() -> None:
    payload = valid_package()
    payload["document"]["original_artifact_uri"] = "D:\\papers\\en_article.pdf"
    with pytest.raises(ValidationError):
        Agent1AExtractionPackageV21.model_validate(payload)


def test_artifact_reference_for_another_run_is_rejected() -> None:
    payload = valid_package()
    payload["artifacts"][1]["uri"] = (
        "artifact://documents/doc_en_article_01/runs/run_20260101_000000_zz99/"
        "parsed_document/art_parsed"
    )
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "belongs to run" in str(excinfo.value)


def test_missing_handoff_artifact_type_is_rejected() -> None:
    payload = valid_package()
    payload["artifacts"] = [
        item for item in payload["artifacts"] if item["artifact_type"] != "parsed_document"
    ]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "missing artifact types" in str(excinfo.value)


def test_original_artifact_must_be_listed() -> None:
    payload = valid_package()
    payload["artifacts"][0] = artifact(
        "original_document",
        "art_original_alt",
        ORIGINAL_URI.replace("art_original", "art_original_alt"),
        media_type="application/pdf",
        sha256=SOURCE_SHA256,
        size_bytes=2048,
    )
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "original_artifact_uri" in str(excinfo.value)


# ------------------------------------------------------------------ validation


def test_semantic_verification_cannot_be_verified() -> None:
    assert {status.value for status in SemanticVerificationStatus} == {
        "not_performed",
        "pending_agent_1b",
    }
    with pytest.raises(ValidationError):
        SemanticValidation(status="verified")  # type: ignore[arg-type]
    payload = valid_package()
    payload["validation"]["semantic"]["status"] = "semantically_verified"
    with pytest.raises(ValidationError):
        Agent1AExtractionPackageV21.model_validate(payload)


def test_semantic_review_must_stay_required() -> None:
    with pytest.raises(ValidationError):
        SemanticValidation(independent_verification_required=False)  # type: ignore[arg-type]


def test_technical_status_cannot_be_valid_with_errors() -> None:
    payload = valid_package()
    payload["validation"]["technical"]["errors"] = [
        {"code": "quote_not_found", "message": "quote missing", "field_path": "bibliography.title"}
    ]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "technical_status" in str(excinfo.value)


def test_agent_1a_package_has_no_scientific_review_status() -> None:
    """The scientific verdict belongs to Agent 1B; the extractor has no field for it."""
    schema = Agent1AExtractionPackageV21.model_json_schema()
    assert "scientific_review_status" not in str(schema)
    payload = valid_package()
    payload["scientific_review_status"] = "verified"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "scientific_review_status" in str(excinfo.value)


def test_validation_of_a_package_does_not_mutate_the_input() -> None:
    payload = valid_package()
    original = copy.deepcopy(payload)
    Agent1AExtractionPackageV21.model_validate(payload)
    assert payload == original


def test_package_uses_the_original_artifact_uri() -> None:
    model = Agent1AExtractionPackageV21.model_validate(valid_package())
    assert model.document.original_artifact_uri == ORIGINAL_URI
    assert model.run.run_id == RUN_ID
    assert any(artifact.uri == ORIGINAL_URI for artifact in model.artifacts)


# --------------------------------------------------------------- coverage trail


def test_coverage_trail_must_have_one_record_per_field() -> None:
    payload = valid_package()
    payload["coverage_trail"] = payload["coverage_trail"][:-1]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "27" in str(excinfo.value)


def test_duplicate_coverage_record_is_rejected() -> None:
    payload = valid_package()
    payload["coverage_trail"][1]["field_path"] = payload["coverage_trail"][0]["field_path"]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "coverage" in str(excinfo.value).lower()


def test_coverage_record_for_unknown_field_is_rejected() -> None:
    payload = valid_package()
    payload["coverage_trail"][0]["field_path"] = "bibliography.issn"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "unknown card fields" in str(excinfo.value)


def test_not_found_requires_complete_coverage() -> None:
    payload = valid_package()
    for record in payload["coverage_trail"]:
        if record["field_path"] == "bibliography.doi":
            record["coverage_status"] = "partial"
            record["required_checks_completed"] = False
            record["not_found_eligible"] = False
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "absence is not established" in str(excinfo.value)


def test_not_found_requires_not_found_eligibility() -> None:
    payload = valid_package()
    for record in payload["coverage_trail"]:
        if record["field_path"] == "bibliography.doi":
            record["not_found_eligible"] = False
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "not_found_eligible" in str(excinfo.value)


def test_found_field_must_not_be_not_found_eligible() -> None:
    payload = valid_package()
    for record in payload["coverage_trail"]:
        if record["field_path"] == "bibliography.title":
            record["not_found_eligible"] = True
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "not_found_eligible" in str(excinfo.value)


def test_rag_mode_not_found_requires_an_executed_fallback() -> None:
    payload = valid_package_rag()
    for record in payload["coverage_trail"]:
        if record["field_path"] == "bibliography.doi":
            record["full_text_fallback_performed"] = False
            record["attempts"] = [
                attempt
                for attempt in record["attempts"]
                if not attempt["step_id"].startswith("fallback_")
            ]
            record["search_attempt_ids"] = [attempt["attempt_id"] for attempt in record["attempts"]]
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "full-text fallback" in str(excinfo.value)


def test_coverage_mode_must_match_the_run() -> None:
    payload = valid_package()
    for record in payload["coverage_trail"]:
        record["retrieval_mode"] = "rag"
        record["retrieval_strategy"] = "dense"
        for attempt in record["attempts"]:
            attempt["retrieval_strategy"] = "dense"
        if record["field_path"] == "bibliography.doi":
            break
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "retrieval_mode" in str(excinfo.value)


def test_not_applicable_field_requires_not_applicable_coverage() -> None:
    payload = valid_package()
    for record in payload["coverage_trail"]:
        if record["field_path"] == "limitations":
            blank = copy.deepcopy(payload["coverage_trail"][0])
            blank["field_path"] = "limitations"
            blank["coverage_status"] = "complete"
            blank["not_found_eligible"] = False
            payload["coverage_trail"][payload["coverage_trail"].index(record)] = blank
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "not_applicable" in str(excinfo.value)


def test_coverage_may_be_built_for_any_card() -> None:
    card = valid_card()
    trail = coverage_trail(card)
    assert len(trail) == 27
    assert {record["coverage_status"] for record in trail} == {"complete", "not_applicable"}


# ------------------------------------------------------------ frozen 2.0 history


def test_frozen_2_0_package_still_validates() -> None:
    model = Agent1AExtractionPackageV2.model_validate(valid_package_v20())
    assert model.contract_version == "2.0"
    assert model.run.retrieval_mode.value == "full_context"


def test_frozen_2_0_package_cannot_grow_a_coverage_trail() -> None:
    """History is closed: a 2.0 package may not silently acquire 2.1 sections."""
    payload = valid_package_v20()
    payload["coverage_trail"] = coverage_trail(valid_card())
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV2.model_validate(payload)
    assert "coverage_trail" in str(excinfo.value)


def test_frozen_2_0_package_cannot_use_hybrid_mode() -> None:
    payload = valid_package_v20()
    payload["run"]["retrieval_mode"] = "hybrid"
    with pytest.raises(ValidationError):
        Agent1AExtractionPackageV2.model_validate(payload)


def test_frozen_2_0_package_cannot_declare_a_retrieval_strategy() -> None:
    payload = valid_package_v20()
    payload["run"]["retrieval_strategy"] = "dense"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV2.model_validate(payload)
    assert "retrieval_strategy" in str(excinfo.value)


def test_2_1_package_requires_coverage_trail() -> None:
    payload = valid_package_v20()
    payload["contract_version"] = "2.1"
    payload["run"]["schema_version"] = "2.1"
    with pytest.raises(ValidationError) as excinfo:
        Agent1AExtractionPackageV21.model_validate(payload)
    assert "coverage_trail" in str(excinfo.value)
