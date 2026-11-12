"""``Agent1AState`` — typed LangGraph state contract.

Design decisions (see ``docs/contracts/agent_1a_graph_state.md``):

* the state is a :class:`typing.TypedDict`, not a Pydantic model, because LangGraph
  merges partial node updates into it and a model would fight partial writes;
* the state holds **references**, never payloads: no PDF bytes, no full article text,
  no embedding vectors.  That keeps checkpoints small and resumes cheap;
* artefact references are stored as logical ``artifact://`` URI strings rather than
  ``ArtifactReference`` objects, because a checkpoint must round-trip through JSON
  without schema-aware deserialization.  :func:`validate_state` still enforces the
  typed URI format, so the guarantee is checked rather than assumed;
* retry counters are separate per stage class (parsing, extraction, evidence,
  validation) and bounded by configuration — a single global counter would let one
  failing stage consume the budget of another.

Checkpointing and ``interrupt`` support are *not* implemented in Iteration 0; the state
shape only leaves room for them (``processing_status="waiting_for_human"``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Final, TypedDict

from research_platform.domain.documents.models import DocumentLanguage, SourceMediaType
from research_platform.domain.runs.artifacts import parse_artifact_uri
from research_platform.domain.runs.models import (
    SEARCH_RETRIEVAL_MODES,
    RetrievalMode,
    RetrievalStrategy,
)

__all__ = [
    "ARTIFACT_REFERENCE_FIELDS",
    "FORBIDDEN_STATE_KEY_FRAGMENTS",
    "MAX_INLINE_STATE_STRING",
    "REQUIRED_STATE_KEYS",
    "RETRY_COUNTER_KEYS",
    "Agent1AState",
    "FieldGroupStatus",
    "ParseStatus",
    "ProcessingStatus",
    "RetryCounters",
    "SourceMetadata",
    "check_retry_limits",
    "initial_state",
    "new_retry_counters",
    "validate_state",
]


class ProcessingStatus(StrEnum):
    """Single-valued, serialisable status of the whole run."""

    CREATED = "created"
    REGISTERED = "registered"
    PARSING = "parsing"
    PARSED = "parsed"
    QUALITY_BLOCKED = "quality_blocked"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


class ParseStatus(StrEnum):
    """Status of the parsing stage."""

    NOT_STARTED = "not_started"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class FieldGroupStatus(StrEnum):
    """Status of one extraction field group (G1..G4)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    SKIPPED = "skipped"


RETRY_COUNTER_KEYS: Final[tuple[str, ...]] = (
    "parsing",
    "extraction",
    "evidence",
    "validation",
)

FORBIDDEN_STATE_KEY_FRAGMENTS: Final[tuple[str, ...]] = (
    "pdf",
    "bytes",
    "blob",
    "binary",
    "base64",
    "full_text",
    "document_text",
    "raw_text",
    "embeddings",
    "vectors",
)

ARTIFACT_REFERENCE_FIELDS: Final[tuple[str, ...]] = (
    "source_ref",
    "parse_artifact_ref",
    "quality_report_ref",
    "extraction_plan_ref",
    "extraction_candidates_ref",
    "evidence_ledger_ref",
    "technical_trace_ref",
    "validation_report_ref",
    "handoff_package_ref",
)

MAX_INLINE_STATE_STRING: Final = 100_000
"""Longer inline strings are rejected: a checkpoint must not carry a document."""

REQUIRED_STATE_KEYS: Final[tuple[str, ...]] = (
    "run_id",
    "correlation_id",
    "document_id",
    "agent_id",
    "agent_version",
    "source_ref",
    "source_sha256",
    "source_metadata",
    "parse_status",
    "retrieval_mode",
    "retrieval_strategy",
    "retry_counters",
    "processing_status",
)
"""Keys that must exist from the very first checkpoint of a run."""


class RetryCounters(TypedDict):
    """Per-stage retry counters.  Non-negative, bounded by configuration."""

    parsing: int
    extraction: int
    evidence: int
    validation: int


class SourceMetadata(TypedDict):
    """Compact source metadata; the original bytes stay in the Artifact Store."""

    file_name: str
    media_type: str
    language: str
    page_count: int | None


