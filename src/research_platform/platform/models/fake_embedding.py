"""Deterministic fake embedding adapter (test/simulation only).

The vectors are a stable hash projection of the text, not a semantic model.  They exist to
exercise the dense index, hybrid fusion, persistence and resume paths deterministically, and
they say so in every response (``provider=fake``, ``execution_mode=simulation``).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping

from research_platform.application.ports.embedding import (
    EmbeddingCapabilities,
    EmbeddingContractError,
    EmbeddingExecutionMode,
    EmbeddingRequest,
    EmbeddingResponse,
)

__all__ = ["FakeEmbeddingAdapter"]

FAKE_PROVIDER_ID = "fake-embedding"
FAKE_MODEL_ID = "fake-hash-embedding-v1"


class FakeEmbeddingAdapter:
    """Hash-based deterministic embeddings for tests and simulations."""

    def __init__(
        self,
        *,
        profiles: Mapping[str, EmbeddingCapabilities] | None = None,
        profile_id: str = "fake-embedding",
        dimension: int = 64,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if profiles is None:
            profiles = {
                profile_id: EmbeddingCapabilities(
                    profile_id=profile_id,
                    provider_id=FAKE_PROVIDER_ID,
                    model_id=FAKE_MODEL_ID,
                    dimension=dimension,
                    normalized=True,
                    is_fake=True,
                    execution_mode=EmbeddingExecutionMode.SIMULATION,
                )
            }
        self._profiles = dict(profiles)

    @property
    def provider_id(self) -> str:
        """Provider identity recorded in manifests."""
        return FAKE_PROVIDER_ID

    @property
    def is_fake(self) -> bool:
        """Always true: this adapter must never be presented as a real model."""
        return True

    def capabilities(self, profile_id: str) -> EmbeddingCapabilities:
        """Capabilities of one configured profile."""
        if profile_id not in self._profiles:
            raise EmbeddingContractError(f"unknown embedding profile {profile_id!r}")
        return self._profiles[profile_id]

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed the batch deterministically; nothing is read from the environment."""
        capabilities = self.capabilities(request.profile_id)
        if len(request.texts) > capabilities.max_batch_size:
            raise EmbeddingContractError(
                f"batch of {len(request.texts)} exceeds max_batch_size "
                f"{capabilities.max_batch_size}"
            )
        vectors = [self._vector(text, capabilities.dimension) for text in request.texts]
        return EmbeddingResponse(
            request_id=request.request_id,
            provider_id=capabilities.provider_id,
            model_id=capabilities.model_id,
            model_revision="1",
            dimension=capabilities.dimension,
            normalized=capabilities.normalized,
            execution_mode=EmbeddingExecutionMode.SIMULATION,
            vectors=vectors,
            chunk_ids=request.chunk_ids,
            latency_ms=0.0,
            usage_texts=len(request.texts),
        )

    @staticmethod
    def _vector(text: str, dimension: int) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [
            (digest[index % len(digest)] / 255.0) - 0.5 for index in range(dimension)
        ]
        norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        return [round(value / norm, 12) for value in raw]
