"""Internal retrieval contracts of iteration 4 (chunks, queries, evidence, manifests).

These models are **internal**: they are not part of the published Agent 1A РІвЂ вЂ™ 1B package and
cannot alter frozen schemas.  They exist to make structural chunking, retrieval, evidence
resolution and coverage inspectable and versioned.

Key principle: a chunk carries provenance back to the ``ParsedDocument`` (element ids, pages,
sections, table/cell locators).  A model may *propose* a quote, but only Python resolves it
into an evidence object with page/span/bbox taken from real parser provenance.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    ShortStr,
    Sha256Hex,
    UtcDatetime,
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
    "RetrievalQuery",
    "RetrievalMode",
    "RetrievedChunk",
    "TableRef",
]

CHUNKER_VERSION: Final = "structural-v1"
RETRIEVAL_MANIFEST_VERSION: Final = "1.0"
EVIDENCE_RESOLVER_VERSION: Final = "evidence-resolver-v1"
COVERAGE_POLICY_VERSION: Final = "1.0"


class RetrievalMode(StrEnum):
    """How the context for one group is assembled."""

    FULL_CONTEXT = "full_context"
    HYBRID = "hybrid"
    RAG = "rag"
    LEXICAL_ONLY = "lexical_only"
    """Diagnostic marker: dense retrieval unavailable, lexical ranking only."""
    NOT_APPLICABLE = "not_applicable"


class ChunkKind(StrEnum):
    """Structural nature of a chunk."""

    TEXT = "text"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    CAPTION = "caption"
    FORMULA = "formula"
    HEADING = "heading"


class MatchStatus(StrEnum):
    """Evidence resolution outcome; only the first two confirm a locator."""

    EXACT = "exact"
    NORMALIZED_EXACT = "normalized_exact"
    FUZZY_CANDIDATE = "fuzzy_candidate"
    UNRESOLVED = "unresolved"

    @property
    def is_confirmed(self) -> bool:
        """Whether the quote was located verbatim in the source."""
        return self in {MatchStatus.EXACT, MatchStatus.NORMALIZED_EXACT}


class ElementRef(ContractModel):
    """Provenance of one source element inside a chunk."""

    element_id: ShortStr
    element_type: ShortStr
    page_number: Annotated[int | None, Field(default=None, ge=1)] = None
    char_start: Annotated[int | None, Field(default=None, ge=0)] = None
    char_end: Annotated[int | None, Field(default=None, ge=0)] = None
    bbox: dict[str, float] | None = None


class TableRef(ContractModel):
    """Provenance of a table fragment (or one of its cells)."""

    table_id: ShortStr
    cell_id: ShortStr | None = None
    row_index: Annotated[int | None, Field(default=None, ge=0)] = None
    column_index: Annotated[int | None, Field(default=None, ge=0)] = None
    header_values: Annotated[list[NonEmptyStr], Field(max_length=32)] = Field(default_factory=list)
    row_label: ShortStr | None = None


class DocumentChunk(ContractModel):
    """One structural retrieval unit with full provenance."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    chunk_id: Annotated[str, StringConstraints(pattern=r"^chunk_[a-f0-9]{16}$")]
    document_id: Identifier
    source_sha256: Sha256Hex
    sequence_no: Annotated[int, Field(ge=1)]
    chunk_kind: ChunkKind
    text: Annotated[str, StringConstraints(min_length=1, max_length=20000)]
    text_hash: Sha256Hex
    language: ShortStr = "unknown"
    section_path: Annotated[list[NonEmptyStr], Field(max_length=16)] = Field(default_factory=list)
    physical_page_numbers: Annotated[list[int], Field(max_length=64)] = Field(default_factory=list)
    printed_page_labels: Annotated[list[NonEmptyStr], Field(max_length=64)] = Field(default_factory=list)
    parent_element_ids: Annotated[list[NonEmptyStr], Field(max_length=64)] = Field(default_factory=list)
    element_refs: Annotated[list[ElementRef], Field(max_length=64)] = Field(default_factory=list)
    table_refs: Annotated[list[TableRef], Field(max_length=512)] = Field(default_factory=list)
    token_count_estimate: Annotated[int, Field(ge=0)] = 0
    token_counter_id: ShortStr = "heuristic-chars-per-4"
    token_count_is_exact: bool = False
    chunker_version: ShortStr = CHUNKER_VERSION
    overlap_tokens: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def _table_chunks_have_table_refs(self) -> DocumentChunk:
        if self.chunk_kind in {ChunkKind.TABLE, ChunkKind.TABLE_CELL} and not self.table_refs:
            raise ValueError("a table chunk must carry at least one table reference")
        if self.chunk_kind is ChunkKind.TEXT and self.table_refs:
            raise ValueError("a plain text chunk must not carry table references")
        return self

    @model_validator(mode="after")
    def _pages_are_physical(self) -> DocumentChunk:
        if any(number < 1 for number in self.physical_page_numbers):
            raise ValueError("physical page numbers are 1-based")
        return self


