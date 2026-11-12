"""Fake model adapters.

These adapters make the platform runnable, testable and demonstrable without a provider.
They are explicit about it: every response carries the ``FAKE`` marker, the profile exposes
``is_fake=True``, and no adapter here ever touches the network.

Nothing in this module produces scientific content.  A structured payload is a fixed
technical object that echoes the request; it is not an extraction result.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from research_platform.application.ports.model_gateway.errors import (
    ModelMalformedResponseError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnsupportedCapabilityError,
    normalize_provider_error,
)
from research_platform.application.ports.model_gateway.protocols import require_structured_output
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
from research_platform.config import ModelProfileSettings

__all__ = [
    "FAKE_PROVIDER_ID",
    "FakeChatModel",
    "FakeEmbeddingModel",
    "FakeModelGateway",
    "FakeReranker",
    "capabilities_from_profile",
]

FAKE_PROVIDER_ID = "fake_provider"
FAKE_TEXT = "FAKE MODEL OUTPUT - no scientific content"


def capabilities_from_profile(profile: ModelProfileSettings) -> ModelCapabilities:
    """Build provider-neutral capabilities from one configured profile."""
    return ModelCapabilities(
        provider_id=profile.provider,
        model_id=profile.model_id,
        max_context_tokens=profile.max_context_tokens,
        max_output_tokens=profile.max_output_tokens,
        supports_structured_output=profile.supports_structured_output,
        supports_json_schema=profile.supports_json_schema,
        supports_streaming=profile.supports_streaming,
        supports_tool_calls=profile.supports_tool_calls,
        supports_embeddings=profile.supports_embeddings,
        supports_reranking=profile.supports_reranking,
    )


class FakeChatModel:
    """Deterministic chat adapter implementing ``StructuredOutputModelPort``."""

    is_fake = True

    def __init__(
        self,
        *,
        capabilities: ModelCapabilities,
        payload: Mapping[str, Any] | None = None,
        text: str = FAKE_TEXT,
        scripted_errors: Mapping[str, BaseException] | None = None,
        latency_ms: float = 1.0,
    ) -> None:
        self._capabilities = capabilities
        self._payload = dict(payload) if payload is not None else {"ok": True, "fake": True}
        self._text = text
        self._scripted_errors = dict(scripted_errors or {})
        self._latency_ms = latency_ms
        self.calls: list[ModelRequest] = []

    @property
    def capabilities(self) -> ModelCapabilities:
        """Static capabilities of the fake model."""
        return self._capabilities

    def _maybe_raise(self, request: ModelRequest) -> None:
        scripted = self._scripted_errors.get(request.task) or self._scripted_errors.get("*")
        if scripted is not None:
            raise normalize_provider_error(
                scripted,
                provider_id=self._capabilities.provider_id,
                model_id=self._capabilities.model_id,
                request_id=request.request_id,
            )

    def _usage(self, request: ModelRequest) -> TokenUsage:
        # Deterministic and obviously synthetic: one token per message character bucket.
        input_tokens = sum(len(message.content) for message in request.messages) // 4
        output_tokens = max(1, len(self._text) // 4)
        return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    def generate_text(self, request: ModelRequest) -> ModelResponse:
        """Return a canned text response (or raise a scripted error)."""
        self.calls.append(request)
        self._maybe_raise(request)
        return ModelResponse(
            request_id=request.request_id,
            provider_id=self._capabilities.provider_id,
            model_id=self._capabilities.model_id,
            finish_reason=FinishReason.STOP,
            raw_text=self._text,
            usage=self._usage(request),
            latency_ms=self._latency_ms,
            warnings=["fake adapter: no real provider was called"],
        )

    def generate_structured(self, request: ModelRequest, schema: SchemaReference) -> ModelResponse:
        """Check the capability first, then return a canned structured payload."""
        require_structured_output(self.capabilities)
        self.calls.append(request)
        self._maybe_raise(request)
        payload = dict(self._payload)
        payload.setdefault("fake", True)
        payload.setdefault("schema_id", schema.schema_id)
        return ModelResponse(
            request_id=request.request_id,
            provider_id=self._capabilities.provider_id,
            model_id=self._capabilities.model_id,
            finish_reason=FinishReason.STOP,
            structured_payload=payload,
            usage=self._usage(request),
            latency_ms=self._latency_ms,
            warnings=["fake adapter: no real provider was called"],
        )

    def raise_scripted(self, task: str, error: BaseException) -> None:
        """Register a scripted failure for a task (tests and demos)."""
        self._scripted_errors[task] = error


class FakeEmbeddingModel:
    """Deterministic embedding adapter (no model is loaded)."""

    is_fake = True

    def __init__(self, *, capabilities: ModelCapabilities, dimensions: int = 8) -> None:
        self._capabilities = capabilities
        self._dimensions = dimensions
        self.calls: list[EmbeddingRequest] = []

    @property
    def capabilities(self) -> ModelCapabilities:
        """Static capabilities of the fake embedding model."""
        return self._capabilities

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return deterministic vectors derived from text length."""
        self.calls.append(request)
        vectors = [
            [((len(text) + index) % 7) / 7.0 for index in range(self._dimensions)]
            for text in request.texts
        ]
        return EmbeddingResponse(
            request_id=request.request_id,
            provider_id=self._capabilities.provider_id,
            model_id=self._capabilities.model_id,
            embedding_model_id=self._capabilities.model_id,
            dimensions=self._dimensions,
            vectors=vectors,
            latency_ms=1.0,
            created_at=datetime.now(UTC),
        )


