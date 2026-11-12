"""Frozen snapshot of the run contract as published in Agent 1A package contract **2.0**.

This module is a historical record, not a working surface.  It exists so that a package
produced under contract version ``2.0`` keeps validating byte-for-byte against the
published schema ``contracts/agent_1a/v2/extraction_package.schema.json`` after the
platform moved on to ``2.1`` (which adds ``hybrid`` to :class:`RetrievalMode` and the
``retrieval_strategy`` field).

Do not edit anything here to satisfy a new requirement: add a new version instead.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    ShortStr,
    UtcDatetime,
)
from research_platform.domain.runs.models import AgentId, RunId

__all__ = ["RetrievalMode", "RunMetadata"]

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


class RetrievalMode(StrEnum):
    """How the document was made available to the model for one run."""

    FULL_CONTEXT = "full_context"
    RAG = "rag"
    NOT_APPLICABLE = "not_applicable"


def _looks_like_secret(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


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
    embedding_model_id: ShortStr | None = None
    index_version: ShortStr | None = None

    @model_validator(mode="after")
    def _timestamps_are_ordered(self) -> RunMetadata:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self

    @model_validator(mode="after")
    def _retrieval_provenance_is_complete(self) -> RunMetadata:
        if self.retrieval_mode is RetrievalMode.RAG:
            missing = [
                name
                for name, value in (
                    ("embedding_model_id", self.embedding_model_id),
                    ("index_version", self.index_version),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "retrieval_mode='rag' requires " + ", ".join(missing) + ": an index "
                    "cannot be reproduced without the embedding model and index version"
                )
        elif self.embedding_model_id is not None or self.index_version is not None:
            raise ValueError(
                f"retrieval_mode={self.retrieval_mode.value!r} must not declare "
                "embedding_model_id/index_version: no retrieval index was used"
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