class ChunkManifest(ContractModel):
    """Versioned index of one chunk set."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    document_id: Identifier
    source_sha256: Sha256Hex
    chunker_version: ShortStr = CHUNKER_VERSION
    chunker_config_hash: ShortStr
    parser_name: ShortStr
    parser_version: ShortStr
    chunk_count: Annotated[int, Field(ge=0)]
    text_chunk_count: Annotated[int, Field(ge=0)] = 0
    table_chunk_count: Annotated[int, Field(ge=0)] = 0
    pages: Annotated[list[int], Field(max_length=2048)] = Field(default_factory=list)
    token_count_estimate: Annotated[int, Field(ge=0)] = 0
    token_counter_id: ShortStr = "heuristic-chars-per-4"
    token_count_is_exact: bool = False
    warnings: Annotated[list[NonEmptyStr], Field(max_length=100)] = Field(default_factory=list)
    chunks_artifact_uri: ShortStr | None = None
    created_at: UtcDatetime


class RetrievalQuery(ContractModel):
    """Deterministic per-group retrieval request."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    query_id: ShortStr
    document_id: Identifier
    source_sha256: Sha256Hex
    group_id: ShortStr
    field_paths: Annotated[list[NonEmptyStr], Field(max_length=32)] = Field(default_factory=list)
    query_text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    alternate_queries: Annotated[list[NonEmptyStr], Field(max_length=8)] = Field(default_factory=list)
    query_terms: Annotated[list[NonEmptyStr], Field(max_length=64)] = Field(default_factory=list)
    language: ShortStr = "unknown"
    retrieval_profile_id: ShortStr = "default"
    query_builder_version: ShortStr = "query-builder-v1"
    retrieval_mode: RetrievalMode = RetrievalMode.RAG


class RetrievedChunk(ContractModel):
    """One ranked chunk in a retrieval result."""

    chunk_id: NonEmptyStr
    rank: Annotated[int, Field(ge=1)]
    score: Annotated[float, Field(ge=0.0)]
    lexical_rank: Annotated[int | None, Field(default=None, ge=1)] = None
    dense_rank: Annotated[int | None, Field(default=None, ge=1)] = None
    dense_similarity: Annotated[float | None, Field(default=None)] = None


class RetrievalContextRecord(ContractModel):
    """Manifest of one group context; never contains the full text."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    group_id: ShortStr
    retrieval_mode: RetrievalMode
    query_id: ShortStr
    retrieval_profile_id: ShortStr = "default"
    index_manifest_uri: ShortStr | None = None
    selected_chunk_ids: Annotated[list[NonEmptyStr], Field(max_length=200)] = Field(default_factory=list)
    excluded_chunk_ids: Annotated[list[NonEmptyStr], Field(max_length=200)] = Field(default_factory=list)
    exclusion_reasons: Annotated[dict[ShortStr, ShortStr], Field()] = Field(default_factory=dict)
    ranking: Annotated[list[RetrievedChunk], Field(max_length=200)] = Field(default_factory=list)
    context_chars: Annotated[int, Field(ge=0)] = 0
    context_tokens_estimate: Annotated[int, Field(ge=0)] = 0
    message_guard_passed: bool = False
    context_artifact_uri: ShortStr | None = None
    context_sha256: Sha256Hex | None = None


class EvidenceProposal(ContractModel):
    """What the model may propose: a quote inside a chunk of *this* request."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    candidate_id: Annotated[str, StringConstraints(pattern=r"^cand_[a-f0-9]{16}$")]
    field_path: NonEmptyStr
    chunk_id: Annotated[str, StringConstraints(pattern=r"^chunk_[a-f0-9]{16}$")]
    quote_candidate: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    surrounding_context: Annotated[str | None, StringConstraints(max_length=2000)] = None


