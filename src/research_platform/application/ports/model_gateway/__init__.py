"""Provider-neutral Model Gateway contract.

Location note: the interface lives in the *application* ports package, not in
``platform/model_gateway``, because the architecture rule is that
``application`` may depend on abstract ports while ``platform`` contains the
adapters that implement them.  Keeping the DTOs and protocols here means
``platform`` (and therefore any vendor SDK) can never leak into a contract, while
``platform.model_gateway`` remains the adapter home.
"""

from __future__ import annotations

from research_platform.application.ports.model_gateway.errors import (
    ModelAuthError,
    ModelContextOverflowError,
    ModelError,
    ModelMalformedResponseError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnknownError,
    ModelUnsupportedCapabilityError,
    normalize_provider_error,
)
from research_platform.application.ports.model_gateway.protocols import (
    ChatModelPort,
    EmbeddingModelPort,
    ModelGatewayPort,
    RerankerPort,
    StructuredOutputModelPort,
    require_structured_output,
)
from research_platform.application.ports.model_gateway.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    FinishReason,
    GenerationParameters,
    Message,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    RerankRequest,
    RerankResponse,
    Role,
    SchemaReference,
    TokenUsage,
)

__all__ = [
    "ChatModelPort",
    "EmbeddingModelPort",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "FinishReason",
    "GenerationParameters",
    "Message",
    "ModelAuthError",
    "ModelCapabilities",
    "ModelContextOverflowError",
    "ModelError",
    "ModelGatewayPort",
    "ModelMalformedResponseError",
    "ModelProviderUnavailableError",
    "ModelRateLimitError",
    "ModelRequest",
    "ModelResponse",
    "ModelTimeoutError",
    "ModelUnknownError",
    "ModelUnsupportedCapabilityError",
    "RerankRequest",
    "RerankResponse",
    "RerankerPort",
    "Role",
    "SchemaReference",
    "StructuredOutputModelPort",
    "TokenUsage",
    "normalize_provider_error",
    "require_structured_output",
]
