"""Provider-neutral embedding capability.

The generated model backend is not the embedding backend: embeddings live behind their own
port, so a real provider can be added later without touching the retrieval services, the
workflow or the domain.  The only implementation shipped in iteration 4 is a deterministic
fake adapter used for tests; it is explicitly marked as a simulation and must not be read as a
semantic-quality embedding.
"""

from __future__ import annotations

from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field, model_validator

from research_platform.domain.base import (
    ContractModel,
    NonEmptyStr,
    ShortStr,
    Sha256Hex,
)

__all__ = [
    "EmbeddingCapabilities",
    "EmbeddingExecutionMode",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "EmbeddingPort",
    "EmbeddingError",
]


class EmbeddingError(RuntimeError):
    """Base class of the embedding failures."""

    code: str = "embedding_error"


class EmbeddingDimensionError(EmbeddingError):
    """Vectors disagree with the configured dimension."""

    code = "embedding_dimension_mismatch"


class EmbeddingContractError(EmbeddingError):
    """The adapter returned a structurally invalid response."""

    code = "embedding_contract_violation"


class EmbeddingExecutionMode:
    """Execution mode constants (kept as plain strings for the manifest)."""

    REAL = "real"
    SIMULATION = "simulation"
    NOT_CONFIGURED = "not_configured"


class EmbeddingCapabilities(ContractModel):
    """What one embedding profile can do."""

    profile_id: ShortStr
    provider_id: ShortStr
    model_id: ShortStr
    dimension: Annotated[int, Field(gt=0, le=8192)]
    normalized: bool = False
    is_fake: bool = False
    execution_mode: Annotated[str, Field(pattern=r"^(real|simulation|not_configured)$")] = (
        "not_configured"
    )
    max_batch_size: Annotated[int, Field(gt=0, le=1024)] = 64


class EmbeddingRequest(ContractModel):
    """One batch of texts to embed (chunks or a query)."""

    request_id: ShortStr
    profile_id: ShortStr
    texts: Annotated[list[NonEmptyStr], Field(min_length=1, max_length=1024)]
    chunk_ids: Annotated[list[ShortStr], Field(default_factory=list, max_length=1024)] = Field(
        default_factory=list
    )
    document_id: ShortStr | None = None
    source_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def _ids_match_texts(self) -> EmbeddingRequest:
        if self.chunk_ids and len(self.chunk_ids) != len(self.texts):
            raise ValueError("chunk_ids must match texts one-to-one")
        return self


class EmbeddingResponse(ContractModel):
    """Vectors plus the metadata needed to validate and reproduce the index."""

    request_id: ShortStr
    provider_id: ShortStr
    model_id: ShortStr
    model_revision: ShortStr | None = None
    dimension: Annotated[int, Field(gt=0, le=8192)]
    normalized: bool = False
    execution_mode: Annotated[str, Field(pattern=r"^(real|simulation|not_configured)$")]
    vectors: Annotated[list[list[float]], Field(min_length=1, max_length=1024)]
    chunk_ids: Annotated[list[ShortStr], Field(default_factory=list, max_length=1024)] = Field(
        default_factory=list
    )
    latency_ms: Annotated[float | None, Field(default=None, ge=0.0)] = None
    usage_texts: Annotated[int | None, Field(default=None, ge=0)] = None

    @model_validator(mode="after")
    def _shape_is_consistent(self) -> EmbeddingResponse:
        if len(self.vectors) != len(self.vectors[0]) and False:  # pragma: no cover
            raise ValueError("unreachable")
        for vector in self.vectors:
            if len(vector) != self.dimension:
                raise ValueError("every vector must match the declared dimension")
            if not vector:
                raise ValueError("empty vector")
            for value in vector:
                if value != value or value in {float("inf"), float("-inf")}:
                    raise ValueError("vectors must be finite (no NaN/Infinity)")
        return self


@runtime_checkable
class EmbeddingPort(Protocol):
    """Embed texts for retrieval."""

    def capabilities(self, profile_id: str) -> EmbeddingCapabilities:
        """Capabilities of one embedding profile."""
        ...

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed one batch of texts."""
        ...
