"""PyMuPDF-based inspection and plain-text fallback.

PyMuPDF is the *diagnostic* mechanism: it answers "how many physical pages does this file
really have, is it encrypted, does each page carry a native text layer, how much of the
page is covered by images" and it can produce plain text when the structured parser fails
technically.  It is never treated as an equal to the structured parser, and its output is
always labelled as a fallback.

Nothing here opens a network connection, executes JavaScript or follows a link found in the
document: the module only reads page geometry, text and image counts.
"""

from __future__ import annotations

from typing import Final

import pymupdf

from research_platform.application.ports.document_pipeline import (
    DocumentInspection,
    FallbackExtraction,
    PageInspection,
)

__all__ = ["MIN_NATIVE_TEXT_CHARS", "PyMuPDFInspector"]

MIN_NATIVE_TEXT_CHARS: Final = 20
"""A page with fewer native characters than this counts as "no usable text layer"."""

_IMAGE_COVERAGE_TOLERANCE: Final = 0.90


class PyMuPDFInspector:
    """Inspect PDF bytes and (only on demand) extract plain text."""

    def inspect_pdf(self, data: bytes) -> DocumentInspection:
        """Inspect a PDF without mutating it.

        Raises:
            ValueError: the bytes cannot be opened as a PDF.
        """
        if not data:
            raise ValueError("pdf source is empty")
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ValueError(f"cannot open the PDF: {exc}") from exc

        try:
            if document.needs_pass or document.is_encrypted:
                return DocumentInspection(
                    page_count=document.page_count,
                    is_encrypted=True,
                    warnings=("the PDF is encrypted; pages cannot be inspected",),
                )
            pages: list[PageInspection] = []
            warnings: list[str] = []
            for index in range(document.page_count):
                page = document.load_page(index)
                text = page.get_text("text") or ""
                images = page.get_images(full=True)
                pages.append(
                    PageInspection(
                        page_number=index + 1,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        rotation=int(page.rotation or 0),
                        has_native_text=len(text.strip()) >= MIN_NATIVE_TEXT_CHARS,
                        text_char_count=len(text),
                        image_count=len(images),
                        image_area_ratio=_image_area_ratio(page),
                    )
                )
            if not document.page_count:
                warnings.append("the PDF reports zero pages")
            return DocumentInspection(
                page_count=document.page_count,
                pages=tuple(pages),
                warnings=tuple(warnings),
            )
        finally:
            document.close()

    def extract_text_fallback(self, data: bytes) -> FallbackExtraction:
        """Plain text per physical page; used only when the primary parser fails."""
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ValueError(f"cannot open the PDF for fallback extraction: {exc}") from exc
        try:
            pages: list[tuple[int, str]] = []
            warnings: list[str] = []
            for index in range(document.page_count):
                page = document.load_page(index)
                text = page.get_text("text") or ""
                pages.append((index + 1, text))
            if not any(text.strip() for _, text in pages):
                warnings.append("fallback extraction found no text at all")
            return FallbackExtraction(
                pages=tuple(pages),
                page_count=document.page_count,
                warnings=tuple(warnings),
            )
        finally:
            document.close()


def _image_area_ratio(page: object) -> float | None:
    """Approximate share of the page covered by raster images.

    Returns ``None`` when the page has no images, so "no images" is not confused with
    "images cover nothing".
    """
    try:
        images = page.get_images(full=True)  # type: ignore[attr-defined]
    except Exception:
        return None
    if not images:
        return None
    page_area = float(page.rect.width) * float(page.rect.height)  # type: ignore[attr-defined]
    if page_area <= 0:
        return None
    covered = 0.0
    for info in images:
        try:
            for rect in page.get_image_rects(info[0]):  # type: ignore[attr-defined]
                covered += float(rect.width) * float(rect.height)
        except Exception:
            continue
    if covered <= 0:
        return _IMAGE_COVERAGE_TOLERANCE if images else None
    return min(1.0, covered / page_area)
