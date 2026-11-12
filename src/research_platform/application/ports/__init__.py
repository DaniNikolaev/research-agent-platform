"""Infrastructure-independent ports.

Every port is a ``typing.Protocol`` so that adapters do not inherit from platform code, and
so that a fake implementation in a test is a first-class implementation.

Ports are deliberately synchronous: the first release is a local CLI-first modular
monolith with one document per run.  Asynchronous execution can be added later by an
adapter without changing the domain contracts.
"""

from __future__ import annotations

from research_platform.application.ports.artifact_store import ArtifactStorePort
from research_platform.application.ports.checkpoint import CheckpointProviderPort
from research_platform.application.ports.document_repository import DocumentRepositoryPort
from research_platform.application.ports.model_gateway import (
    ChatModelPort,
    EmbeddingModelPort,
    ModelGatewayPort,
    RerankerPort,
    StructuredOutputModelPort,
    require_structured_output,
)
from research_platform.application.ports.run_repository import (
    RunRepositoryError,
    RunRepositoryPort,
)

__all__ = [
    "ArtifactStorePort",
    "ChatModelPort",
    "CheckpointProviderPort",
    "DocumentRepositoryPort",
    "EmbeddingModelPort",
    "ModelGatewayPort",
    "RerankerPort",
    "RunRepositoryError",
    "RunRepositoryPort",
    "StructuredOutputModelPort",
    "require_structured_output",
]
