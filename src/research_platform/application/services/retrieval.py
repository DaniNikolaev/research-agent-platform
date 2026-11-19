"""Retrieval services: lexical BM25, dense cosine, RRF hybrid, context building, coverage.

All components are deterministic Python.  Dense retrieval only runs when a real or explicitly
simulated embedding profile provides vectors; otherwise retrieval degrades to a clearly labelled
``lexical_only`` status.  A retrieval miss never means "the document does not say it": coverage
is tracked separately and ``not_found`` remains a Python decision requiring a complete scan.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from research_platform.application.ports.context_budget import TokenCounterPort
from research_platform.domain.coverage.models import (
    CoverageStatus,
    FieldCoverageRecord,
    PageRange,
    RetrievalMode as CoverageRetrievalMode,
    RetrievalStrategy,
    SearchAttempt,
    SearchAttemptOutcome,
)
from research_platform.domain.documents.parsed import ParsedDocument
from research_platform.domain.documents.quality import QualityStatus
from research_platform.domain.extraction.models import ExtractionGroup, field_keys_of
from research_platform.domain.retrieval.models import (
    ChunkKind,
    DocumentChunk,
    RetrievalContextRecord,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
)

__all__ = [
    "RetrievalConfig",
    "RetrievalResult",
    "RetrievalService",
    "RetrievalQueryBuilder",
    "RetrievalModeSelector",
    "RetrievalContextBuilder",
    "build_coverage_records",
    "LEXICAL_ANALYZER_VERSION",
    "tokenize",
]

LEXICAL_ANALYZER_VERSION = "bm25-unicode-v1"
MESSAGE_CONTENT_LIMIT = 8192
_TOKEN_RE = re.compile(
    r"[0-9A-Za-zА-Яа-яЁё]+(?:[.,][0-9]+)?|[+\-−=<>≤≥±×÷^*/%]"
)
_CHUNK_OPEN = "[[chunk {chunk_id}]]"
_CHUNK_CLOSE = "[[/chunk]]"

QUERY_TERMS: dict[ExtractionGroup, tuple[str, ...]] = {
    ExtractionGroup.G1_BIBLIOGRAPHY: (
        "title",
        "authors",
        "year",
        "journal",
        "doi",
        "keywords",
        "название",
        "авторы",
        "год",
        "журнал",
        "ключевые слова",
    ),
    ExtractionGroup.G2_RESEARCH_DESIGN: (
        "research question",
        "objective",
        "hypotheses",
        "research design",
        "theoretical framework",
        "исследовательский вопрос",
        "цель",
        "гипотеза",
        "дизайн исследования",
    ),
    ExtractionGroup.G3_METHODS_DATA_VARIABLES: (
        "method",
        "data",
        "sample",
        "variables",
        "estimation",
        "model",
        "метод",
        "данные",
        "выборка",
        "переменные",
        "модель",
    ),
    ExtractionGroup.G4_FINDINGS_IMPLICATIONS: (
        "findings",
        "results",
        "limitations",
        "future research",
        "contribution",
        "результаты",
        "ограничения",
        "будущие исследования",
        "вклад",
    ),
}


def tokenize(text: str) -> list[str]:
    """Deterministic Unicode-safe tokenizer (numbers, math signs and negation survive)."""
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Retrieval knobs (provisional, to be evaluated on a real corpus)."""

    lexical_top_k: int = 8
    dense_top_k: int = 8
    final_top_k: int = 6
    rrf_k: int = 60
    fallback_scan: bool = True
    profile_id: str = "default"

    def as_dict(self) -> dict[str, object]:
        """Stable representation for the configuration hash."""
        return {
            "lexical_top_k": self.lexical_top_k,
            "dense_top_k": self.dense_top_k,
            "final_top_k": self.final_top_k,
            "rrf_k": self.rrf_k,
            "fallback_scan": self.fallback_scan,
            "analyzer": LEXICAL_ANALYZER_VERSION,
        }

    @property
    def config_hash(self) -> str:
        """Short deterministic hash of the retrieval configuration."""
        return hashlib.sha256(
            json.dumps(self.as_dict(), sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]


@dataclass(slots=True)
class RetrievalResult:
    """Ranked chunks plus diagnostics of one group retrieval."""

    query_id: str
    mode: RetrievalMode
    status: str
    ranking: list[RetrievedChunk] = field(default_factory=list)
    attempts: list[SearchAttempt] = field(default_factory=list)
    fallback_performed: bool = False
    scanned_chunk_ids: list[str] = field(default_factory=list)
    total_chunks: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def chunk_ids(self) -> list[str]:
        """Selected chunk ids in rank order."""
        return [item.chunk_id for item in self.ranking]

    @property
    def coverage_ratio(self) -> float:
        """Share of the document chunks examined by the search (0..1)."""
        if not self.total_chunks:
            return 0.0
        return min(1.0, len(set(self.scanned_chunk_ids)) / self.total_chunks)


class _LexicalIndex:
    """In-memory BM25 over the chunk set."""

    def __init__(self, chunks: list[DocumentChunk]) -> None:
        self._ids = [chunk.chunk_id for chunk in chunks]
        self._tokens = {chunk.chunk_id: tokenize(chunk.text) for chunk in chunks}
        self._lengths = {chunk_id: len(tokens) for chunk_id, tokens in self._tokens.items()}
        self._avgdl = sum(self._lengths.values()) / max(1, len(self._lengths))
        self._df: Counter[str] = Counter()
        for tokens in self._tokens.values():
            self._df.update(set(tokens))

    def score(self, query: str, *, k1: float = 1.5, b: float = 0.75) -> dict[str, float]:
        """BM25 score of every chunk for one query."""
        terms = [term for term in tokenize(query) if len(term) >= 2]
        if not terms:
            return {}
        total = max(1, len(self._ids))
        scores: dict[str, float] = {}
        for chunk_id, tokens in self._tokens.items():
            counts = Counter(tokens)
            length = self._lengths[chunk_id] or 1
            score = 0.0
            for term in terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
                denominator = frequency + k1 * (1 - b + b * length / max(self._avgdl, 1))
                score += idf * frequency * (k1 + 1) / denominator
            if score > 0:
                scores[chunk_id] = round(score, 6)
        return scores


class RetrievalService:
    """Lexical, dense and hybrid retrieval restricted to one document."""

    def __init__(
        self,
        *,
        chunks: list[DocumentChunk],
        embeddings: dict[str, list[float]] | None = None,
        embedding_status: str = "not_configured",
        embedding_provider: str | None = None,
        embedding_model_id: str | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._lexical = _LexicalIndex(chunks)
        self._vectors = embeddings or {}
        self.embedding_status = embedding_status
        self.embedding_provider = embedding_provider
        self.embedding_model_id = embedding_model_id
        self._config = config or RetrievalConfig()

    @property
    def config(self) -> RetrievalConfig:
        """Active retrieval configuration."""
        return self._config

    @property
    def chunk_ids(self) -> list[str]:
        """All chunk ids of the indexed document."""
        return list(self._chunks)

    def chunk(self, chunk_id: str) -> DocumentChunk | None:
        """Look up one chunk."""
        return self._chunks.get(chunk_id)

    def search(
        self,
        query: RetrievalQuery,
        *,
        query_vector: list[float] | None = None,
    ) -> RetrievalResult:
        """Run one retrieval request with document/source filtering and RRF fusion."""
        result = RetrievalResult(
            query_id=query.query_id,
            mode=query.retrieval_mode,
            status="ok",
            total_chunks=len(self._chunks),
        )
        lexical_scores = self._lexical.score(query.query_text)
        for alternate in query.alternate_queries:
            for chunk_id, score in self._lexical.score(alternate).items():
                lexical_scores[chunk_id] = max(lexical_scores.get(chunk_id, 0.0), score)
        lexical_ranking = sorted(
            lexical_scores.items(), key=lambda item: (-item[1], item[0])
        )[: self._config.lexical_top_k]
        result.attempts.append(
            SearchAttempt(
                attempt_id=f"att_{query.group_id}_lexical",
                step_id=f"{query.group_id}:lexical",
                retrieval_strategy=RetrievalStrategy.LEXICAL,
                retrieved_block_ids=[chunk_id for chunk_id, _ in lexical_ranking],
                outcome=(
                    SearchAttemptOutcome.SUCCEEDED
                    if lexical_ranking
                    else SearchAttemptOutcome.NO_HITS
                ),
            )
        )

        dense_ranking: list[tuple[str, float]] = []
        if self._vectors and query_vector is not None:
            similarities = []
            for chunk_id, vector in self._vectors.items():
                if len(vector) != len(query_vector):
                    continue
                similarity = _cosine(vector, query_vector)
                if similarity > 0:
                    similarities.append((chunk_id, similarity))
            dense_ranking = sorted(similarities, key=lambda item: (-item[1], item[0]))[
                : self._config.dense_top_k
            ]
            result.attempts.append(
                SearchAttempt(
                    attempt_id=f"att_{query.group_id}_dense",
                    step_id=f"{query.group_id}:dense",
                    retrieval_strategy=RetrievalStrategy.DENSE,
                    retrieved_block_ids=[chunk_id for chunk_id, _ in dense_ranking],
                    outcome=(
                        SearchAttemptOutcome.SUCCEEDED
                        if dense_ranking
                        else SearchAttemptOutcome.NO_HITS
                    ),
                )
            )
            result.status = "hybrid"
        else:
            result.status = "lexical_only"
            if query.retrieval_mode is RetrievalMode.HYBRID:
                result.warnings.append(
                    "dense retrieval is unavailable; hybrid request degraded to lexical_only"
                )

        fused = self._fuse(lexical_ranking, dense_ranking)
        result.ranking = fused[: self._config.final_top_k]
        result.scanned_chunk_ids = [
            *[chunk_id for chunk_id, _ in lexical_ranking],
            *[chunk_id for chunk_id, _ in dense_ranking],
        ]
        if not result.ranking and self._config.fallback_scan:
            self._full_scan(query, result)
        return result

    def _fuse(
        self,
        lexical: list[tuple[str, float]],
        dense: list[tuple[str, float]],
    ) -> list[RetrievedChunk]:
        """Reciprocal Rank Fusion with a stable chunk-id tie-break."""
        scores: dict[str, float] = {}
        lexical_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(lexical, start=1)}
        dense_rank = {chunk_id: rank for rank, (chunk_id, _) in enumerate(dense, start=1)}
        dense_similarity = dict(dense)
        for chunk_id, rank in lexical_rank.items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (self._config.rrf_k + rank)
        for chunk_id, rank in dense_rank.items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (self._config.rrf_k + rank)
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [
            RetrievedChunk(
                chunk_id=chunk_id,
                rank=rank,
                score=round(score, 8),
                lexical_rank=lexical_rank.get(chunk_id),
                dense_rank=dense_rank.get(chunk_id),
                dense_similarity=(
                    round(dense_similarity[chunk_id], 8)
                    if chunk_id in dense_similarity
                    else None
                ),
            )
            for rank, (chunk_id, score) in enumerate(ordered, start=1)
        ]

    def _full_scan(self, query: RetrievalQuery, result: RetrievalResult) -> None:
        """Deterministic fallback: examine every chunk with all query variants."""
        terms = set(tokenize(query.query_text))
        for alternate in query.alternate_queries:
            terms |= set(tokenize(alternate))
        scanned: list[str] = []
        hits: list[tuple[str, float]] = []
        for chunk_id, chunk in self._chunks.items():
            scanned.append(chunk_id)
            chunk_terms = set(tokenize(chunk.text))
            overlap = len(terms & chunk_terms)
            if overlap:
                hits.append((chunk_id, float(overlap)))
        result.attempts.append(
            SearchAttempt(
                attempt_id=f"att_{query.group_id}_fallback",
                step_id=f"{query.group_id}:fallback",
                retrieval_strategy=RetrievalStrategy.LEXICAL,
                retrieved_block_ids=[chunk_id for chunk_id, _ in hits],
                outcome=(
                    SearchAttemptOutcome.SUCCEEDED if hits else SearchAttemptOutcome.NO_HITS
                ),
            )
        )
        result.fallback_performed = True
        result.scanned_chunk_ids = scanned
        hits.sort(key=lambda item: (-item[1], item[0]))
        result.ranking = [
            RetrievedChunk(chunk_id=chunk_id, rank=rank, score=score)
            for rank, (chunk_id, score) in enumerate(hits[: self._config.final_top_k], start=1)
        ]
        if not hits:
            result.status = "no_hits_full_scan"
            result.warnings.append("full-source scan found no matching chunk")


class RetrievalQueryBuilder:
    """Deterministic per-group retrieval queries (RU/EN term dictionaries)."""

    def __init__(self, *, language: str = "unknown", profile_id: str = "default") -> None:
        self._language = language
        self._profile_id = profile_id

    def build(
        self,
        *,
        document: ParsedDocument,
        group: ExtractionGroup,
        mode: RetrievalMode,
        query_builder_version: str = "query-builder-v1",
    ) -> RetrievalQuery:
        """Build the query for one group of one document."""
        field_paths = list(field_keys_of(group))
        terms = list(QUERY_TERMS[group])
        text = " ".join([group.value.replace("_", " "), *field_paths, *terms])
        payload = "|".join([document.document_id, document.source_sha256, group.value, text])
        query_id = "qry_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        alternate = [
            self._match_phrase_alternate(document, term) for term in terms[:4]
        ]
        return RetrievalQuery(
            query_id=query_id,
            document_id=document.document_id,
            source_sha256=document.source_sha256,
            group_id=group.value,
            field_paths=field_paths,
            query_text=text,
            alternate_queries=[item for item in alternate if item],
            query_terms=terms[:32],
            language=self._language,
            retrieval_profile_id=self._profile_id,
            query_builder_version=query_builder_version,
            retrieval_mode=mode,
        )

    def _match_phrase_alternate(self, document: ParsedDocument, term: str) -> str | None:
        lowered = term.lower()
        for element in document.elements:
            if lowered in element.text.lower():
                start = element.text.lower().find(lowered)
                return element.text[max(0, start - 40) : start + len(term) + 40].strip()
        return None


