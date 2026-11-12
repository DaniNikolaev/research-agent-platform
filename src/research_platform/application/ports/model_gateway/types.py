"""Provider-neutral Model Gateway types.

Nothing in this module names a vendor.  A vendor SDK is allowed to live only inside
an adapter under ``research_platform.platform.model_gateway``; the domain and the
application layer see these types exclusively.

Two guarantees are structural rather than documentary:

* a request has no field that could carry a credential, and its free-form
  ``metadata`` is validated against secret-looking keys and values;
* a truncated response (``finish_reason == "length"``) must carry a warning, so
  silent truncation cannot happen.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    NonEmptyStr,
    ShortStr,
    UtcDatetime,
)
from research_platform.domain.runs.artifacts import ArtifactReference

__all__ = [
    "EMBEDDING_DIMENSIONS_NOTE",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "FinishReason",
    "GenerationParameters",
    "Message",
    "ModelCapabilities",
    "ModelRequest",
    "ModelResponse",
    "RerankRequest",
    "RerankResponse",
    "Role",
    "SchemaReference",
    "TokenUsage",
]

_SECRET_MARKERS: Final = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "passwd",
    "authorization",
    "bearer",
    "credential",
    "sk-",
    "token=",
)

EMBEDDING_DIMENSIONS_NOTE: Final = (
    "Embedding vectors are returned as plain lists of floats; vector storage is the "
    "storage adapter's responsibility, and embeddings never enter GraphState."
)


def _looks_like_secret(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


class Role(StrEnum):
    """Chat message role."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(ContractModel):
    """One chat message; content is untrusted text, never an instruction source."""

    role: Role
    content: NonEmptyStr


class FinishReason(StrEnum):
    """Why the provider stopped generating."""

    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"
    ERROR = "error"


class SchemaReference(ContractModel):
    """Reference to the output JSON Schema a structured call must satisfy."""

    schema_id: ShortStr
    version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")]
    json_schema: dict[str, Any] | None = None


class GenerationParameters(ContractModel):
    """Generation knobs that are part of the reproducible fingerprint."""

    temperature: Annotated[float | None, Field(default=None, ge=0.0, le=2.0)] = None
    top_p: Annotated[float | None, Field(default=None, gt=0.0, le=1.0)] = None
    max_output_tokens: Annotated[int | None, Field(default=None, ge=1, le=200_000)] = None
    stop_sequences: Annotated[list[NonEmptyStr] | None, Field(default=None, max_length=8)] = None
    seed: int | None = None


class ModelRequest(ContractModel):
    """Provider-neutral generation request.

    A request never carries a credential: authentication is the adapter's concern and
    is loaded from a secret reference by the composition root.
    """

    request_id: ShortStr
    task: ShortStr
    """Task/role name, e.g. ``extraction.field_group`` or ``agent_1b.verify``."""

    model_profile: ShortStr
    """Logical profile resolved to provider/model by the gateway configuration."""

    messages: Annotated[list[Message], Field(min_length=1, max_length=64)]
    output_schema: SchemaReference | None = None
    generation: GenerationParameters = Field(default_factory=GenerationParameters)
    timeout_seconds: Annotated[float, Field(gt=0.0, le=3600.0)] = 120.0
    metadata: Annotated[dict[ShortStr, ShortStr], Field(max_length=16)] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def _metadata_carries_no_secrets(self) -> ModelRequest:
        for key, value in self.metadata.items():
            if _looks_like_secret(key) or _looks_like_secret(value):
                raise ValueError(
                    f"ModelRequest.metadata[{key!r}] looks like a credential; secrets are "
                    "resolved by the adapter from a secret reference and never travel in "
                    "the request contract"
                )
        return self

    @model_validator(mode="after")
    def _structured_output_is_declared_when_required(self) -> ModelRequest:
        if self.output_schema is not None and self.task.endswith(".text"):
            raise ValueError(
                "a '.text' task must not declare output_schema; use a structured task name"
            )
        return self

    def redacted(self) -> dict[str, Any]:
        """JSON-safe representation that is safe to log (no message bodies)."""
        return {
            "request_id": self.request_id,
            "task": self.task,
            "model_profile": self.model_profile,
            "message_count": len(self.messages),
            "timeout_seconds": self.timeout_seconds,
            "metadata_keys": sorted(self.metadata),
        }