class Agent1AState(TypedDict):
    """Typed graph state of the Agent 1A workflow."""

    run_id: str
    correlation_id: str
    document_id: str
    agent_id: str
    agent_version: str

    source_ref: str | None
    source_sha256: str
    source_metadata: SourceMetadata

    parse_artifact_ref: str | None
    parse_status: str
    quality_report_ref: str | None

    retrieval_mode: str
    retrieval_strategy: str | None
    chunk_ids: list[str]
    index_version: str | None

    extraction_plan_ref: str | None
    field_group_statuses: dict[str, str]

    extraction_candidates_ref: str | None
    evidence_ledger_ref: str | None
    technical_trace_ref: str | None

    validation_report_ref: str | None
    validation_errors: list[str]

    retry_counters: RetryCounters
    warnings: list[str]
    processing_status: str
    handoff_package_ref: str | None


def new_retry_counters() -> RetryCounters:
    """Fresh counters for a new run."""
    return RetryCounters(parsing=0, extraction=0, evidence=0, validation=0)


def initial_state(
    *,
    run_id: str,
    document_id: str,
    agent_id: str = "1A",
    agent_version: str,
    source_ref: str | None,
    source_sha256: str,
    source_metadata: SourceMetadata,
    correlation_id: str = "",
) -> Agent1AState:
    """Build the JSON-serialisable initial state of a run.

    Every field is present from the start, so a checkpoint can always be compared
    against the contract without guessing which keys are "not yet" meaningful.
    """
    validate_source_metadata(source_metadata)
    return Agent1AState(
        run_id=run_id,
        correlation_id=correlation_id,
        document_id=document_id,
        agent_id=agent_id,
        agent_version=agent_version,
        source_ref=source_ref,
        source_sha256=source_sha256,
        source_metadata=source_metadata,
        parse_artifact_ref=None,
        parse_status=ParseStatus.NOT_STARTED.value,
        quality_report_ref=None,
        retrieval_mode=RetrievalMode.NOT_APPLICABLE.value,
        retrieval_strategy=None,
        chunk_ids=[],
        index_version=None,
        extraction_plan_ref=None,
        field_group_statuses={},
        extraction_candidates_ref=None,
        evidence_ledger_ref=None,
        technical_trace_ref=None,
        validation_report_ref=None,
        validation_errors=[],
        retry_counters=new_retry_counters(),
        warnings=[],
        processing_status=ProcessingStatus.CREATED.value,
        handoff_package_ref=None,
    )


def validate_source_metadata(source_metadata: Mapping[str, object]) -> None:
    """Validate the compact source metadata block.

    Raises:
        ValueError: a field is missing, a media type or language is unknown, or the
            page-count/pagination rule is violated.
    """
    required = {"file_name", "media_type", "language", "page_count"}
    missing = sorted(required - set(source_metadata))
    if missing:
        raise ValueError(f"source_metadata is missing fields: {', '.join(missing)}")
    media_type = str(source_metadata["media_type"])
    allowed_media = {item.value for item in SourceMediaType}
    if media_type not in allowed_media:
        raise ValueError(
            f"source_metadata.media_type must be one of {sorted(allowed_media)}, got {media_type!r}"
        )
    language = str(source_metadata["language"])
    allowed_languages = {item.value for item in DocumentLanguage}
    if language not in allowed_languages:
        raise ValueError(
            f"source_metadata.language must be one of {sorted(allowed_languages)}, got {language!r}"
        )
    page_count = source_metadata["page_count"]
    if page_count is not None and (not isinstance(page_count, int) or page_count < 1):
        raise ValueError("source_metadata.page_count must be a positive integer or None")
    if page_count is None and media_type != SourceMediaType.TEXT.value:
        raise ValueError("source_metadata.page_count may be null only for text/plain sources")


