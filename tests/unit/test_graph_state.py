"""GraphState contract: serialisable, compact, bounded retries."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.fixtures.builders import DOCUMENT_ID, ORIGINAL_URI, RUN_ID, SOURCE_SHA256

from research_platform.agents.agent_1a.contracts.graph_state import (
    ARTIFACT_REFERENCE_FIELDS,
    MAX_INLINE_STATE_STRING,
    REQUIRED_STATE_KEYS,
    RETRY_COUNTER_KEYS,
    Agent1AState,
    FieldGroupStatus,
    ParseStatus,
    ProcessingStatus,
    check_retry_limits,
    initial_state,
    new_retry_counters,
    validate_state,
)
from research_platform.config import WorkflowSettings


def build_state(**overrides: Any) -> Agent1AState:
    state = initial_state(
        run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        agent_version="0.1.0",
        source_ref=ORIGINAL_URI,
        source_sha256=SOURCE_SHA256,
        source_metadata={
            "file_name": "en_article.pdf",
            "media_type": "application/pdf",
            "language": "en",
            "page_count": 7,
        },
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def test_initial_state_is_serialisable_and_valid() -> None:
    state = build_state()
    validate_state(state)
    text = json.dumps(state, ensure_ascii=False)
    assert json.loads(text)["run_id"] == RUN_ID


def test_initial_state_contains_every_contract_key() -> None:
    state = build_state()
    assert set(Agent1AState.__annotations__) <= set(state)
    for key in REQUIRED_STATE_KEYS:
        assert key in state


def test_initial_state_has_no_bulk_payload() -> None:
    state = build_state()
    assert "pdf_bytes" not in state
    assert "full_text" not in state
    assert "embeddings" not in state
    assert state["parse_status"] == ParseStatus.NOT_STARTED.value
    assert state["processing_status"] == ProcessingStatus.CREATED.value


def test_every_processing_status_is_serialisable() -> None:
    for status in ProcessingStatus:
        state = build_state(processing_status=status.value)
        validate_state(state)
        json.dumps(state)


@pytest.mark.parametrize("key", ["pdf_bytes", "document_bytes", "full_text", "embeddings"])
def test_forbidden_bulk_keys_are_rejected(key: str) -> None:
    state = build_state()
    state[key] = b"%PDF-1.7" if key.endswith("bytes") else "x"  # type: ignore[typeddict-unknown-key]
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "bulk payload" in str(excinfo.value)


def test_bytes_anywhere_are_rejected() -> None:
    state = build_state()
    state["warnings"] = [b"binary"]  # type: ignore[list-item]
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "bytes" in str(excinfo.value)


def test_oversized_inline_string_is_rejected() -> None:
    state = build_state()
    state["warnings"] = ["x" * (MAX_INLINE_STATE_STRING + 1)]
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "inline string" in str(excinfo.value)


def test_missing_required_key_is_rejected() -> None:
    state = build_state()
    del state["run_id"]
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "run_id" in str(excinfo.value)


@pytest.mark.parametrize("field_name", ARTIFACT_REFERENCE_FIELDS)
def test_artifact_reference_fields_are_typed(field_name: str) -> None:
    state = build_state()
    state[field_name] = "D:\\MultiAgentPipeline\\artifacts\\x.json"  # type: ignore[literal-required]
    with pytest.raises(ValueError):
        validate_state(state)


def test_valid_artifact_reference_field_is_accepted() -> None:
    ledger_uri = f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/evidence_ledger/art_ledger"
    state = build_state(evidence_ledger_ref=ledger_uri)
    validate_state(state)
    assert state["evidence_ledger_ref"] == ledger_uri


def test_unknown_processing_status_is_rejected() -> None:
    state = build_state(processing_status="almost_done")
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "processing_status" in str(excinfo.value)


def test_retry_counters_are_separate_per_stage() -> None:
    counters = new_retry_counters()
    assert tuple(counters) == RETRY_COUNTER_KEYS
    assert set(counters) == {"parsing", "extraction", "evidence", "validation"}
    assert all(value == 0 for value in counters.values())


def test_retry_counters_must_be_non_negative() -> None:
    state = build_state()
    state["retry_counters"] = {**new_retry_counters(), "extraction": -1}
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "non-negative" in str(excinfo.value)


def test_retry_counters_are_bounded_by_configuration() -> None:
    limits = WorkflowSettings().retry_limits
    assert set(limits) == set(RETRY_COUNTER_KEYS)
    check_retry_limits(new_retry_counters(), limits)
    with pytest.raises(ValueError) as excinfo:
        check_retry_limits(
            {"parsing": 0, "extraction": limits["extraction"] + 1, "evidence": 0, "validation": 0},
            limits,
        )
    assert "exceeds the configured limit" in str(excinfo.value)


def test_unknown_retry_counter_is_rejected() -> None:
    with pytest.raises(ValueError):
        check_retry_limits({"parsing": 0, "retrieval": 1}, WorkflowSettings().retry_limits)


def test_field_group_status_values() -> None:
    assert {status.value for status in FieldGroupStatus} >= {
        "pending",
        "in_progress",
        "completed",
        "failed",
    }


def test_initial_state_records_no_retrieval_strategy() -> None:
    state = build_state()
    assert state["retrieval_mode"] == "not_applicable"
    assert state["retrieval_strategy"] is None


@pytest.mark.parametrize("mode", ["rag", "hybrid"])
def test_search_mode_requires_a_strategy_in_the_state(mode: str) -> None:
    state = build_state(retrieval_mode=mode)
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "retrieval_strategy" in str(excinfo.value)


def test_search_mode_with_a_strategy_is_valid() -> None:
    state = build_state(retrieval_mode="hybrid", retrieval_strategy="dense_lexical")
    validate_state(state)
    assert state["retrieval_strategy"] == "dense_lexical"


def test_full_context_must_not_declare_a_strategy() -> None:
    state = build_state(retrieval_mode="full_context", retrieval_strategy="dense")
    with pytest.raises(ValueError) as excinfo:
        validate_state(state)
    assert "must not declare retrieval_strategy" in str(excinfo.value)


@pytest.mark.parametrize(
    ("mode", "strategy"),
    [("semantic_search", None), ("rag", "rrf"), ("full_context", "lexical")],
)
def test_unknown_retrieval_values_are_rejected(mode: str, strategy: str | None) -> None:
    state = build_state(retrieval_mode=mode, retrieval_strategy=strategy)
    with pytest.raises(ValueError):
        validate_state(state)
