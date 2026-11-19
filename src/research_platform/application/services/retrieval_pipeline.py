"""Retrieval pipeline: chunking/index preparation, per-group context and evidence resolution.

This module keeps the workflow nodes thin: it owns the sequence "chunks → index → query →
retrieve → context → resolve evidence" and returns typed artefacts, while LangGraph keeps
deciding *when* each step runs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from research_platform.application.ports.context_budget import TokenCounterPort
from research_platform.application.ports.embedding import (
    EmbeddingPort,
    EmbeddingRequest,
)
from research_platform.application.services.chunking import (
    ChunkingConfig,
    StructuralChunker,
)
from research_platform.application.services.evidence_resolver import EvidenceResolver
from research_platform.application.services.retrieval import (
    RetrievalConfig,
    RetrievalContextBuilder,
    RetrievalModeSelector,
    RetrievalQueryBuilder,
    RetrievalResult,
    RetrievalService,
)
from research_platform.config import (
    ChunkingSettings,
    EmbeddingSettings,
    RetrievalSettings,
)
from research_platform.domain.documents.parsed import ParsedDocument
from research_platform.domain.documents.quality import QualityStatus
from research_platform.domain.extraction.models import ExtractionGroup, field_keys_of
from research_platform.domain.retrieval.models import (
    DocumentChunk,
    EvidenceProposal,
    EvidenceLedger,
    RetrievalContextRecord,
    RetrievalMode,
    RetrievalQuery,
)

__all__ = ["PreparedRetrieval", "RetrievalPipeline"]

_CHUNK_OPEN = "[[chunk {chunk_id}]]"
_CHUNK_CLOSE = "[[/chunk]]"


@dataclass(slots=True)
class PreparedRetrieval:
    """Chunks + dense vectors + retrieval service of one document."""

    chunks: list[DocumentChunk]
    by_id: dict[str, DocumentChunk]
    chunk_manifest: dict[str, object]
    embeddings: dict[str, list[float]] | None
    embedding_manifest: dict[str, object] | None
    service: RetrievalService
    warnings: list[str] = field(default_factory=list)

    @property
    def full_context_text(self) -> str:
        """All chunks with annotations, in sequence order (used by FULL_CONTEXT)."""
        return "\n\n".join(
            f"{_CHUNK_OPEN.format(chunk_id=chunk.chunk_id)}\n{chunk.text}\n{_CHUNK_CLOSE}"
            for chunk in self.chunks
        )


class RetrievalPipeline:
    """Compose chunking, retrieval and evidence resolution for one run."""

    def __init__(
        self,
        *,
        counter: TokenCounterPort,
        chunking: ChunkingSettings,
        retrieval: RetrievalSettings,
        embedding: EmbeddingSettings,
        embeddings: EmbeddingPort | None = None,
    ) -> None:
        self._counter = counter
        self._chunking = chunking
        self._retrieval = retrieval
        self._embedding = embedding
        self._embeddings = embeddings
        self._resolver = EvidenceResolver(version=retrieval.resolver_version)
        self._mode_selector = RetrievalModeSelector(counter=counter)
        self._context_builder = RetrievalContextBuilder(counter=counter)

    # ------------------------------------------------------------------ prepare

    def prepare(self, document: ParsedDocument, *, created_at: str) -> PreparedRetrieval:
        """Chunk the document and build the dense vectors when a profile is configured."""
        chunker = StructuralChunker(
            counter=self._counter,
            config=ChunkingConfig(
                target_tokens=self._chunking.target_tokens,
                max_tokens=self._chunking.max_tokens,
                overlap_tokens=self._chunking.overlap_tokens,
                min_tokens=self._chunking.min_tokens,
            ),
        )
        chunking_result = chunker.chunk(document, created_at=created_at)
        chunks = chunking_result.chunks
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        vectors: dict[str, list[float]] | None = None
        embedding_manifest: dict[str, object] | None = None
        execution_mode = self._embedding.execution_mode
        provider: str | None = None
        model_id: str | None = None
        if self._embeddings is not None and self._embedding.profile_id:
            response = self._embeddings.embed(
                EmbeddingRequest(
                    request_id="req_" + hashlib.sha256(
                        document.document_id.encode()
                    ).hexdigest()[:12],
                    profile_id=self._embedding.profile_id,
                    texts=[chunk.text for chunk in chunks] or [" "],
                    chunk_ids=[chunk.chunk_id for chunk in chunks] or [" "],
                    document_id=document.document_id,
                    source_sha256=document.source_sha256,
                )
            )
            vectors = {
                chunk_id: vector
                for chunk_id, vector in zip(response.chunk_ids, response.vectors, strict=True)
                if chunk_id in by_id
            }
            provider = response.provider_id
            model_id = response.model_id
            execution_mode = response.execution_mode
            embedding_manifest = {
                "schema_version": "1.0",
                "embedding_provider": response.provider_id,
                "embedding_model_id": response.model_id,
                "embedding_revision": response.model_revision,
                "dimension": response.dimension,
                "normalized": response.normalized,
                "execution_mode": response.execution_mode,
                "vector_count": len(vectors),
                "chunk_ids": sorted(vectors)[:5000],
            }
        service = RetrievalService(
            chunks=chunks,
            embeddings=vectors,
            embedding_status=execution_mode,
            embedding_provider=provider,
            embedding_model_id=model_id,
            config=RetrievalConfig(
                lexical_top_k=self._retrieval.lexical_top_k,
                dense_top_k=self._retrieval.dense_top_k,
                final_top_k=self._retrieval.final_top_k,
                rrf_k=self._retrieval.rrf_k,
                fallback_scan=self._retrieval.fallback_scan,
                profile_id=self._retrieval.profile_id,
            ),
        )
        return PreparedRetrieval(
            chunks=chunks,
            by_id=by_id,
            chunk_manifest=(
                chunking_result.manifest.model_dump(mode="json")
                if chunking_result.manifest
                else {}
            ),
            embeddings=vectors,
            embedding_manifest=embedding_manifest,
            service=service,
            warnings=list(chunking_result.warnings),
        )

    # -------------------------------------------------------------------- query

    def build_query(
        self,
        *,
        document: ParsedDocument,
        group: ExtractionGroup,
        mode: RetrievalMode,
    ) -> RetrievalQuery:
        """Deterministic group query."""
        return RetrievalQueryBuilder(
            language=str(getattr(document.language, "value", document.language)),
            profile_id=self._retrieval.profile_id,
        ).build(document=document, group=group, mode=mode)

    def select_mode(
        self,
        *,
        prompt: str,
        prepared: PreparedRetrieval,
        document: ParsedDocument,
        input_budget_tokens: int,
    ) -> RetrievalMode:
        """FULL_CONTEXT if it fits, else HYBRID/RAG according to policy."""
        override = None if self._retrieval.mode_policy == "auto" else self._retrieval.mode_policy
        return self._mode_selector.select(
            prompt=prompt,
            full_context_text=prepared.full_context_text,
            canonical_text=self._canonical_text(document),
            input_budget_tokens=input_budget_tokens,
            has_global_context=self._global_context(prepared) is not None,
            override=override,
        )

    def build_context(
        self,
        *,
        document: ParsedDocument,
        prepared: PreparedRetrieval,
        query: RetrievalQuery,
        prompt: str,
        mode: RetrievalMode,
    ) -> tuple[str, RetrievalContextRecord, RetrievalResult]:
        """Retrieve (unless FULL_CONTEXT) and assemble the context for one group."""
        if mode is RetrievalMode.FULL_CONTEXT:
            result = RetrievalResult(
                query_id=query.query_id,
                mode=mode,
                status="full_context",
                total_chunks=len(prepared.chunks),
            )
            text = prepared.full_context_text
            record = RetrievalContextRecord(
                group_id=query.group_id,
                retrieval_mode=mode,
                query_id=query.query_id,
                retrieval_profile_id=query.retrieval_profile_id,
                selected_chunk_ids=[chunk.chunk_id for chunk in prepared.chunks],
                ranking=[],
                context_chars=len(text),
                context_tokens_estimate=self._counter.count(text),
                message_guard_passed=len(prompt) + len(text) <= 8192,
            )
            return text, record, result
        result = prepared.service.search(query, query_vector=self._query_vector(query))
        global_context = self._global_context(prepared) if mode is RetrievalMode.HYBRID else None
        return self._context_builder.build(
            query=query,
            result=result,
            chunks=prepared.by_id,
            prompt=prompt,
            mode=mode,
            global_context=global_context,
        )

    # ----------------------------------------------------------------- evidence

    def resolve(
        self,
        *,
        document: ParsedDocument,
        prepared: PreparedRetrieval,
        proposals: list[EvidenceProposal],
        run_id: str,
        created_at: str,
    ) -> tuple[EvidenceLedger, list[object]]:
        """Resolve proposals against the parsed document."""
        return self._resolver.resolve(
            proposals=proposals,
            document=document,
            chunks=prepared.by_id,
            run_id=run_id,
            created_at=created_at,
        )

    # ------------------------------------------------------------------ helpers

    def _query_vector(self, query: RetrievalQuery) -> list[float] | None:
        if self._embeddings is None or not self._embedding.profile_id:
            return None
        response = self._embeddings.embed(
            EmbeddingRequest(
                request_id=query.query_id,
                profile_id=self._embedding.profile_id,
                texts=[query.query_text],
            )
        )
        return response.vectors[0]

    @staticmethod
    def _canonical_text(document: ParsedDocument) -> str:
        return " ".join(element.text for element in document.elements)

    @staticmethod
    def _global_context(prepared: PreparedRetrieval) -> str | None:
        """Compact deterministic global context: headings + first lines of each section."""
        if not prepared.chunks:
            return None
        lines: list[str] = []
        for chunk in prepared.chunks:
            if chunk.chunk_kind.value in {"heading", "caption"}:
                lines.append(f"- {chunk.text.strip()[:200]}")
            if len(lines) >= 12:
                break
        if not lines:
            lines = [f"- {prepared.chunks[0].text.strip()[:200]}"]
        return "Document outline:\n" + "\n".join(lines)


def evidence_proposals_from_payload(
    *,
    payload_candidates: list[dict[str, object]],
    selected_chunk_ids: list[str],
    allowed_fields: set[str],
    document_id: str,
    group: str,
) -> tuple[list[EvidenceProposal], list[str]]:
    """Turn raw model candidates into validated proposals; rejects foreign fields/chunks."""
    proposals: list[EvidenceProposal] = []
    warnings: list[str] = []
    for index, candidate in enumerate(payload_candidates):
        field_path = str(candidate.get("field_path") or "")
        chunk_id = str(candidate.get("chunk_id") or "")
        quote = str(candidate.get("quote") or "")
        if field_path not in allowed_fields:
            warnings.append(f"candidate {index} references a field of another group")
            continue
        if chunk_id not in selected_chunk_ids:
            warnings.append(f"candidate {index} references a chunk outside the context")
            continue
        if not quote.strip():
            warnings.append(f"candidate {index} has an empty quote")
            continue
        candidate_id = "cand_" + hashlib.sha256(
            f"{document_id}|{group}|{index}|{field_path}|{chunk_id}".encode()
        ).hexdigest()[:16]
        proposals.append(
            EvidenceProposal(
                candidate_id=candidate_id,
                field_path=field_path,
                chunk_id=chunk_id,
                quote_candidate=quote[:2000],
            )
        )
    return proposals, warnings


def fields_of_group(group: ExtractionGroup) -> set[str]:
    """All field paths of one group."""
    return set(field_keys_of(group))


def quality_is_ready(status: QualityStatus) -> bool:
    """Whether retrieval may run for this quality status."""
    return status in {QualityStatus.PASSED, QualityStatus.PASSED_WITH_WARNINGS}
