"""Docling adapter: the primary structured PDF parser.

The adapter is the only place that imports Docling.  It converts a PDF (given as bytes) into
the platform's own :class:`ParsedPayload`, and it never invents data:

* every page of the source appears in the result, in physical order, because pages are
  created from the document's own page table rather than from the elements found on them;
* an element gets a ``page_number`` only when Docling reports one; an element without
  provenance keeps ``page_number = None`` instead of being attached to a guessed page;
* OCR is disabled in the options (no engine is approved), so a scanned page simply yields
  no text — the Quality Gate then routes it to ``requires_ocr``;
* the parser never raises on "no text": it returns an empty payload with ``status=EMPTY``
  and leaves the verdict to the gate.

Fallback is a separate adapter (PyMuPDF); this class does not degrade itself silently.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import tempfile
from pathlib import Path
from typing import Any

from research_platform.application.ports.document_pipeline import (
    DocumentParserPort,
    ParsedPayload,
    ParseRequest,
)
from research_platform.domain.documents.elements import (
    DocumentElement,
    ElementType,
    FigureRecord,
    PageRecord,
    PageStatus,
    ParsedSection,
    ProvenanceSource,
    TableRecord,
)
from research_platform.domain.documents.parsed import ParserProvenance, ParsingStatus
from research_platform.platform.document_parsing.docling.converters import (
    element_type_for,
    normalise_bbox,
    raw_label,
    table_cells_from_frame,
    table_text_from_cells,
)
from research_platform.platform.document_parsing.docling.options import (
    DOCLING_PIPELINE_VERSION,
    DoclingParserOptions,
)

__all__ = ["DoclingPdfParser"]

TableCells = list[list[str]]


class DoclingPdfParser(DocumentParserPort):
    """Structured PDF parser built on Docling's standard pipeline."""

    parser_name = "docling"

    def __init__(
        self,
        *,
        options: DoclingParserOptions | None = None,
        artifacts_path: Path | None = None,
    ) -> None:
        self._options = options or DoclingParserOptions()
        if self._options.do_ocr:
            raise ValueError(
                "OCR must stay disabled until the owner approves an engine (OI-008); "
                "DoclingPdfParser refuses to run with do_ocr=True"
            )
        self._artifacts_path = artifacts_path
        self._converter: Any = None

    # ------------------------------------------------------------------ identity

    @property
    def parser_version(self) -> str:
        """Version of the installed Docling package."""
        try:
            return metadata.version("docling")
        except metadata.PackageNotFoundError:  # pragma: no cover - parsing group missing
            return "unavailable"

    @property
    def options_hash(self) -> str:
        """Hash of the effective pipeline options."""
        return self._options.options_hash

    def provenance(
        self,
        *,
        table_extraction_enabled: bool | None = None,
        ocr_enabled: bool = False,
        model_assets_revision: str | None = None,
    ) -> ParserProvenance:
        """Provenance block recorded in the parsed document."""
        return ParserProvenance(
            parser_name=self.parser_name,
            parser_version=self.parser_version,
            pipeline_version=DOCLING_PIPELINE_VERSION,
            parser_options_hash=self.options_hash,
            backend_name=self._options.backend,
            table_extraction_enabled=(
                self._options.do_table_structure
                if table_extraction_enabled is None
                else table_extraction_enabled
            ),
            ocr_enabled=ocr_enabled,
            model_assets_revision=model_assets_revision,
        )

    # -------------------------------------------------------------------- parsing

    def _build_converter(self) -> Any:
        if self._converter is not None:
            return self._converter
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = self._options.to_pipeline_options()
        if self._artifacts_path is not None:
            options.artifacts_path = str(self._artifacts_path)
        self._converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
        return self._converter

    def parse_pdf(self, request: ParseRequest) -> ParsedPayload:
        """Parse PDF bytes into a platform payload.

        Raises:
            ValueError: the source is empty or Docling cannot convert it.
        """
        if not request.data:
            raise ValueError("pdf source is empty")
        converter = self._build_converter()
        with tempfile.TemporaryDirectory(prefix="docling-src-") as workdir:
            source = Path(workdir) / "source.pdf"
            source.write_bytes(request.data)
            try:
                result = converter.convert(source)
            except Exception as exc:
                raise ValueError(f"docling could not convert the PDF: {exc}") from exc
        document = getattr(result, "document", None)
        if document is None:
            raise ValueError("docling returned no document")
        return self._payload(document, request)

    def parse_txt(self, request: ParseRequest) -> ParsedPayload:  # pragma: no cover
        """Not implemented here: text parsing has its own adapter."""
        raise NotImplementedError(
            "DoclingPdfParser parses PDF only; use TxtBlockParser for text sources"
        )

    # --------------------------------------------------------------------- mapping

    def _payload(self, document: Any, request: ParseRequest) -> ParsedPayload:
        page_sizes = self._page_sizes(document)
        page_count = len(page_sizes) or int(getattr(document, "num_pages", lambda: 0)() or 0)
        warnings: list[str] = []

        sections, elements, tables, figures = self._extract_items(document, request)

        # Page records must cover every physical page, including pages without any element.
        by_page: dict[int, list[DocumentElement]] = {}
        for element in elements:
            if element.page_number is not None:
                by_page.setdefault(element.page_number, []).append(element)
        pages: list[PageRecord] = []
        for page_number in range(1, page_count + 1):
            width, height = page_sizes.get(page_number, (None, None))
            page_elements = by_page.get(page_number, [])
            text = "\n\n".join(element.text for element in page_elements if element.text)
            if not text:
                status = PageStatus.EMPTY
            elif len(page_elements) == 0:
                status = PageStatus.PARTIAL
            else:
                status = PageStatus.PARSED
            pages.append(
                PageRecord(
                    page_number=page_number,
                    page_width=width,
                    page_height=height,
                    status=status,
                    text=text,
                    char_count=len(text),
                    block_ids=[element.block_id for element in page_elements],
                )
            )

        total_text = sum(len(element.text) for element in elements)
        if not elements:
            status = ParsingStatus.EMPTY
            warnings.append("docling extracted no elements from the document")
        else:
            status = ParsingStatus.PARSED
        if total_text == 0:
            warnings.append("docling extracted no characters from the document")

        return ParsedPayload(
            parser=self.provenance(),
            status=status,
            pages=tuple(pages),
            sections=tuple(sections),
            elements=tuple(elements),
            tables=tuple(tables),
            figures=tuple(figures),
            warnings=tuple(warnings),
            page_count=page_count or None,
        )

    @staticmethod
    def _page_sizes(document: Any) -> dict[int, tuple[float | None, float | None]]:
        sizes: dict[int, tuple[float | None, float | None]] = {}
        for number, page in getattr(document, "pages", {}).items():
            size = getattr(page, "size", None)
            width = getattr(size, "width", None) if size is not None else None
            height = getattr(size, "height", None) if size is not None else None
            sizes[int(number)] = (
                float(width) if width is not None else None,
                float(height) if height is not None else None,
            )
        return sizes

    def _extract_items(
        self, document: Any, request: ParseRequest
    ) -> tuple[list[ParsedSection], list[DocumentElement], list[TableRecord], list[FigureRecord]]:
        sections: list[ParsedSection] = []
        elements: list[DocumentElement] = []
        tables: list[TableRecord] = []
        figures: list[FigureRecord] = []
        section_stack: list[tuple[int, str]] = []
        page_sizes = self._page_sizes(document)

        table_index = 0

        for order, (item, nesting) in enumerate(document.iterate_items()):
            element_type = element_type_for(getattr(item, "label", None))
            provenance = (getattr(item, "prov", None) or [None])[0]
            page_number = int(getattr(provenance, "page_no", 0) or 0) or None
            page_width, page_height = (
                page_sizes.get(page_number, (None, None)) if page_number else (None, None)
            )
            bbox = normalise_bbox(
                getattr(provenance, "bbox", None) if provenance is not None else None,
                page_height=page_height,
                page_width=page_width,
            )
            text = str(getattr(item, "text", "") or "")

            if element_type is ElementType.HEADING:
                level = int(getattr(item, "level", None) or nesting or 1)
                level = max(1, min(6, level))
                while section_stack and section_stack[-1][0] >= level:
                    section_stack.pop()
                section_id = f"sec_{len(sections) + 1:04d}"
                parent = section_stack[-1][1] if section_stack else None
                sections.append(
                    ParsedSection(
                        section_id=section_id,
                        title=(text.strip() or "Untitled section")[:300],
                        level=level,
                        parent_section_id=parent,
                        page_start=page_number,
                        page_end=page_number,
                    )
                )
                section_stack.append((level, section_id))
                current_section = section_id
            else:
                current_section = section_stack[-1][1] if section_stack else None

            if element_type is ElementType.TABLE:
                table_index += 1
                record = self._table_record(
                    item=item,
                    document=document,
                    table_index=table_index,
                    page_number=page_number,
                    bbox=bbox,
                    order=order,
                    section_id=current_section,
                )
                tables.append(record)
                elements.append(
                    self._element(
                        request=request,
                        order=order,
                        element_type=ElementType.TABLE,
                        text=record.text_representation,
                        page_number=page_number,
                        section_id=current_section,
                        bbox=bbox,
                        raw=f"table:{table_index}",
                    )
                )
                continue

            if element_type in {ElementType.FIGURE, ElementType.FIGURE_CAPTION}:
                figures.append(
                    FigureRecord(
                        figure_id=f"fig_{len(figures) + 1:04d}",
                        page_number=page_number,
                        section_id=current_section,
                        reading_order=order,
                        bbox=bbox,
                        caption=(text.strip()[:500] or None)
                        if element_type is ElementType.FIGURE_CAPTION
                        else None,
                        source=ProvenanceSource.DOCLING,
                    )
                )

            if element_type is ElementType.FIGURE:
                # A figure without a caption still occupies a place in reading order, but it
                # has no text of its own: do not create an empty text element for it.
                continue

            if not text.strip() and element_type is not ElementType.HEADING:
                continue

            elements.append(
                self._element(
                    request=request,
                    order=order,
                    element_type=element_type,
                    text=text,
                    page_number=page_number,
                    section_id=current_section,
                    bbox=bbox,
                    raw=raw_label(getattr(item, "label", None)),
                    level=int(getattr(item, "level", None) or nesting or 0) or None
                    if element_type is ElementType.HEADING
                    else None,
                )
            )

        return sections, elements, tables, figures

    def _table_record(
        self,
        *,
        item: Any,
        document: Any,
        table_index: int,
        page_number: int | None,
        bbox: Any,
        order: int,
        section_id: str | None,
    ) -> TableRecord:
        cells: TableCells = []
        caption: str | None = None
        try:
            frame = item.export_to_dataframe(doc=document)
            cells = table_cells_from_frame(frame)
        except Exception:
            cells = []
        try:
            caption = str(item.caption_text(document) or "").strip()[:500] or None
        except Exception:
            caption = None

        row_count = len(cells)
        column_count = len(cells[0]) if cells else 0
        if not cells:
            extraction_status = "empty"
        elif any(not str(cell).strip() for row in cells for cell in row):
            extraction_status = "partial"
        else:
            extraction_status = "complete"
        return TableRecord(
            table_id=f"tbl_{table_index:04d}",
            page_number=page_number,
            section_id=section_id,
            reading_order=order,
            bbox=bbox,
            row_count=row_count,
            column_count=column_count,
            cells=cells,
            has_header_row=True if cells else None,
            caption=caption,
            text_representation=table_text_from_cells(cells),
            extraction_status=extraction_status,
            source=ProvenanceSource.DOCLING,
        )

    @staticmethod
    def _element(
        *,
        request: ParseRequest,
        order: int,
        element_type: ElementType,
        text: str,
        page_number: int | None,
        section_id: str | None,
        bbox: Any,
        raw: str,
        level: int | None = None,
    ) -> DocumentElement:
        digest = hashlib.sha256(
            f"{request.document_id}|{request.run_id}|{order}|{element_type.value}|"
            f"{page_number}|{text[:200]}".encode()
        ).hexdigest()[:16]
        return DocumentElement(
            block_id=f"blk_{digest}",
            element_type=element_type,
            text=text,
            page_number=page_number,
            section_id=section_id,
            reading_order=order,
            bbox=bbox,
            source=ProvenanceSource.DOCLING,
            level=level,
            metadata={"docling_label": raw},
        )
