"""Parsed-document contract (version 1.0).

``ParsedDocument`` is the platform's stable, versioned representation of what a parser
extracted from the original source.  It is *not* an Agent 1A card: no extracted field, no
evidence record and no coverage trail is produced here.  It carries text, structure and
provenance only, and it is stored as an immutable artefact.

Large collections may be moved into separate artefacts later, but this contract keeps one
addressable document object so that a reader can reconstruct the whole picture from one
reference.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    Sha256Hex,
    ShortStr,
    UtcDatetime,
)
from research_platform.domain.documents.elements import (
    DocumentElement,
    FigureRecord,
    PageRecord,
    PageStatus,
    ParsedSection,
    TableRecord,
)
from research_platform.domain.documents.identity import DocumentSourceType
from research_platform.domain.runs.artifacts import ArtifactUri

__all__ = [
    "PARSED_DOCUMENT_CONTRACT_VERSION",
    "CanonicalTextInfo",
    "ParsedDocument",
    "ParserProvenance",
    "ParsingStatus",
]

PARSED_DOCUMENT_CONTRACT_VERSION: Final = "1.0"


class ParsingStatus(StrEnum):
    """Outcome of the parsing stage itself."""

    PARSED = "parsed"
    PARSED_WITH_FALLBACK = "parsed_with_fallback"
    """The primary parser failed technically and a reduced fallback produced the result."""
    EMPTY = "empty"
    FAILED = "failed"


class ParserProvenance(ContractModel):
    """Which parser produced the document and under which settings.

    Without these fields a stored parse cannot be reproduced or compared, so they are
    mandatory rather than optional metadata.
    """

    parser_name: ShortStr
    parser_version: ShortStr
    pipeline_version: ShortStr
    parser_options_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{16,64}$")]
    backend_name: ShortStr
    table_extraction_enabled: bool
    ocr_enabled: bool
    model_assets_revision: ShortStr | None = None


class CanonicalTextInfo(ContractModel):
    """Address of the canonical text and the rules used to build it."""

    artifact_uri: ArtifactUri
    canonicalization_version: ShortStr
    char_count: Annotated[int, Field(ge=0)]
    page_separator: Annotated[str, Field(max_length=8)]
    """Separator inserted between physical pages (a form feed, ``"\f"``, in version 1.0).

    It is a control character on purpose: an explicit boundary in the text stream that adds
    no words of its own.  A text source has no pages, so its canonical text never contains
    the separator.
    """


class ParsedDocument(ContractModel):
    """Versioned, parser-independent representation of a parsed source."""

    document_contract_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = (
        PARSED_DOCUMENT_CONTRACT_VERSION
    )
    document_id: Identifier
    run_id: ShortStr
    source_sha256: Sha256Hex
    source_type: DocumentSourceType
    language: Annotated[str, Field(pattern=r"^(ru|en|unknown)$")]
    parser: ParserProvenance
    parsing_status: ParsingStatus
    page_count: int | None = Field(default=None, ge=1)
    pages: Annotated[list[PageRecord], Field(default_factory=list, max_length=2000)] = Field(
        default_factory=list
    )
    sections: Annotated[list[ParsedSection], Field(default_factory=list, max_length=500)] = Field(
        default_factory=list
    )
    elements: Annotated[list[DocumentElement], Field(default_factory=list, max_length=50_000)] = (
        Field(default_factory=list)
    )
    tables: Annotated[list[TableRecord], Field(default_factory=list, max_length=1000)] = Field(
        default_factory=list
    )
    figures: Annotated[list[FigureRecord], Field(default_factory=list, max_length=2000)] = Field(
        default_factory=list
    )
    canonical_text: CanonicalTextInfo
    source_artifact_uri: ArtifactUri
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _pagination_matches_the_source_type(self) -> ParsedDocument:
        if self.source_type is DocumentSourceType.TXT:
            if self.page_count is not None or self.pages:
                raise ValueError(
                    "a text source has no physical pages: page_count must be null and "
                    "pages must be empty (no invented page 1)"
                )
            if any(element.page_number is not None for element in self.elements):
                raise ValueError("a text source must not attach page_number to its elements")
            return self
        if self.page_count is None:
            raise ValueError("a paginated source must declare its physical page_count")
        return self

    @model_validator(mode="after")
    def _pages_are_the_full_physical_sequence(self) -> ParsedDocument:
        if not self.pages:
            return self
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(
                "pages must be the full physical sequence 1..N without gaps or reordering; "
                f"got {numbers[:10]}{'…' if len(numbers) > 10 else ''}"
            )
        return self

    @model_validator(mode="after")
    def _elements_reference_known_pages_and_sections(self) -> ParsedDocument:
        known_pages = {page.page_number for page in self.pages}
        known_sections = {section.section_id for section in self.sections}
        block_ids: set[str] = set()
        for element in self.elements:
            if element.block_id in block_ids:
                raise ValueError(f"duplicate block_id {element.block_id!r}")
            block_ids.add(element.block_id)
            if element.page_number is not None and element.page_number not in known_pages:
                raise ValueError(
                    f"element {element.block_id!r} references page {element.page_number}, "
                    "which is not part of the parsed page sequence"
                )
            if element.section_id is not None and element.section_id not in known_sections:
                raise ValueError(
                    f"element {element.block_id!r} references unknown section "
                    f"{element.section_id!r}"
                )
        return self

    @model_validator(mode="after")
    def _page_block_lists_match_the_elements(self) -> ParsedDocument:
        by_page: dict[int, set[str]] = {
            page.page_number: set(page.block_ids) for page in self.pages
        }
        element_ids = {element.block_id for element in self.elements}
        for page_number, block_ids in by_page.items():
            missing = block_ids - element_ids
            if missing:
                raise ValueError(
                    f"page {page_number} references blocks that do not exist: {sorted(missing)[:5]}"
                )
        return self

    @model_validator(mode="after")
    def _tables_and_figures_point_at_real_pages(self) -> ParsedDocument:
        if not self.pages:
            return self
        known_pages = {page.page_number for page in self.pages}
        for record in (*self.tables, *self.figures):
            identifier = record.table_id if isinstance(record, TableRecord) else record.figure_id
            if record.page_number is not None and record.page_number not in known_pages:
                raise ValueError(
                    f"{identifier!r} references page {record.page_number}, which is not part "
                    "of the parsed page sequence"
                )
        return self

    @model_validator(mode="after")
    def _empty_text_is_visible(self) -> ParsedDocument:
        if self.parsing_status is ParsingStatus.EMPTY:
            total = sum(element.char_end or 0 for element in self.elements)
            if total > 0:
                raise ValueError(
                    "parsing_status 'empty' contradicts extracted characters; an empty parse "
                    "cannot report text"
                )
        return self

    @model_validator(mode="after")
    def _pages_without_text_are_declared(self) -> ParsedDocument:
        for page in self.pages:
            if not page.text and page.status is PageStatus.PARSED:
                raise ValueError(
                    f"page {page.page_number} is marked 'parsed' but holds no text; an empty "
                    "page must be declared as 'empty', 'no_text_layer' or 'partial'"
                )
        return self
