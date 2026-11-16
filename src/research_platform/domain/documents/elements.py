"""Canonical document elements: pages, blocks, sections, tables and figures.

These models are the platform's own representation of a parsed document.  Nothing here
mirrors a parser's internal object graph: adapters map *their* classes into these types, so
swapping Docling for another parser cannot leak into contracts or into stored artefacts.

Position rules that the whole platform depends on:

* ``page_number`` is the **physical** page of the source, 1-based, and is ``None`` only for
  a non-paginated text source.  A printed journal page number never replaces it.
* ``reading_order`` is a deterministic position inside the document, not a claim about the
  parser's internal traversal.
* ``char_start``/``char_end`` address the **canonical text** of this document version
  (0-based, end-exclusive, Unicode code points).  They never address raw PDF bytes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    ShortStr,
)
from research_platform.domain.documents.provenance import BBox

__all__ = [
    "CANONICALIZATION_VERSION",
    "DocumentElement",
    "ElementType",
    "FigureRecord",
    "PageRecord",
    "PageStatus",
    "ParsedSection",
    "ProvenanceSource",
    "TableRecord",
]

CANONICALIZATION_VERSION = "1.0"
"""Version of the canonical-text construction rules.

A change in normalisation (whitespace, page separators, table rendering) must bump this
version, because stored ``char_start``/``char_end`` ranges are only valid for the version
they were computed against.
"""

CharacterOffset = Annotated[int, Field(ge=0)]


class ElementType(StrEnum):
    """Kinds of blocks the platform keeps."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    TABLE_CAPTION = "table_caption"
    FIGURE = "figure"
    FIGURE_CAPTION = "figure_caption"
    FOOTNOTE = "footnote"
    HEADER = "header"
    FOOTER = "footer"
    FORMULA = "formula"
    UNKNOWN = "unknown"


class ProvenanceSource(StrEnum):
    """Which mechanism produced an element."""

    DOCLING = "docling"
    PYMUPDF_FALLBACK = "pymupdf_fallback"
    TXT_DECODER = "txt_decoder"


class PageStatus(StrEnum):
    """Extraction status of one physical page."""

    PARSED = "parsed"
    EMPTY = "empty"
    NO_TEXT_LAYER = "no_text_layer"
    PARTIAL = "partial"
    FAILED = "failed"


class DocumentElement(ContractModel):
    """One addressable block of text or content."""

    block_id: Identifier
    element_type: ElementType
    text: str = ""
    page_number: int | None = Field(default=None, ge=1)
    section_id: ShortStr | None = None
    reading_order: Annotated[int, Field(ge=0)]
    bbox: BBox | None = None
    char_start: CharacterOffset | None = None
    char_end: CharacterOffset | None = None
    source: ProvenanceSource
    level: int | None = Field(default=None, ge=1, le=6)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: Annotated[dict[ShortStr, ShortStr], Field(default_factory=dict, max_length=16)] = (
        Field(default_factory=dict)
    )

    @model_validator(mode="after")
    def _character_range_is_consistent(self) -> DocumentElement:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start and char_end must be declared together")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end < self.char_start
        ):
            raise ValueError("char_end must not precede char_start")
        return self

    @model_validator(mode="after")
    def _heading_carries_a_level(self) -> DocumentElement:
        if self.element_type is ElementType.HEADING and self.level is None:
            raise ValueError("a heading element must declare its level")
        return self


class ParsedSection(ContractModel):
    """A section of the document as the parser reported it."""

    section_id: ShortStr
    title: NonEmptyStr
    level: Annotated[int, Field(ge=1, le=6)]
    parent_section_id: ShortStr | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _page_range_is_ordered(self) -> ParsedSection:
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_end < self.page_start
        ):
            raise ValueError("page_end must not precede page_start")
        return self


class PageRecord(ContractModel):
    """One physical page of a paginated source."""

    page_number: Annotated[int, Field(ge=1)]
    page_width: float | None = Field(default=None, gt=0)
    page_height: float | None = Field(default=None, gt=0)
    rotation: Annotated[int, Field(ge=0, le=359)] = 0
    status: PageStatus
    text: str = ""
    char_count: Annotated[int, Field(ge=0)] = 0
    block_ids: Annotated[list[Identifier], Field(default_factory=list, max_length=5000)] = Field(
        default_factory=list
    )
    has_native_text: bool | None = None
    has_images: bool | None = None
    image_area_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    parser_page_reference: ShortStr | None = None
    metadata: Annotated[dict[ShortStr, ShortStr], Field(default_factory=dict, max_length=16)] = (
        Field(default_factory=dict)
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_char_count(cls, data: object) -> object:
        """``char_count`` is a measurement of ``text``, never an independent claim."""
        if isinstance(data, dict):
            text = data.get("text") or ""
            return {**data, "char_count": len(text)}
        return data


class TableRecord(ContractModel):
    """A structured table, kept with its physical fragment and a deterministic text view."""

    table_id: Identifier
    page_number: int | None = Field(default=None, ge=1)
    section_id: ShortStr | None = None
    reading_order: Annotated[int, Field(ge=0)]
    bbox: BBox | None = None
    row_count: Annotated[int, Field(ge=0)]
    column_count: Annotated[int, Field(ge=0)]
    cells: Annotated[list[list[str]], Field(default_factory=list, max_length=10_000)] = Field(
        default_factory=list
    )
    has_header_row: bool | None = None
    caption: str | None = None
    text_representation: str = ""
    """Deterministic pipe-separated rendering used for search, never the only form."""
    extraction_status: Annotated[
        str, StringConstraints(pattern=r"^(complete|partial|empty|failed)$")
    ] = "complete"
    source: ProvenanceSource

    @model_validator(mode="after")
    def _shape_matches_cells(self) -> TableRecord:
        if self.cells:
            if len(self.cells) != self.row_count:
                raise ValueError(
                    f"table {self.table_id!r}: {len(self.cells)} rows do not match "
                    f"row_count {self.row_count}"
                )
            widths = {len(row) for row in self.cells}
            if widths != {self.column_count}:
                raise ValueError(
                    f"table {self.table_id!r}: row widths {sorted(widths)} do not match "
                    f"column_count {self.column_count}"
                )
        return self


class FigureRecord(ContractModel):
    """Metadata of a figure or picture; content interpretation is out of scope."""

    figure_id: Identifier
    page_number: int | None = Field(default=None, ge=1)
    section_id: ShortStr | None = None
    reading_order: Annotated[int, Field(ge=0)]
    bbox: BBox | None = None
    caption: str | None = None
    image_artifact_uri: ShortStr | None = None
    source: ProvenanceSource
