"""Provider-neutral Model Gateway protocols.

The workflow (application/agent layer) owns retries, timeouts, budget checks and
fallback decisions.  The protocols below describe only *what* an adapter can do,
never *how many times* it will be asked to do it — the timeout value travels in the
request, and a retry is a new request with a new ``request_id``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_platform.application.ports.model_gateway.errors import (
    ModelUnsupportedCapabilityError,
)
from research_platform.application.ports.model_gateway.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    RerankRequest,
    RerankResponse,
    SchemaReference,
)

__all__ = [
    "ChatModelPort",
    "EmbeddingModelPort",
    "ModelGatewayPort",
    "RerankerPort",
    "StructuredOutputModelPort",
    "require_structured_output",
]


def require_structured_output(capabilities: ModelCapabilities) -> None:
    """Fail fast, before any extraction call, when structured output is unavailable.

    Raises:
        ModelUnsupportedCapabilityError: the model cannot satisfy a typed output
            contract. Discovering this *after* the call would mean post-processing a
            free-text guess, which the architecture forbids.
    """
    if not capabilities.supports_structured_output:
        raise ModelUnsupportedCapabilityError(
            f"model {capabilities.provider_id}/{capabilities.model_id} does not support "
            "structured output; a typed extraction call cannot be issued",
            provider_id=capabilities.provider_id,
            model_id=capabilities.model_id,
        )


@runtime_checkable
class ChatModelPort(Protocol):
    """Free-text and structured-capable chat access to one model profile."""

    @property
    def capabilities(self) -> ModelCapabilities:
        """Static capabilities of the model behind this port."""
        ...

    def generate_text(self, request: ModelRequest) -> ModelResponse:
        """Generate free text.  Used only where free text is genuinely required."""
        ...


@runtime_checkable
class StructuredOutputModelPort(ChatModelPort, Protocol):
    """Chat access that can honour a JSON Schema output contract."""

    def generate_structured(self, request: ModelRequest, schema: SchemaReference) -> ModelResponse:
        """Generate a payload that validates against ``schema``.

        Implementations must call :func:`require_structured_output` first and must not
        attempt a silent free-text fallback.
        """
        ...


@runtime_checkable
class EmbeddingModelPort(Protocol):
    """Embedding access for retrieval indexing."""

    @property
    def capabilities(self) -> ModelCapabilities:
        """Static capabilities of the embedding model."""
        ...

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed a batch of texts."""
        ...


@runtime_checkable
class RerankerPort(Protocol):
    """Optional reranking access; not required for the first release."""

    def rerank(self, request: RerankRequest) -> RerankResponse:
        """Score candidate passages against a query."""
        ...


@runtime_checkable
class ModelGatewayPort(Protocol):
    """Composition-level facade used by the application layer.

    It resolves a logical profile to a concrete adapter and exposes capability
    inspection, so that a workflow can validate a capability *before* building the
    request.
    """

    def capabilities(self, model_profile: str) -> ModelCapabilities:
        """Return capabilities for a configured profile."""
        ...

    def generate_text(self, request: ModelRequest) -> ModelResponse:
        """Generate free text for the profile named in the request."""
        ...

    def generate_structured(self, request: ModelRequest, schema: SchemaReference) -> ModelResponse:
        """Generate a schema-constrained payload for the profile in the request."""
        ...

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed texts with the profile named in the request."""
        ...
