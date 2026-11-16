"""Ports of the document pipeline: parsing, inspection, text decoding and OCR.

All four ports work on **bytes**, never on a filesystem path: an adapter may stage bytes in
its own temporary area, but the caller (application layer) never learns where the file is.
That keeps Domain/Application free of Docling, PyMuPDF and Windows paths, and makes every
adapter replaceable in tests.

The parser port returns domain models (:class:`ParsedPayload`), not parser objects, so the
internal structure of a parser cannot leak into stored artefacts or contracts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from research_platform.domain.documents.elements import (
    DocumentElement,
    FigureRecord,
    PageRecord,
    ParsedSection,
    TableRecord,
)
from research_platform.domain.documents.parsed import ParserProvenance, ParsingStatus

__all__ = [
    "DecodedText",
    "DocumentInspection",
    "DocumentInspectorPort",
    "DocumentParserPort",
    "OcrEnginePort",
    "OcrPageResult",
    "OcrRequest",
    "OcrResult",
    "PageInspection",
    "ParseRequest",
    "ParsedPayload",
    "SourceFile",
    "SourceReaderPort",
]


@dataclass(frozen=True, slots=True)
class ParseRequest:
    """Input of one parsing call."""

    data: bytes
    document_id: str
    run_id: str
    file_name: str
    language: str = "unknown"


@dataclass(frozen=True, slots=True)
class ParsedPayload:
    """Parser-independent result of one parsing call."""

    parser: ParserProvenance
    status: ParsingStatus
    pages: tuple[PageRecord, ...] = ()
    sections: tuple[ParsedSection, ...] = ()
    elements: tuple[DocumentElement, ...] = ()
    tables: tuple[TableRecord, ...] = ()
    figures: tuple[FigureRecord, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    page_count: int | None = None


@runtime_checkable
class DocumentParserPort(Protocol):
    """Structured parsing of one document (PDF or TXT)."""

    @property
    def parser_name(self) -> str:
        """Stable adapter name recorded in every result."""
        ...

    @property
    def parser_version(self) -> str:
        """Version of the underlying engine."""
        ...

    @property
    def options_hash(self) -> str:
        """Hash of the effective parser options (reproducibility)."""
        ...

    def parse_pdf(self, request: ParseRequest) -> ParsedPayload:
        """Extract structure and text from PDF bytes."""
        ...

    def parse_txt(self, request: ParseRequest) -> ParsedPayload:
        """Extract blocks and lines from already-decoded text bytes."""
        ...


@dataclass(frozen=True, slots=True)
class PageInspection:
    """Diagnostics about one page gathered independently of the main parser."""

    page_number: int
    width: float
    height: float
    rotation: int = 0
    has_native_text: bool = False
    text_char_count: int = 0
    image_count: int = 0
    image_area_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class DocumentInspection:
    """Result of inspecting a PDF before or after parsing."""

    page_count: int
    is_encrypted: bool = False
    is_corrupt: bool = False
    pages: tuple[PageInspection, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def text_pages(self) -> tuple[int, ...]:
        """Pages that carry a usable native text layer."""
        return tuple(page.page_number for page in self.pages if page.has_native_text)

    @property
    def image_only_pages(self) -> tuple[int, ...]:
        """Pages without native text but with images (scan candidates)."""
        return tuple(
            page.page_number
            for page in self.pages
            if not page.has_native_text and page.image_count > 0
        )


@dataclass(frozen=True, slots=True)
class FallbackExtraction:
    """Plain-text extraction used only when the primary parser fails technically."""

    pages: tuple[tuple[int, str], ...] = ()
    page_count: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def total_chars(self) -> int:
        """Total extracted characters across pages."""
        return sum(len(text) for _, text in self.pages)


@runtime_checkable
class DocumentInspectorPort(Protocol):
    """Independent inspection and plain-text fallback (PyMuPDF in this iteration)."""

    def inspect_pdf(self, data: bytes) -> DocumentInspection:
        """Inspect a PDF: page count, encryption, text layer, images, geometry."""
        ...

    def extract_text_fallback(self, data: bytes) -> FallbackExtraction:
        """Plain text per page, used only when the primary parser fails."""
        ...


@dataclass(frozen=True, slots=True)
class DecodedText:
    """Result of decoding a text source."""

    text: str
    encoding: str
    encoding_uncertain: bool = False
    had_bom: bool = False
    replacement_count: int = 0
    warnings: tuple[str, ...] = ()


@runtime_checkable
class TextDecoderPort(Protocol):
    """Deterministic decoding of a text source."""

    def decode(self, data: bytes) -> DecodedText:
        """Decode bytes with an explicit, documented encoding order."""
        ...


@dataclass(frozen=True, slots=True)
class OcrRequest:
    """What to recognise."""

    document_id: str
    run_id: str
    page_numbers: tuple[int, ...]
    languages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    """Recognised text of one page."""

    page_number: int
    text: str
    confidence: float | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Result of an OCR call."""

    engine_id: str
    engine_version: str
    pages: tuple[OcrPageResult, ...] = ()
    warnings: tuple[str, ...] = ()


@runtime_checkable
class OcrEnginePort(Protocol):
    """Optical character recognition backend.

    No backend is shipped: ``OI-008`` is not decided by the owner, so the platform can only
    *detect* the need for OCR and route the document to ``requires_ocr``.  A backend must
    never fabricate text: an unavailable engine reports ``is_available() == False``.
    """

    @property
    def engine_id(self) -> str:
        """Stable engine identifier."""
        ...

    @property
    def engine_version(self) -> str:
        """Engine version."""
        ...

    def supported_languages(self) -> Sequence[str]:
        """Languages the engine can recognise."""
        ...

    def is_available(self) -> bool:
        """Whether the engine can actually run in this environment."""
        ...

    def recognize(self, request: OcrRequest, page_images: Sequence[bytes]) -> OcrResult:
        """Recognise the requested pages from rendered page images."""
        ...


@dataclass(frozen=True, slots=True)
class SourceFile:
    """Bytes read from the user-supplied file, with what the reader observed."""

    data: bytes
    size_bytes: int
    file_name: str
    extension: str


@runtime_checkable
class SourceReaderPort(Protocol):
    """Read a user-supplied source file safely.

    Reading is infrastructure: it touches the filesystem, refuses directories, symbol links
    and unreadable files, and enforces the size limit before loading anything into memory.
    """

    def read(self, path: str, *, max_size_bytes: int) -> SourceFile:
        """Read the file once and return its bytes.

        Raises:
            DocumentIngestionError: the file is missing, not a regular file, a symbol link,
                unreadable or larger than ``max_size_bytes``.
        """
        ...

    def fingerprint(self, path: str) -> tuple[int, int]:
        """``(size, mtime_ns)`` used to detect that the file changed while being processed."""
        ...
