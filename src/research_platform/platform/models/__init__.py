"""Model adapters.

Provider adapters live here.  Today only the fake adapters exist: the real provider is not
selected (``OI-001``…``OI-004``), so a real adapter would be guesswork.  The fake adapters
are shipped (not test-only) because the platform must be runnable end to end without a
provider, and they are explicit about being fake in every response.
"""

from __future__ import annotations

from research_platform.platform.models.fake_model_adapter import (
    FAKE_PROVIDER_ID,
    FakeChatModel,
    FakeEmbeddingModel,
    FakeModelGateway,
    FakeReranker,
    capabilities_from_profile,
    scripted_service_errors,
)

__all__ = [
    "FAKE_PROVIDER_ID",
    "FakeChatModel",
    "FakeEmbeddingModel",
    "FakeModelGateway",
    "FakeReranker",
    "capabilities_from_profile",
    "scripted_service_errors",
]
