"""Run identity, run status and reproducibility metadata.

Non-negotiable rules encoded here (see ``docs/contracts/run_and_artifact_contracts.md``):

* ``run_id`` identifies one attempt; a new attempt never reuses an id and never
  overwrites a completed run;
* ``document_id`` identifies the logical source and ``source_sha256`` the exact
  bytes that were processed;
* the run status is a finite enum, so a state machine cannot invent a status;
* every error carries ``code``, ``message``, ``stage``, ``retryable`` and
  ``correlation_id`` — and never a secret or a full article text.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    CodeStr,
    ContractModel,
    Identifier,
    NonEmptyStr,
    ShortStr,
    UtcDatetime,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "RETRYABLE_STAGES",
    "SEARCH_RETRIEVAL_MODES",
    "TERMINAL_RUN_STATUSES",
    "AgentId",
    "InvalidRunTransitionError",
    "RetrievalMode",
    "RetrievalStrategy",
    "RunError",
    "RunId",
    "RunMetadata",
    "RunRecord",
    "RunStage",
    "RunStatus",
    "generate_correlation_id",
    "generate_run_id",
]

RUN_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_\-]{2,63}$"
RunId = Annotated[str, StringConstraints(pattern=RUN_ID_PATTERN)]

_SECRET_MARKERS: Final = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "passwd",
    "authorization",
    "bearer",
    "sk-",
    "token=",
)


class AgentId(StrEnum):
    """Registered agent identifiers; ``1A`` is the extraction agent."""

    AGENT_1A = "1A"


class RunStatus(StrEnum):
    """Finite run lifecycle.  No other status may be persisted."""

    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_WITH_WARNINGS,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)

ALLOWED_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_FOR_HUMAN,
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_WARNINGS,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_FOR_HUMAN: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.COMPLETED_WITH_WARNINGS: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}
"""Run lifecycle state machine.

