"""Structural chunker: ParsedDocument → provenance-preserving retrieval chunks.

Rules (iteration 4):

* boundaries follow the parsed structure (headings, paragraphs, lists, tables, captions,
  formulas); unrelated elements are never merged just to reach a size;
* a long text element is split by sentences with an explicit overlap, keeping the parent
  element and offsets;
* tables are chunked separately: a short table is one chunk, a long table is split by rows and
  every fragment keeps the header row and its table/row/column locators;
* every chunk carries element/table/page/section provenance and a deterministic ``chunk_id``
  derived from the document, source hash, element ids, boundaries, chunker version and config.

The chunker also reports which source elements were represented and which were skipped (with a
reason), so silent omission is impossible.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from research_platform.application.ports.context_budget import TokenCounterPort
from research_platform.domain.documents.elements import (
    DocumentElement,
    ElementType,
    TableRecord,
)
from research_platform.domain.documents.parsed import ParsedDocument
from research_platform.domain.retrieval.models import (
    CHUNKER_VERSION,
    ChunkKind,
    ChunkManifest,
    DocumentChunk,
    ElementRef,
    TableRef,
)

__all__ = ["ChunkingConfig", "ChunkingResult", "StructuralChunker"]

_TEXT_ELEMENT_TYPES = {
    ElementType.PARAGRAPH,
    ElementType.LIST_ITEM,
    ElementType.FOOTNOTE,
    ElementType.UNKNOWN,
}
_CAPTION_TYPES = {ElementType.TABLE_CAPTION, ElementType.FIGURE_CAPTION}
_HEADING_TYPES = {ElementType.HEADING}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+")
_MERGE_SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Chunking knobs; the hash of this object identifies the produced index."""

    target_tokens: int = 600
    max_tokens: int = 800
    overlap_tokens: int = 40
    min_tokens: int = 100

    def as_dict(self) -> dict[str, int]:
        """Stable representation used for the configuration hash."""
        return {
            "target_tokens": self.target_tokens,
            "max_tokens": self.max_tokens,
            "overlap_tokens": self.overlap_tokens,
            "min_tokens": self.min_tokens,
        }

    @property
    def config_hash(self) -> str:
        """Short deterministic hash of the chunking configuration."""
        payload = json.dumps(self.as_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def __post_init__(self) -> None:
        if not (0 < self.min_tokens <= self.target_tokens <= self.max_tokens):
            raise ValueError("chunk sizes must satisfy 0 < min <= target <= max")
        if not (0 <= self.overlap_tokens < self.target_tokens):
            raise ValueError("overlap must be smaller than the target size")


@dataclass(slots=True)
class ChunkingResult:
    """Chunks plus the manifest and the coverage of source elements."""

    chunks: list[DocumentChunk] = field(default_factory=list)
    manifest: ChunkManifest | None = None
    skipped_elements: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class StructuralChunker:
    """Deterministic structural chunker."""

    def __init__(self, *, counter: TokenCounterPort, config: ChunkingConfig | None = None) -> None:
        self._counter = counter
        self._config = config or ChunkingConfig()

    @property
    def config(self) -> ChunkingConfig:
        """Active configuration."""
        return self._config

    def chunk(
        self, document: ParsedDocument, *, created_at: str
    ) -> ChunkingResult:
        """Build the chunk set of one parsed document."""
        result = ChunkingResult()
        blocks = self._text_blocks(document, result)
        for text, elements, kind in blocks:
            for chunk in self._chunks_from_text(
                document, text, elements, kind=kind, result=result
            ):
                result.chunks.append(chunk)
        for table in document.tables:
            for chunk in self._chunks_from_table(document, table, result=result):
                result.chunks.append(chunk)

        result.chunks = self._renumber(result.chunks)
        result.warnings.extend(self._coverage_warnings(document, result))
        result.manifest = self._manifest(document, result, created_at=created_at)
        return result

    # ----------------------------------------------------------------- structure

    def _text_blocks(
        self, document: ParsedDocument, result: ChunkingResult
    ) -> list[tuple[str, list[DocumentElement], ChunkKind]]:
        """Group consecutive text-like elements that belong to the same section."""
        blocks: list[tuple[str, list[DocumentElement], ChunkKind]] = []
        buffer: list[DocumentElement] = []
        buffer_text: list[str] = []
        buffer_section: str | None = None

        def flush() -> None:
            nonlocal buffer, buffer_text, buffer_section
            if buffer:
                blocks.append(
                    (
                        _MERGE_SEPARATOR.join(buffer_text).strip(),
                        list(buffer),
                        ChunkKind.TEXT,
                    )
                )
            buffer, buffer_text, buffer_section = [], [], None

        for element in sorted(document.elements, key=lambda item: item.reading_order):
            kind = element.element_type
            if kind in _HEADING_TYPES:
                flush()
                blocks.append((element.text.strip(), [element], ChunkKind.HEADING))
                continue
            if kind in _CAPTION_TYPES:
                flush()
                blocks.append((element.text.strip(), [element], ChunkKind.CAPTION))
                continue
            if kind is ElementType.FORMULA:
                flush()
                blocks.append((element.text.strip(), [element], ChunkKind.FORMULA))
                continue
            if kind is ElementType.TABLE:
                # The table text lives in TableRecord; the element only marks placement.
                flush()
                continue
            if kind in _TEXT_ELEMENT_TYPES:
                if buffer and element.section_id != buffer_section:
                    flush()
                if not buffer:
                    buffer_section = element.section_id
                buffer.append(element)
                buffer_text.append(element.text.strip())
                continue
            flush()
            result.skipped_elements[element.block_id] = f"unsupported element type {kind.value}"
        flush()
        return [block for block in blocks if block[0]]

    # -------------------------------------------------------------------- chunks

    def _chunks_from_text(
        self,
        document: ParsedDocument,
        text: str,
        elements: list[DocumentElement],
        *,
        kind: ChunkKind,
        result: ChunkingResult,
    ) -> list[DocumentChunk]:
        section_path = self._section_path(document, elements[0].section_id)
        pages = sorted({element.page_number for element in elements if element.page_number})
        if kind is not ChunkKind.TEXT:
            return [
                self._build_chunk(
                    document,
                    text=text,
                    kind=kind,
                    elements=elements,
                    section_path=section_path,
                    pages=pages,
                    sequence=0,
                    overlap=0,
                )
            ]
        budget = self._config.target_tokens * 4
        if len(text) <= self._config.max_tokens * 4:
            return [
                self._build_chunk(
                    document,
                    text=text,
                    kind=kind,
                    elements=elements,
                    section_path=section_path,
                    pages=pages,
                    sequence=0,
                    overlap=0,
                )
            ]
        pieces = self._split_text(text, budget)
        return [
            self._build_chunk(
                document,
                text=piece,
                kind=kind,
                elements=elements,
                section_path=section_path,
                pages=pages,
                sequence=index,
                overlap=self._config.overlap_tokens if index else 0,
            )
            for index, piece in enumerate(pieces)
        ]

    def _split_text(self, text: str, budget: int) -> list[str]:
        sentences = [part for part in _SENTENCE_SPLIT.split(text) if part]
        pieces: list[str] = []
        current: list[str] = []
        size = 0
        for sentence in sentences:
            if size + len(sentence) > budget and current:
                pieces.append(" ".join(current))
                # keep the tail of the previous piece as an explicit overlap window
                overlap_chars = self._config.overlap_tokens * 4
                tail = " ".join(current)[-overlap_chars:] if overlap_chars else ""
                current = [tail, sentence] if tail else [sentence]
                size = sum(len(item) for item in current)
                continue
            current.append(sentence)
            size += len(sentence)
        if current:
            pieces.append(" ".join(current))
        return [piece.strip() for piece in pieces if piece.strip()]

    def _chunks_from_table(
        self, document: ParsedDocument, table: TableRecord, *, result: ChunkingResult
    ) -> list[DocumentChunk]:
        header, rows = self._table_matrix(table)
        if table.extraction_status == "empty" or not rows:
            result.skipped_elements[table.table_id] = "empty table"
            return []
        caption = table.caption or ""
        rows_per_chunk = max(1, self._config.target_tokens // max(1, self._row_tokens(rows)))
        fragments: list[tuple[int, list[list[str]]]] = []
        for start in range(0, len(rows), rows_per_chunk):
            fragments.append((start, rows[start : start + rows_per_chunk]))
        page = table.page_number
        chunks: list[DocumentChunk] = []
        for start, fragment in fragments:
            lines = [" | ".join(header)] if header else []
            lines.extend(" | ".join(row) for row in fragment)
            text = (caption + "\n" if caption and start == 0 else "") + "\n".join(lines)
            refs = [
                TableRef(
                    table_id=table.table_id,
                    cell_id=f"{table.table_id}_r{start + offset}_c{column}",
                    row_index=start + offset,
                    column_index=column,
                    header_values=[value for value in header if value],
                )
                for offset in range(len(fragment))
                for column in range(len(fragment[offset]))
            ]
            chunks.append(
                self._build_chunk(
                    document,
                    text=text,
                    kind=ChunkKind.TABLE,
                    elements=[],
                    section_path=self._section_path(document, table.section_id),
                    pages=[page] if page else [],
                    sequence=0,
                    overlap=0,
                    table_refs=refs,
                    table_id=table.table_id,
                )
            )
        return chunks

    def _table_matrix(self, table: TableRecord) -> tuple[list[str], list[list[str]]]:
        if not table.cells:
            return [], []
        header = table.cells[0] if table.has_header_row else []
        rows = table.cells[1:] if table.has_header_row else list(table.cells)
        return [cell.strip() for cell in header], [
            [cell.strip() for cell in row] for row in rows
        ]

    def _row_tokens(self, rows: list[list[str]]) -> int:
        if not rows:
            return 1
        return max(1, self._counter.count(" | ".join(rows[0])))

    # ------------------------------------------------------------------- builder

    def _build_chunk(
        self,
        document: ParsedDocument,
        *,
        text: str,
        kind: ChunkKind,
        elements: list[DocumentElement],
        section_path: list[str],
        pages: list[int],
        sequence: int,
        overlap: int,
        table_refs: list[TableRef] | None = None,
        table_id: str | None = None,
    ) -> DocumentChunk:
        element_ids = [element.block_id for element in elements] or (
            [table_id] if table_id else []
        )
        chunk_id = self._chunk_id(
            document, text=text, kind=kind, element_ids=element_ids, sequence=sequence
        )
        return DocumentChunk(
            chunk_id=chunk_id,
            document_id=document.document_id,
            source_sha256=document.source_sha256,
            sequence_no=1,
            chunk_kind=kind,
            text=text,
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            language=str(getattr(document.language, "value", document.language)),
            section_path=section_path,
            physical_page_numbers=pages,
            parent_element_ids=[element.block_id for element in elements],
            element_refs=[
                ElementRef(
                    element_id=element.block_id,
                    element_type=element.element_type.value,
                    page_number=element.page_number,
                    char_start=element.char_start,
                    char_end=element.char_end,
                    bbox=element.bbox.model_dump(mode="json") if element.bbox else None,
                )
                for element in elements
            ],
            table_refs=table_refs or [],
            token_count_estimate=self._counter.count(text),
            token_counter_id=self._counter.name,
            token_count_is_exact=self._counter.is_exact,
            overlap_tokens=overlap,
        )

    def _chunk_id(
        self,
        document: ParsedDocument,
        *,
        text: str,
        kind: ChunkKind,
        element_ids: list[str],
        sequence: int,
    ) -> str:
        payload = "|".join(
            [
                document.document_id,
                document.source_sha256,
                CHUNKER_VERSION,
                self._config.config_hash,
                kind.value,
                ",".join(element_ids),
                str(sequence),
                hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            ]
        )
        return "chunk_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _renumber(chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        return [
            chunk.model_copy(update={"sequence_no": index})
            for index, chunk in enumerate(chunks, start=1)
        ]

    def _section_path(self, document: ParsedDocument, section_id: str | None) -> list[str]:
        if not section_id:
            return []
        for section in document.sections:
            if section.section_id == section_id:
                return list(section.path) if hasattr(section, "path") else [section.title]
        return []

    # -------------------------------------------------------------------- report

    def _coverage_warnings(
        self, document: ParsedDocument, result: ChunkingResult
    ) -> list[str]:
        represented = {
            element_id
            for chunk in result.chunks
            for element_id in chunk.parent_element_ids
        }
        missing = [
            element.block_id
            for element in document.elements
            if element.block_id not in represented
            and element.element_type is not ElementType.TABLE
        ]
        warnings: list[str] = []
        if missing:
            warnings.append(
                f"{len(missing)} element(s) were not represented in chunks: "
                + ", ".join(missing[:5])
            )
        tables = {ref.table_id for chunk in result.chunks for ref in chunk.table_refs}
        for table in document.tables:
            if table.table_id not in tables:
                result.skipped_elements.setdefault(table.table_id, "empty table")
        return warnings

    def _manifest(
        self, document: ParsedDocument, result: ChunkingResult, *, created_at: str
    ) -> ChunkManifest:
        text_chunks = sum(
            1
            for chunk in result.chunks
            if chunk.chunk_kind in {ChunkKind.TEXT, ChunkKind.HEADING, ChunkKind.CAPTION}
        )
        table_chunks = sum(
            1
            for chunk in result.chunks
            if chunk.chunk_kind in {ChunkKind.TABLE, ChunkKind.TABLE_CELL}
        )
        return ChunkManifest(
            document_id=document.document_id,
            source_sha256=document.source_sha256,
            chunker_version=CHUNKER_VERSION,
            chunker_config_hash=self._config.config_hash,
            parser_name=document.parser.parser_name,
            parser_version=document.parser.parser_version,
            chunk_count=len(result.chunks),
            text_chunk_count=text_chunks,
            table_chunk_count=table_chunks,
            pages=sorted(
                {page for chunk in result.chunks for page in chunk.physical_page_numbers}
            ),
            token_count_estimate=sum(chunk.token_count_estimate for chunk in result.chunks),
            token_counter_id=self._counter.name,
            token_count_is_exact=self._counter.is_exact,
            warnings=result.warnings[:20],
            created_at=created_at,
        )
