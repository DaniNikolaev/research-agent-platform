"""Fake adapters used to test the ports.

They live in the test tree on purpose: a fake must never be importable from the shipped
package, otherwise it could be mistaken for production capability.
"""

from __future__ import annotations

from typing import Any

from research_platform.application.ports.model_gateway.errors import normalize_provider_error
from research_platform.application.ports.model_gateway.protocols import (
    require_structured_output,
)
from research_platform.application.ports.model_gateway.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    FinishReason,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    RerankRequest,
    RerankResponse,
    SchemaReference,
    TokenUsage,
)


def capabilities(
    *,
    structured: bool = True,
    json_schema: bool = True,
    embeddings: bool = False,
    reranking: bool = False,
) -> ModelCapabilities:
    """Capabilities of a fake chat model."""
    return ModelCapabilities(
        provider_id="fake_provider",
        model_id="fake_model_1",
        max_context_tokens=32_000,
        max_output_tokens=4_000,
        supports_structured_output=structured,
        supports_json_schema=json_schema,
        supports_embeddings=embeddings,
        supports_reranking=reranking,
    )


class FakeChatModel:
    """In-memory adapter implementing ``StructuredOutputModelPort``."""

    def __init__(
        self,
        *,
        structured: bool = True,
        json_schema: bool = True,
        payload: dict[str, Any] | None = None,
        text: str = "fake answer",
        raise_error: BaseException | None = None,
    ) -> None:
        self._capabilities = capabilities(structured=structured, json_schema=json_schema)
        self._payload = payload if payload is not None else {"ok": True}
        self._text = text
        self._raise_error = raise_error
        self.calls: list[ModelRequest] = []

    @property
    def capabilities(self) -> ModelCapabilities:
        """Static capabilities of the fake model."""
        return self._capabilities

    def _maybe_raise(self, request: ModelRequest) -> None:
        if self._raise_error is not None:
            raise normalize_provider_error(
                self._raise_error,
                provider_id=self._capabilities.provider_id,
                model_id=self._capabilities.model_id,
                request_id=request.request_id,
            )

    def generate_text(self, request: ModelRequest) -> ModelResponse:
        """Return a canned text response (or raise the configured error)."""
        self.calls.append(request)
        self._maybe_raise(request)
        return ModelResponse(
            request_id=request.request_id,
            provider_id=self._capabilities.provider_id,
            model_id=self._capabilities.model_id,
            finish_reason=FinishReason.STOP,
            raw_text=self._text,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            latency_ms=12.5,
        )

    def generate_structured(self, request: ModelRequest, schema: SchemaReference) -> ModelResponse:
        """Check the capability first, then return a canned structured payload."""
        require_structured_output(self.capabilities)
        self.calls.append(request)
        self._maybe_raise(request)
        return ModelResponse(
            request_id=request.request_id,
            provider_id=self._capabilities.provider_id,
            model_id=self._capabilities.model_id,
            finish_reason=FinishReason.STOP,
            structured_payload=dict(self._payload),
            usage=TokenUsage(input_tokens=100, output_tokens=40, cost_usd=0.0),
            latency_ms=42.0,
        )


class FakeEmbeddingModel:
    """In-memory adapter implementing ``EmbeddingModelPort``."""

    def __init__(self, *, dimensions: int = 4) -> None:
        self._dimensions = dimensions
        self._capabilities = capabilities(structured=False, json_schema=False, embeddings=True)
        self.calls: list[EmbeddingRequest] = []

    @property
    def capabilities(self) -> ModelCapabilities:
        """Static capabilities of the fake embedding model."""
        return self._capabilities

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return deterministic vectors of the configured size."""
        self.calls.append(request)
        return EmbeddingResponse(
            request_id=request.request_id,
            provider_id=self._capabilities.provider_id,
            model_id=self._capabilities.model_id,
            embedding_model_id="fake_embedding_model_1",
            dimensions=self._dimensions,
            vectors=[[0.1 * index] * self._dimensions for index in range(len(request.texts))],
            latency_ms=5.0,
            created_at="2026-11-11T12:00:00Z",
        )


class FakeReranker:
    """In-memory adapter implementing ``RerankerPort``."""

    def rerank(self, request: RerankRequest) -> RerankResponse:
        """Return descending scores, one per candidate."""
        return RerankResponse(
            request_id=request.request_id,
            provider_id="fake_provider",
            model_id="fake_reranker_1",
            scores=[1.0 - 0.1 * index for index in range(len(request.candidates))],
            latency_ms=3.0,
        )