Terminal statuses have no outgoing transition: a new attempt is a new ``run_id``, never a
resurrection of a finished run.  ``completed`` is the platform's "succeeded" state.
"""


class InvalidRunTransitionError(ValueError):
    """Raised when a status change is not allowed by :data:`ALLOWED_TRANSITIONS`."""

    def __init__(self, run_id: str, current: RunStatus, requested: RunStatus) -> None:
        allowed = sorted(item.value for item in ALLOWED_TRANSITIONS[current])
        super().__init__(
            f"run {run_id!r} cannot move from {current.value!r} to {requested.value!r}; "
            f"allowed from {current.value!r}: {allowed or 'nothing (terminal status)'}"
        )
        self.run_id = run_id
        self.current = current
        self.requested = requested


class RunStage(StrEnum):
    """Workflow stage an error or check belongs to."""

    REGISTRATION = "registration"
    PARSING = "parsing"
    QUALITY_GATE = "quality_gate"
    RETRIEVAL = "retrieval"
    EXTRACTION = "extraction"
    EVIDENCE = "evidence"
    VALIDATION = "validation"
    EXPORT = "export"
    HANDOFF = "handoff"
    SMOKE = "smoke"
    """Technical self-check of the platform skeleton; it never processes a document."""

    UNKNOWN = "unknown"


RETRYABLE_STAGES: Final[frozenset[RunStage]] = frozenset(
    {RunStage.RETRIEVAL, RunStage.EXTRACTION, RunStage.EVIDENCE, RunStage.VALIDATION}
)


class RetrievalMode(StrEnum):
    """How the document context is assembled for the model (ADD §10.2).

    ``hybrid`` means "short global document context + targeted fragments"; it is *not*
    a way to push the full text of a large article past the context budget.
    """

    FULL_CONTEXT = "full_context"
    RAG = "rag"
    HYBRID = "hybrid"
    NOT_APPLICABLE = "not_applicable"


class RetrievalStrategy(StrEnum):
    """Algorithm used to *search* inside the document.

    Deliberately separate from :class:`RetrievalMode`: the mode says what the model
    receives, the strategy says how the fragments were found.
    """

    DENSE = "dense"
    LEXICAL = "lexical"
    DENSE_LEXICAL = "dense_lexical"


SEARCH_RETRIEVAL_MODES: Final[frozenset[RetrievalMode]] = frozenset(
    {RetrievalMode.RAG, RetrievalMode.HYBRID}
)
"""Modes that require a retrieval strategy and an embedding index."""


def _looks_like_secret(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


class RunError(ContractModel):
    """Normalised run error.  Secrets and document text are never stored here."""

    code: CodeStr
    message: NonEmptyStr
    stage: RunStage
    retryable: bool
    correlation_id: ShortStr

    @model_validator(mode="after")
    def _no_secret_in_message(self) -> RunError:
        if _looks_like_secret(self.message):
            raise ValueError(
                "RunError.message looks like it contains a credential; secrets must never "
                "reach run metadata"
            )
        return self


class RunMetadata(ContractModel):
    """Reproducibility block of one Agent 1A attempt."""

    run_id: RunId
    agent_id: AgentId
    agent_version: ShortStr
    prompt_version: ShortStr
    schema_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")]
    model_provider: ShortStr
    model_id: ShortStr
    model_configuration_fingerprint: ShortStr
    parser_name: ShortStr
    parser_version: ShortStr
    started_at: UtcDatetime
    completed_at: UtcDatetime
    retrieval_mode: RetrievalMode
    retrieval_strategy: RetrievalStrategy | None = None
    embedding_model_id: ShortStr | None = None
    index_version: ShortStr | None = None

    @model_validator(mode="after")
    def _timestamps_are_ordered(self) -> RunMetadata:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self

    @model_validator(mode="after")
    def _retrieval_provenance_is_complete(self) -> RunMetadata:
        if self.retrieval_mode in SEARCH_RETRIEVAL_MODES:
            missing = [
                name
                for name, value in (
                    ("embedding_model_id", self.embedding_model_id),
                    ("index_version", self.index_version),
                    ("retrieval_strategy", self.retrieval_strategy),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"retrieval_mode={self.retrieval_mode.value!r} requires "
                    + ", ".join(missing)
                    + ": a retrieval run cannot be reproduced without the index provenance "
                    "and the search strategy"
                )
        else:
            declared = [
                name
                for name, value in (
                    ("embedding_model_id", self.embedding_model_id),
                    ("index_version", self.index_version),
                    ("retrieval_strategy", self.retrieval_strategy),
                )
                if value is not None
            ]
            if declared:
                raise ValueError(
                    f"retrieval_mode={self.retrieval_mode.value!r} must not declare "
                    + ", ".join(declared)
                    + ": no retrieval index or search strategy was used"
                )
        return self

    @model_validator(mode="after")
    def _fingerprint_has_no_secrets(self) -> RunMetadata:
        if _looks_like_secret(self.model_configuration_fingerprint):
            raise ValueError(
                "model_configuration_fingerprint must be a non-secret fingerprint "
                "(for example a hash of the non-secret parameters)"
            )
        return self


class RunRecord(ContractModel):
    """Persisted run state; owned by ``RunRepositoryPort``.

    The record is deliberately small: identifiers, lifecycle, stage, counters and
    normalised errors.  Documents, prompts, model answers and embeddings never live here —
    they belong to the Artifact Store and are referenced by logical URI.
    """

    run_id: RunId
    document_id: Identifier
    agent_id: AgentId
    agent_version: ShortStr
    contract_version: ShortStr | None = None
    status: RunStatus
    current_stage: RunStage | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    retry_count: Annotated[int, Field(ge=0, le=10_000)] = 0
    correlation_id: ShortStr | None = None
    errors: list[RunError] = Field(default_factory=list, max_length=100)
    warnings: list[NonEmptyStr] = Field(default_factory=list, max_length=200)
    metadata: Annotated[dict[ShortStr, ShortStr], Field(max_length=32)] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _timeline_is_coherent(self) -> RunRecord:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not precede created_at")
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must not precede started_at")
        return self

    @model_validator(mode="after")
    def _terminal_status_has_completion_time(self) -> RunRecord:
        if self.status in TERMINAL_RUN_STATUSES:
            if self.completed_at is None:
                raise ValueError(
                    f"status {self.status.value!r} is terminal and requires completed_at"
                )
        elif self.completed_at is not None:
            raise ValueError(
                f"status {self.status.value!r} is not terminal and must not set completed_at"
            )
        return self

    @model_validator(mode="after")
    def _failed_run_records_an_error(self) -> RunRecord:
        if self.status is RunStatus.FAILED and not self.errors:
            raise ValueError("status 'failed' requires at least one RunError")
        return self

    @model_validator(mode="after")
    def _metadata_carries_no_secrets(self) -> RunRecord:
        for key, value in self.metadata.items():
            if _looks_like_secret(key) or _looks_like_secret(value):
                raise ValueError(
                    f"RunRecord.metadata[{key!r}] looks like a credential; run metadata is "
                    "not a secret store"
                )
        return self

    @property
    def is_terminal(self) -> bool:
        """Whether the run can no longer change state."""
        return self.status in TERMINAL_RUN_STATUSES

    @property
    def finished_at(self) -> datetime | None:
        """Terminal timestamp (contract name is ``completed_at``)."""
        return self.completed_at

    def can_transition_to(self, requested: RunStatus) -> bool:
        """Whether the requested status is reachable from the current one."""
        return requested in ALLOWED_TRANSITIONS[self.status]


def generate_run_id(*, now: datetime | None = None, suffix: str | None = None) -> str:
    """Generate a new run identifier.

    Format: ``run_YYYYMMDD_HHMMSS_<6 hex>`` — sortable, obviously a run, and inside the
    ``RunId`` pattern.  A new attempt always gets a new id; ids are never reused.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    tail = suffix if suffix is not None else secrets.token_hex(3)
    if not re.fullmatch(r"[0-9a-f]{6}", tail):
        raise ValueError("run id suffix must be 6 lowercase hex characters")
    return f"run_{moment:%Y%m%d_%H%M%S}_{tail}"


def generate_correlation_id(*, suffix: str | None = None) -> str:
    """Generate a correlation id used to tie logs of one operation together."""
    tail = suffix if suffix is not None else secrets.token_hex(6)
    if not re.fullmatch(r"[0-9a-f]{12}", tail):
        raise ValueError("correlation id suffix must be 12 lowercase hex characters")
    return f"corr_{tail}"