def _iter_values(node: object) -> Iterator[object]:
    if isinstance(node, Mapping):
        for value in node.values():
            yield from _iter_values(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _iter_values(value)
    else:
        yield node


def validate_state(state: Mapping[str, object]) -> None:
    """Validate a graph state against the contract.

    Raises:
        ValueError: a required key is missing, the state carries a forbidden bulk
            payload, an artefact reference is unparsable, a status is unknown or an
            inline string is too large.
    """
    missing = sorted(key for key in REQUIRED_STATE_KEYS if key not in state)
    if missing:
        raise ValueError("Agent1AState is missing required keys: " + ", ".join(missing))

    for key in state:
        lowered = key.lower()
        for fragment in FORBIDDEN_STATE_KEY_FRAGMENTS:
            if fragment in lowered:
                raise ValueError(
                    f"Agent1AState must not contain bulk payload key {key!r} "
                    f"(matched forbidden fragment {fragment!r}); keep it in the "
                    "Artifact Store and reference it"
                )

    for value in _iter_values(state):
        if isinstance(value, (bytes, bytearray, memoryview)):
            raise ValueError("Agent1AState must be JSON-serialisable; bytes are not allowed")
        if isinstance(value, str) and len(value) > MAX_INLINE_STATE_STRING:
            raise ValueError(
                f"Agent1AState carries an inline string of {len(value)} characters "
                f"(limit {MAX_INLINE_STATE_STRING}); store document text as an artefact"
            )

    for field_name in ARTIFACT_REFERENCE_FIELDS:
        raw = state.get(field_name)
        if raw is None:
            continue
        if not isinstance(raw, str):
            raise ValueError(f"Agent1AState.{field_name} must be a logical artefact URI string")
        parse_artifact_uri(raw)

    counters = state.get("retry_counters")
    if isinstance(counters, Mapping):
        for key in RETRY_COUNTER_KEYS:
            value = counters.get(key)
            if not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"retry_counters.{key} must be a non-negative integer, got {value!r}"
                )

    status = state.get("processing_status")
    if status is not None:
        allowed = {item.value for item in ProcessingStatus}
        if str(status) not in allowed:
            raise ValueError(f"processing_status {status!r} is not one of {sorted(allowed)}")

    _validate_retrieval_fields(state)

    try:
        json.dumps(state, ensure_ascii=False)
    except TypeError as exc:
        raise ValueError(f"Agent1AState must be JSON-serialisable: {exc}") from exc


def _validate_retrieval_fields(state: Mapping[str, object]) -> None:
    """Check the retrieval mode/strategy pair recorded in the state.

    Raises:
        ValueError: the mode or strategy is unknown, or the combination contradicts the
            contract (a search mode requires a strategy; the other modes forbid it).
    """
    mode = state.get("retrieval_mode")
    strategy = state.get("retrieval_strategy")
    allowed_modes = {item.value for item in RetrievalMode}
    if mode is not None and str(mode) not in allowed_modes:
        raise ValueError(f"retrieval_mode {mode!r} is not one of {sorted(allowed_modes)}")
    allowed_strategies = {item.value for item in RetrievalStrategy}
    if strategy is not None and str(strategy) not in allowed_strategies:
        raise ValueError(
            f"retrieval_strategy {strategy!r} is not one of {sorted(allowed_strategies)}"
        )
    if mode is None:
        return
    search_modes = {item.value for item in SEARCH_RETRIEVAL_MODES}
    if str(mode) in search_modes and strategy is None:
        raise ValueError(f"retrieval_mode {mode!r} requires retrieval_strategy")
    if str(mode) not in search_modes and strategy is not None:
        raise ValueError(f"retrieval_mode {mode!r} must not declare retrieval_strategy")


def check_retry_limits(counters: Mapping[str, int], limits: Mapping[str, int]) -> None:
    """Ensure every retry counter stays within its configured limit.

    Raises:
        ValueError: an unknown counter name is present, or a counter exceeds its limit.
    """
    unknown = sorted(set(counters) - set(RETRY_COUNTER_KEYS))
    if unknown:
        raise ValueError(f"unknown retry counter names: {', '.join(unknown)}")
    for key, value in counters.items():
        limit = limits.get(key)
        if limit is None:
            raise ValueError(f"no configured retry limit for counter {key!r}")
        if value < 0:
            raise ValueError(f"retry counter {key!r} must not be negative")
        if value > limit:
            raise ValueError(
                f"retry counter {key!r} = {value} exceeds the configured limit {limit}"
            )