class RetrievalModeSelector:
    """Deterministic FULL_CONTEXT / HYBRID / RAG decision."""

    def __init__(self, *, counter: TokenCounterPort, message_limit: int = MESSAGE_CONTENT_LIMIT) -> None:
        self._counter = counter
        self._message_limit = message_limit

    def select(
        self,
        *,
        prompt: str,
        full_context_text: str,
        canonical_text: str,
        input_budget_tokens: int,
        has_global_context: bool = False,
        override: str | None = None,
    ) -> RetrievalMode:
        """Apply the documented order: FULL_CONTEXT, then HYBRID, then RAG."""
        if override == RetrievalMode.RAG.value:
            return RetrievalMode.RAG
        if override == RetrievalMode.HYBRID.value:
            return RetrievalMode.HYBRID
        if override == RetrievalMode.FULL_CONTEXT.value:
            return RetrievalMode.FULL_CONTEXT
        if (
            len(prompt) + len(full_context_text) <= self._message_limit
            and self._counter.count(prompt) + self._counter.count(canonical_text)
            <= input_budget_tokens
        ):
            return RetrievalMode.FULL_CONTEXT
        if has_global_context:
            return RetrievalMode.HYBRID
        return RetrievalMode.RAG


class RetrievalContextBuilder:
    """Assemble a group context from ranked chunks without truncating anything."""

    def __init__(
        self,
        *,
        counter: TokenCounterPort,
        message_limit: int = MESSAGE_CONTENT_LIMIT,
    ) -> None:
        self._counter = counter
        self._message_limit = message_limit

    def build(
        self,
        *,
        query: RetrievalQuery,
        result: RetrievalResult,
        chunks: dict[str, DocumentChunk],
        prompt: str,
        mode: RetrievalMode,
        global_context: str | None = None,
    ) -> tuple[str, RetrievalContextRecord]:
        """Return the context text and its manifest record.

        Chunks that do not fit are excluded as whole units and recorded; nothing is cut.
        """
        header = global_context.strip() + "\n\n" if global_context else ""
        parts: list[str] = []
        selected: list[str] = []
        excluded: list[str] = []
        reasons: dict[str, str] = {}
        for entry in result.ranking:
            chunk = chunks.get(entry.chunk_id)
            if chunk is None:  # pragma: no cover - defensive
                continue
            block = f"{_CHUNK_OPEN.format(chunk_id=chunk.chunk_id)}\n{chunk.text}\n{_CHUNK_CLOSE}"
            candidate = header + "\n\n".join([*parts, block])
            if len(prompt) + len(candidate) > self._message_limit:
                excluded.append(chunk.chunk_id)
                reasons[chunk.chunk_id] = "does not fit the Message.content limit"
                continue
            parts.append(block)
            selected.append(chunk.chunk_id)
        context_text = header + "\n\n".join(parts)
        record = RetrievalContextRecord(
            group_id=query.group_id,
            retrieval_mode=mode,
            query_id=query.query_id,
            retrieval_profile_id=query.retrieval_profile_id,
            selected_chunk_ids=selected,
            excluded_chunk_ids=excluded,
            exclusion_reasons=reasons,
            ranking=result.ranking,
            context_chars=len(context_text),
            context_tokens_estimate=self._counter.count(context_text),
            message_guard_passed=len(prompt) + len(context_text) <= self._message_limit,
        )
        return context_text, record


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left)) or 1.0
    right_norm = math.sqrt(sum(value * value for value in right)) or 1.0
    return numerator / (left_norm * right_norm)


