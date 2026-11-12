"""Run, artifact and identifier contracts."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.builders import (
    DOCUMENT_ID,
    ORIGINAL_URI,
    RUN_ID,
    SOURCE_SHA256,
    artifact,
    valid_run,
)

from research_platform.domain.runs.artifacts import (
    ARTIFACT_URI_TEMPLATE,
    ArtifactReference,
    ArtifactType,
    build_artifact_uri,
    parse_artifact_uri,
)
from research_platform.domain.runs.models import (
    SEARCH_RETRIEVAL_MODES,
    AgentId,
    RetrievalMode,
    RetrievalStrategy,
    RunError,
    RunMetadata,
    RunRecord,
    RunStage,
    RunStatus,
)


def run_metadata(**overrides: Any) -> dict[str, Any]:
    payload = valid_run()
    payload.update(overrides)
    return payload


def run_record(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": RUN_ID,
        "document_id": DOCUMENT_ID,
        "agent_id": "1A",
        "agent_version": "0.1.0",
        "status": "running",
        "created_at": "2026-11-11T12:00:00Z",
        "started_at": "2026-11-11T12:00:05Z",
        "completed_at": None,
        "errors": [],
        "warnings": [],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- run


def test_run_metadata_is_accepted() -> None:
    metadata = RunMetadata.model_validate(run_metadata())
    assert metadata.agent_id is AgentId.AGENT_1A
    assert metadata.retrieval_mode is RetrievalMode.FULL_CONTEXT


def test_run_status_enum_is_closed() -> None:
    assert {status.value for status in RunStatus} == {
        "created",
        "running",
        "waiting_for_human",
        "completed",
        "completed_with_warnings",
        "failed",
        "cancelled",
    }
    with pytest.raises(ValidationError):
        RunRecord.model_validate(run_record(status="partially_done"))


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(run_metadata(started_at="2026-11-11T12:00:00"))
    assert "timezone-aware" in str(excinfo.value)


def test_non_utc_offset_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(run_metadata(started_at="2026-11-11T15:00:00+03:00"))
    assert "UTC" in str(excinfo.value)


def test_completed_at_must_follow_started_at() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(
            run_metadata(started_at="2026-11-11T12:05:00Z", completed_at="2026-11-11T12:00:00Z")
        )
    assert "completed_at" in str(excinfo.value)


def test_rag_mode_requires_embedding_provenance() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(run_metadata(retrieval_mode="rag"))
    assert "embedding_model_id" in str(excinfo.value)


def test_rag_mode_requires_a_retrieval_strategy() -> None:
    payload = run_metadata(
        retrieval_mode="rag", embedding_model_id="bge-m3", index_version="idx_0001"
    )
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(payload)
    assert "retrieval_strategy" in str(excinfo.value)


def test_full_context_mode_forbids_embedding_provenance() -> None:
    with pytest.raises(ValidationError):
        RunMetadata.model_validate(
            run_metadata(embedding_model_id="bge-m3", index_version="idx_0001")
        )


def test_full_context_mode_forbids_a_retrieval_strategy() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(run_metadata(retrieval_strategy="dense"))
    assert "retrieval_strategy" in str(excinfo.value)


def test_rag_mode_with_provenance_is_accepted() -> None:
    metadata = RunMetadata.model_validate(
        run_metadata(
            retrieval_mode="rag",
            retrieval_strategy="dense_lexical",
            embedding_model_id="bge-m3",
            index_version="idx_0001",
        )
    )
    assert metadata.index_version == "idx_0001"
    assert metadata.retrieval_strategy is RetrievalStrategy.DENSE_LEXICAL


def test_hybrid_mode_is_a_first_class_retrieval_mode() -> None:
    metadata = RunMetadata.model_validate(
        run_metadata(
            retrieval_mode="hybrid",
            retrieval_strategy="lexical",
            embedding_model_id="bge-m3",
            index_version="idx_0001",
        )
    )
    assert metadata.retrieval_mode is RetrievalMode.HYBRID


def test_retrieval_mode_and_strategy_vocabularies() -> None:
    assert {item.value for item in RetrievalMode} == {
        "full_context",
        "rag",
        "hybrid",
        "not_applicable",
    }
    assert {item.value for item in RetrievalStrategy} == {"dense", "lexical", "dense_lexical"}
    assert {RetrievalMode.RAG, RetrievalMode.HYBRID} == SEARCH_RETRIEVAL_MODES


def test_unknown_retrieval_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunMetadata.model_validate(run_metadata(retrieval_mode="semantic_search"))


def test_fingerprint_must_not_contain_a_secret() -> None:
    for value in ("sk-live-abcdef", "api_key=1234", "Bearer token here"):
        with pytest.raises(ValidationError) as excinfo:
            RunMetadata.model_validate(run_metadata(model_configuration_fingerprint=value))
        assert "fingerprint" in str(excinfo.value)


def test_run_error_rejects_secret_in_message() -> None:
    with pytest.raises(ValidationError):
        RunError(
            code="auth_failed",
            message="provider returned api_key=abc",
            stage=RunStage.EXTRACTION,
            retryable=False,
            correlation_id="corr_0001",
        )


def test_terminal_status_requires_completion_time() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RunRecord.model_validate(run_record(status="completed", completed_at=None))
    assert "terminal" in str(excinfo.value)


def test_non_terminal_status_forbids_completion_time() -> None:
    with pytest.raises(ValidationError):
        RunRecord.model_validate(run_record(status="running", completed_at="2026-11-11T12:01:00Z"))


def test_failed_run_requires_an_error_record() -> None:
    with pytest.raises(ValidationError) as excinfo:
        RunRecord.model_validate(
            run_record(status="failed", completed_at="2026-11-11T12:09:00Z", errors=[])
        )
    assert "RunError" in str(excinfo.value)


def test_failed_run_with_error_is_accepted() -> None:
    record = RunRecord.model_validate(
        run_record(
            status="failed",
            completed_at="2026-11-11T12:09:00Z",
            errors=[
                {
                    "code": "parse_failed",
                    "message": "page 3 could not be extracted",
                    "stage": "parsing",
                    "retryable": True,
                    "correlation_id": "corr_0001",
                }
            ],
        )
    )
    assert record.is_terminal


# ----------------------------------------------------------------------- artifact


def test_build_and_parse_artifact_uri_round_trip() -> None:
    uri = build_artifact_uri(
        document_id=DOCUMENT_ID,
        run_id=RUN_ID,
        artifact_type=ArtifactType.EVIDENCE_LEDGER,
        artifact_id="art_ledger",
    )
    parts = parse_artifact_uri(uri)
    assert parts.document_id == DOCUMENT_ID
    assert parts.run_id == RUN_ID
    assert parts.artifact_type is ArtifactType.EVIDENCE_LEDGER
    assert parts.artifact_id == "art_ledger"
    for placeholder in ("{document_id}", "{run_id}", "{artifact_type}", "{artifact_id}"):
        assert placeholder in ARTIFACT_URI_TEMPLATE
    assert uri.startswith("artifact://documents/")


@pytest.mark.parametrize(
    "uri",
    [
        "D:\\MultiAgentPipeline\\storage\\evidence.json",
        "\\\\server\\share\\evidence.json",
        "artifact://documents/../runs/x/evidence_ledger/art_ledger",
        "artifact://documents/doc_en_article_01/runs/run_x/evidence_ledger/../art",
        "https://example.com/evidence.json",
        "artifact://documents/doc_en_article_01/runs/run_x/not_a_type/art_ledger",
        "artifact://documents/doc_en_article_01/runs/run_x/evidence_ledger",
    ],
)
def test_non_portable_artifact_uri_is_rejected(uri: str) -> None:
    with pytest.raises(ValueError):
        parse_artifact_uri(uri)


def test_artifact_reference_must_match_its_uri() -> None:
    payload = artifact("original_document", "art_original", ORIGINAL_URI)
    payload["artifact_id"] = "art_other"
    with pytest.raises(ValidationError) as excinfo:
        ArtifactReference.model_validate(payload)
    assert "artifact_id" in str(excinfo.value)


def test_artifact_type_must_match_its_uri() -> None:
    payload = artifact("original_document", "art_original", ORIGINAL_URI)
    payload["artifact_type"] = "parsed_document"
    with pytest.raises(ValidationError) as excinfo:
        ArtifactReference.model_validate(payload)
    assert "artifact_type" in str(excinfo.value)


def test_artifact_media_type_must_be_a_mime_type() -> None:
    payload = artifact("original_document", "art_original", ORIGINAL_URI)
    payload["media_type"] = "pdf"
    with pytest.raises(ValidationError):
        ArtifactReference.model_validate(payload)


def test_artifact_checksum_must_be_sha256() -> None:
    payload = artifact("original_document", "art_original", ORIGINAL_URI)
    payload["sha256"] = SOURCE_SHA256[:8]
    with pytest.raises(ValidationError):
        ArtifactReference.model_validate(payload)


def test_artifact_reference_is_immutable() -> None:
    reference = ArtifactReference.model_validate(
        artifact("original_document", "art_original", ORIGINAL_URI)
    )
    with pytest.raises(ValidationError):
        reference.version = "2"  # type: ignore[misc]


def test_artifact_payload_copy_is_not_mutated_by_validation() -> None:
    payload = artifact("original_document", "art_original", ORIGINAL_URI)
    original = copy.deepcopy(payload)
    ArtifactReference.model_validate(payload)
    assert payload == original
