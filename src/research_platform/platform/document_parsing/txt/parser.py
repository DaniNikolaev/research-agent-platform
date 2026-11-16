"""TXT block parser: line-oriented structure without invented pages.

A text source has no physical pages, so this adapter never creates one: ``page_number`` is
``None`` everywhere, and ``page_count`` stays ``None``.  What it does produce is a
deterministic block/line structure derived from the decoded text:

* lines keep their 0-based line numbers;
* consecutive non-empty lines form a paragraph block, separated by blank lines;
* a line is a heading only when it is short, has no terminal punctuation and is followed by
  a blank line — a weak, documented signal, recorded as ``confidence`` below 1.0 rather than
  claimed as structure.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
from typing import Final

from research_platform.application.ports.document_pipeline import (
    DocumentParserPort,
    ParsedPayload,
    ParseRequest,
)
from research_platform.domain.documents.elements import (
    DocumentElement,
    ElementType,
    ProvenanceSource,
)
from research_platform.domain.documents.parsed import ParserProvenance, ParsingStatus

__all__ = ["TxtBlockParser"]

PARSER_NAME: Final = "txt-block-parser"
PIPELINE_VERSION: Final = "txt/1"
OPTIONS_HASH: Final = hashlib.sha256(b"txt-blocks-v1").hexdigest()[:16]

_MAX_HEADING_CHARS: Final = 120
_TERMINAL_PUNCTUATION: Final = tuple(".!?:;,»\"'")


class TxtBlockParser(DocumentParserPort):
    """Deterministic block parser for already-decoded text."""

    parser_name = PARSER_NAME

    def __init__(self) -> None:
        self._warnings: list[str] = []

    @property
    def parser_version(self) -> str:
        """Version of this adapter (it has no third-party engine)."""
        return "1.0"

    @property
    def options_hash(self) -> str:
        """Hash of the parsing options (fixed for this adapter version)."""
        return OPTIONS_HASH

    def provenance(self) -> ParserProvenance:
        """Provenance block recorded in the parsed document."""
        return ParserProvenance(
            parser_name=PARSER_NAME,
            parser_version=self.parser_version,
            pipeline_version=PIPELINE_VERSION,
            parser_options_hash=OPTIONS_HASH,
            backend_name="python",
            table_extraction_enabled=False,
            ocr_enabled=False,
            model_assets_revision=metadata.version("docling") if _has_docling() else None,
        )

    def parse_txt(self, request: ParseRequest) -> ParsedPayload:
        """Parse decoded text bytes (UTF-8) into elements.

        Raises:
            ValueError: the payload is empty or is not valid UTF-8.
        """
        if not request.data:
            raise ValueError("text source is empty")
        try:
            text = request.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "TxtBlockParser expects already-decoded UTF-8 text; decoding belongs to the "
                f"text decoder adapter ({exc})"
            ) from exc

        lines = text.splitlines()
        elements: list[DocumentElement] = []
        warnings: list[str] = []
        index = 0
        paragraph: list[str] = []
        paragraph_start = 0

        def flush() -> None:
            nonlocal paragraph
            if not paragraph:
                return
            body = "\n".join(paragraph).strip()
            if body:
                elements.append(
                    self._element(
                        request=request,
                        order=len(elements),
                        element_type=ElementType.PARAGRAPH,
                        text=body,
                        line_start=paragraph_start,
                        line_end=paragraph_start + len(paragraph) - 1,
                        confidence=1.0,
                    )
                )
            paragraph = []

        while index < len(lines):
            raw = lines[index]
            stripped = raw.strip()
            if not stripped:
                flush()
                index += 1
                continue

            next_is_blank = index + 1 >= len(lines) or not lines[index + 1].strip()
            is_heading = (
                next_is_blank
                and len(stripped) <= _MAX_HEADING_CHARS
                and not stripped.endswith(_TERMINAL_PUNCTUATION)
                and not stripped[0].islower()
            )
            if is_heading:
                flush()
                elements.append(
                    self._element(
                        request=request,
                        order=len(elements),
                        element_type=ElementType.HEADING,
                        text=stripped,
                        line_start=index,
                        line_end=index,
                        confidence=0.6,
                        level=1,
                    )
                )
                index += 1
                continue

            if not paragraph:
                paragraph_start = index
            paragraph.append(stripped)
            index += 1

        flush()

        if not elements:
            warnings.append("the text source contains no non-empty lines")
            return ParsedPayload(
                parser=self.provenance(),
                status=ParsingStatus.EMPTY,
                pages=(),
                sections=(),
                elements=(),
                tables=(),
                figures=(),
                warnings=tuple(warnings),
                page_count=None,
            )

        return ParsedPayload(
            parser=self.provenance(),
            status=ParsingStatus.PARSED,
            pages=(),
            sections=(),
            elements=tuple(elements),
            tables=(),
            figures=(),
            warnings=tuple(warnings),
            page_count=None,
        )

    def parse_pdf(self, request: ParseRequest) -> ParsedPayload:  # pragma: no cover
        """Not implemented here: PDF parsing has its own adapter."""
        raise NotImplementedError("TxtBlockParser parses text only; use DoclingPdfParser")

    def _element(
        self,
        *,
        request: ParseRequest,
        order: int,
        element_type: ElementType,
        text: str,
        line_start: int,
        line_end: int,
        confidence: float,
        level: int | None = None,
    ) -> DocumentElement:
        digest = hashlib.sha256(
            f"{request.document_id}|{order}|{element_type.value}|{line_start}|{text[:200]}".encode()
        ).hexdigest()[:16]
        return DocumentElement(
            block_id=f"blk_{digest}",
            element_type=element_type,
            text=text,
            page_number=None,
            section_id=None,
            reading_order=order,
            bbox=None,
            source=ProvenanceSource.TXT_DECODER,
            level=level,
            confidence=confidence,
            metadata={"line_start": str(line_start), "line_end": str(line_end)},
        )


def _has_docling() -> bool:
    try:
        metadata.version("docling")
    except metadata.PackageNotFoundError:
        return False
    return True