def build_coverage_records(
    *,
    document: ParsedDocument,
    run_id: str,
    mode: RetrievalMode,
    query: RetrievalQuery,
    result: RetrievalResult,
    quality_status: QualityStatus,
    total_chunks: int,
) -> list[FieldCoverageRecord]:
    """Technical coverage for all 27 fields using the existing v2.1 record model."""
    coverage_mode = (
        CoverageRetrievalMode(mode.value)
        if mode in {
            RetrievalMode.FULL_CONTEXT,
            RetrievalMode.HYBRID,
            RetrievalMode.RAG,
        }
        else CoverageRetrievalMode.RAG
    )
    # A full-context run performs no search: the v2.1 coverage record forbids a strategy there.
    strategy = (
        None
        if coverage_mode is CoverageRetrievalMode.FULL_CONTEXT
        else (
            RetrievalStrategy.DENSE_LEXICAL
            if result.status == "hybrid"
            else RetrievalStrategy.LEXICAL
        )
    )
    scanned = len(set(result.scanned_chunk_ids))
    complete = result.fallback_performed and scanned >= total_chunks and total_chunks > 0
    status = (
        CoverageStatus.COMPLETE
        if complete
        else (CoverageStatus.PARTIAL if scanned else CoverageStatus.FAILED)
    )
    pages = sorted(
        {chunk_page for chunk_page in _pages_of_result(document) if chunk_page is not None}
    )
    page_range = [PageRange(start_page=pages[0], end_page=pages[-1])] if pages else []
    records: list[FieldCoverageRecord] = []
    for field_path in _all_field_paths():
        records.append(
            FieldCoverageRecord(
                field_path=field_path,
                retrieval_mode=coverage_mode,
                retrieval_strategy=strategy,
                coverage_status=status,
                search_plan_id=run_id,
                retrieved_block_ids=result.chunk_ids[:100],
                search_attempt_ids=[attempt.attempt_id for attempt in result.attempts],
                full_text_fallback_performed=result.fallback_performed,
                required_checks_completed=complete,
                not_found_eligible=complete,
                searched_page_ranges=page_range,
                attempts=list(result.attempts),
                notes=(
                    [f"scanned {scanned}/{total_chunks} chunks"]
                    if total_chunks
                    else ["document has no chunks"]
                ),
            )
        )
    return records


def _pages_of_result(document: ParsedDocument) -> list[int | None]:
    return [element.page_number for element in document.elements]


def _all_field_paths() -> tuple[str, ...]:
    from research_platform.domain.cards.models import CARD_FIELD_PATHS

    return CARD_FIELD_PATHS
