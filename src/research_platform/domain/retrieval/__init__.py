"""Internal retrieval contracts of iteration 4."""

from __future__ import annotations

from research_platform.domain.retrieval.models import (
    CHUNKER_VERSION,
    EVIDENCE_RESOLVER_VERSION,
    RETRIEVAL_MANIFEST_VERSION,
    ChunkKind,
    ChunkManifest,
    DocumentChunk,
    ElementRef,
    EvidenceLedger,
    EvidenceObject,
    EvidenceProposal,
    EvidenceTableRef,
    MatchStatus,
    RetrievalContextRecord,
    RetrievalManifest,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
    TableRef,
)

__all__ = [
    "CHUNKER_VERSION",
    "EVIDENCE_RESOLVER_VERSION",
    "RETRIEVAL_MANIFEST_VERSION",
    "ChunkKind",
    "ChunkManifest",
    "DocumentChunk",
    "ElementRef",
    "EvidenceLedger",
    "EvidenceObject",
    "EvidenceProposal",
    "EvidenceTableRef",
    "MatchStatus",
    "RetrievalContextRecord",
    "RetrievalManifest",
    "RetrievalMode",
    "RetrievalQuery",
    "RetrievedChunk",
    "TableRef",
]