class TokenUsage(ContractModel):
    """Token usage and, when the provider reports it, cost."""

    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int | None, Field(default=None, ge=0)] = None
    cost_usd: Annotated[float | None, Field(default=None, ge=0.0)] = None

    @model_validator(mode="after")
    def _total_is_consistent(self) -> TokenUsage:
        if self.total_tokens is not None and self.total_tokens < max(
            self.input_tokens, self.output_tokens
        ):
            raise ValueError("total_tokens must be at least max(input_tokens, output_tokens)")
        return self

    @property
    def effective_total(self) -> int:
        """Total tokens, derived when the provider does not report one."""
        return (
            self.total_tokens
            if self.total_tokens is not None
            else (self.input_tokens + self.output_tokens)
        )


class ModelCapabilities(ContractModel):
    """What a concrete provider/model can do.

    Capabilities are inspected *before* a structured request is issued, so that a
    missing capability is reported as such instead of being discovered after the
    provider returns unparsable text.
    """

    provider_id: ShortStr
    model_id: ShortStr
    max_context_tokens: Annotated[int, Field(gt=0)]
    max_output_tokens: Annotated[int, Field(gt=0)]
    supports_structured_output: bool
    supports_json_schema: bool
    supports_streaming: bool = False
    supports_tool_calls: bool = False
    supports_embeddings: bool = False
    supports_reranking: bool = False
    json_schema_dialect: ShortStr | None = None

    @model_validator(mode="after")
    def _json_schema_implies_structured_output(self) -> ModelCapabilities:
        if self.supports_json_schema and not self.supports_structured_output:
            raise ValueError("supports_json_schema=True requires supports_structured_output=True")
        return self


class ModelResponse(ContractModel):
    """Provider-neutral generation response."""

    request_id: ShortStr
    provider_id: ShortStr
    model_id: ShortStr
    provider_request_id: ShortStr | None = None
    finish_reason: FinishReason
    structured_payload: dict[str, Any] | None = None
    raw_text: str | None = None
    raw_text_artifact: ArtifactReference | None = None
    usage: TokenUsage | None = None
    latency_ms: Annotated[float, Field(ge=0.0)]
    warnings: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=50)]

    @model_validator(mode="after")
    def _length_finish_reason_is_reported(self) -> ModelResponse:
        if self.finish_reason is FinishReason.LENGTH and not self.warnings:
            raise ValueError(
                "finish_reason 'length' means the output was truncated; it must be "
                "surfaced as a warning (silent truncation is forbidden)"
            )
        return self

    @model_validator(mode="after")
    def _carries_some_payload(self) -> ModelResponse:
        if self.structured_payload is None and not self.raw_text and self.raw_text_artifact is None:
            raise ValueError(
                "a model response must carry structured_payload, raw_text or raw_text_artifact"
            )
        return self


class EmbeddingRequest(ContractModel):
    """Provider-neutral embedding request."""

    request_id: ShortStr
    model_profile: ShortStr
    texts: Annotated[list[NonEmptyStr], Field(min_length=1, max_length=512)]
    timeout_seconds: Annotated[float, Field(gt=0.0, le=3600.0)] = 60.0


class EmbeddingResponse(ContractModel):
    """Provider-neutral embedding response."""

    request_id: ShortStr
    provider_id: ShortStr
    model_id: ShortStr
    embedding_model_id: ShortStr
    dimensions: Annotated[int, Field(gt=0)]
    vectors: Annotated[list[list[float]], Field(min_length=1)]
    usage: TokenUsage | None = None
    latency_ms: Annotated[float, Field(ge=0.0)]
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _vectors_are_rectangular(self) -> EmbeddingResponse:
        lengths = {len(vector) for vector in self.vectors}
        if lengths != {self.dimensions}:
            raise ValueError(
                "every embedding vector must have exactly 'dimensions' components; "
                f"observed lengths: {sorted(lengths)}"
            )
        return self


class RerankRequest(ContractModel):
    """Optional reranking request (not required for the first release)."""

    request_id: ShortStr
    model_profile: ShortStr
    query: NonEmptyStr
    candidates: Annotated[list[NonEmptyStr], Field(min_length=1, max_length=200)]
    top_n: Annotated[int | None, Field(default=None, ge=1, le=200)] = None


class RerankResponse(ContractModel):
    """Optional reranking response."""

    request_id: ShortStr
    provider_id: ShortStr
    model_id: ShortStr
    scores: Annotated[list[float], Field(min_length=1)]
    latency_ms: Annotated[float, Field(ge=0.0)]