class FakeReranker:
    """Deterministic reranker adapter (descending scores, no model)."""

    is_fake = True

    def rerank(self, request: RerankRequest) -> RerankResponse:
        """Return descending scores, one per candidate."""
        return RerankResponse(
            request_id=request.request_id,
            provider_id=FAKE_PROVIDER_ID,
            model_id="fake_reranker_1",
            scores=[1.0 - 0.1 * index for index in range(len(request.candidates))],
            latency_ms=1.0,
        )


class FakeModelGateway:
    """Profile-aware fake implementing ``ModelGatewayPort``.

    The gateway is what the application and the graph see, so the platform can be exercised
    end to end without a provider.  It refuses unknown profiles instead of silently falling
    back — a typo in a profile name must not look like a working configuration.
    """

    is_fake = True
    provider_id = FAKE_PROVIDER_ID

    def __init__(
        self,
        *,
        profiles: Mapping[str, ModelCapabilities],
        chat_models: Mapping[str, FakeChatModel] | None = None,
        embedding_models: Mapping[str, FakeEmbeddingModel] | None = None,
        default_profile: str | None = None,
        scripted_errors: Mapping[str, BaseException] | None = None,
    ) -> None:
        if not profiles:
            raise ValueError("FakeModelGateway requires at least one profile")
        self._profiles = dict(profiles)
        self._default = default_profile or next(iter(sorted(self._profiles)))
        self._scripted_errors = dict(scripted_errors or {})
        self._chats = dict(chat_models or {})
        self._embeddings = dict(embedding_models or {})
        for name, capabilities in self._profiles.items():
            self._chats.setdefault(
                name,
                FakeChatModel(capabilities=capabilities, scripted_errors=self._scripted_errors),
            )

    # ------------------------------------------------------------------ profiles

    @property
    def profiles(self) -> tuple[str, ...]:
        """Configured profile names."""
        return tuple(sorted(self._profiles))

    @property
    def calls(self) -> list[ModelRequest]:
        """Every request the fake chat models received (diagnostics and tests)."""
        collected: list[ModelRequest] = []
        for model in self._chats.values():
            collected.extend(model.calls)
        return collected

    @property
    def default_profile(self) -> str:
        """Profile used when a request does not name one."""
        return self._default

    def _resolve(self, profile: str | None) -> str:
        name = profile or self._default
        if name not in self._profiles:
            raise ModelUnsupportedCapabilityError(
                f"unknown model profile {name!r}; configured profiles: "
                f"{', '.join(sorted(self._profiles))}",
                provider_id=self.provider_id,
            )
        return name

    def capabilities(self, model_profile: str) -> ModelCapabilities:
        """Capabilities of one configured profile."""
        return self._profiles[self._resolve(model_profile)]

    # ------------------------------------------------------------------- calling

    def generate_text(self, request: ModelRequest) -> ModelResponse:
        """Generate fake text for the request's profile."""
        profile = self._resolve(request.model_profile)
        return self._chats[profile].generate_text(request)

    def generate_structured(self, request: ModelRequest, schema: SchemaReference) -> ModelResponse:
        """Generate a fake structured payload, after the capability gate."""
        profile = self._resolve(request.model_profile)
        return self._chats[profile].generate_structured(request, schema)

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed texts with the fake embedding model of the request's profile."""
        profile = self._resolve(request.model_profile)
        capabilities = self._profiles[profile]
        if not capabilities.supports_embeddings:
            raise ModelUnsupportedCapabilityError(
                f"profile {profile!r} does not support embeddings",
                provider_id=capabilities.provider_id,
                model_id=capabilities.model_id,
            )
        model = self._embeddings.setdefault(profile, FakeEmbeddingModel(capabilities=capabilities))
        return model.embed(request)

    def raise_scripted(self, task: str, error: BaseException) -> None:
        """Register a scripted failure for a task across all fake chat models."""
        self._scripted_errors[task] = error
        for model in self._chats.values():
            model.raise_scripted(task, error)


def scripted_service_errors() -> dict[str, BaseException]:
    """A convenience set of scripted failures used by tests and the CLI demo."""
    return {
        "timeout": ModelTimeoutError("scripted timeout"),
        "rate_limit": ModelRateLimitError("scripted rate limit"),
        "invalid_response": ModelMalformedResponseError("scripted malformed response"),
    }