class EvidenceTableRef(ContractModel):
    """Canonical table locator of one resolved evidence object."""

    table_id: ShortStr
    cell_id: ShortStr | None = None
    row_index: Annotated[int | None, Field(default=None, ge=0)] = None
    column_index: Annotated[int | None, Field(default=None, ge=0)] = None
    header_values: Annotated[list[NonEmptyStr], Field(max_length=32)] = Field(default_factory=list)


class EvidenceObject(ContractModel):
    """Canonical evidence produced by Python, not by the model."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    evidence_id: Annotated[str, StringConstraints(pattern=r"^ev_[a-f0-9]{16}$")]
    document_id: Identifier
    source_sha256: Sha256Hex
    chunk_id: NonEmptyStr
    field_path: NonEmptyStr
    page_number: Annotated[int | None, Field(default=None, ge=1)] = None
    printed_page_label: ShortStr | None = None
    section_path: Annotated[list[NonEmptyStr], Field(max_length=16)] = Field(default_factory=list)
    element_id: ShortStr
    element_type: ShortStr
    quote_original: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    source_span: dict[str, int] | None = None
    bbox: dict[str, float] | None = None
    table_ref: EvidenceTableRef | None = None
    language: ShortStr = "unknown"
    extraction_method: ShortStr = "text_layer"
    match_status: MatchStatus
    resolver_version: ShortStr = EVIDENCE_RESOLVER_VERSION

    @model_validator(mode="after")
    def _only_confirmed_matches_may_be_evidence(self) -> EvidenceObject:
        if self.match_status is MatchStatus.FUZZY_CANDIDATE:
            raise ValueError(
                "a fuzzy candidate cannot become a canonical evidence object; keep it "
                "unresolved"
            )
        return self


class EvidenceLedger(ContractModel):
    """Canonical evidence of one run plus resolution diagnostics."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    document_id: Identifier
    source_sha256: Sha256Hex
    run_id: ShortStr
    resolver_version: ShortStr = EVIDENCE_RESOLVER_VERSION
    evidence: Annotated[list[EvidenceObject], Field(max_length=400)] = Field(default_factory=list)
    unresolved: Annotated[list[dict[str, str]], Field(max_length=400)] = Field(default_factory=list)
    warnings: Annotated[list[NonEmptyStr], Field(max_length=100)] = Field(default_factory=list)
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> EvidenceLedger:
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate evidence_id in the ledger")
        return self


class RetrievalManifest(ContractModel):
    """Everything needed to understand and resume one retrieval run."""

    manifest_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = RETRIEVAL_MANIFEST_VERSION
    run_id: ShortStr
    document_id: Identifier
    source_sha256: Sha256Hex
    retrieval_mode: RetrievalMode
    chunker_version: ShortStr
    chunker_config_hash: ShortStr
    parser_name: ShortStr
    parser_version: ShortStr
    embedding_provider: ShortStr | None = None
    embedding_model_id: ShortStr | None = None
    embedding_execution_mode: ShortStr = "not_configured"
    lexical_analyzer_version: ShortStr = "bm25-unicode-v1"
    retrieval_profile_id: ShortStr = "default"
    retrieval_config_hash: ShortStr
    chunks_uri: ShortStr | None = None
    chunk_manifest_uri: ShortStr | None = None
    lexical_index_uri: ShortStr | None = None
    embedding_vectors_uri: ShortStr | None = None
    embedding_manifest_uri: ShortStr | None = None
    group_context_uris: Annotated[dict[ShortStr, ShortStr], Field()] = Field(default_factory=dict)
    group_evidence_uris: Annotated[dict[ShortStr, ShortStr], Field()] = Field(default_factory=dict)
    evidence_ledger_uri: ShortStr | None = None
    coverage_uri: ShortStr | None = None
    warnings: Annotated[list[NonEmptyStr], Field(max_length=100)] = Field(default_factory=list)
    created_at: UtcDatetime
