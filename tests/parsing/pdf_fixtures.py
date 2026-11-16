"""Synthetic PDF fixtures for the document-parsing integration tests.

Every fixture is generated deterministically with PyMuPDF, so the repository stores code
instead of binaries and each test states exactly which property it exercises.  No real
scientific article is used: the documents are designed to isolate one parser capability at
a time (pagination, Cyrillic, tables, two columns, figures, scans, rotation).

A *damaged text layer* is deliberately not generated here: PyMuPDF's built-in text insertion
always writes a correct ToUnicode map, so a PDF with genuinely broken glyph mapping cannot be
produced deterministically. That scenario is covered by a Quality Gate test on a constructed
document instead of a fake PDF.

Each fixture declares its expected properties in :data:`FIXTURES`.  A test that simply opens
a PDF proves nothing, so the expectations name the observable result the parser must produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

__all__ = ["FIXTURES", "FixtureSpec", "build_all", "build_fixture"]

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

EN_HEADING = "Deterministic Extraction of Scientific Documents"
EN_SENTENCE = (
    "The platform parses a document into pages, sections and blocks, and keeps the "
    "physical page of every element."
)
EN_SECOND = "Second section describes the quality gate and its deterministic checks."
RU_HEADING = "Детерминированное извлечение структуры научных документов"
RU_SENTENCE = (
    "Платформа разбирает документ на страницы, разделы и блоки и сохраняет физическую "
    "страницу каждого элемента."
)
TABLE_HEADERS = ("Model", "BLEU", "Latency")
TABLE_ROWS = (("Transformer", "28.4", "12 ms"), ("Recurrent", "25.1", "31 ms"))


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    """One synthetic document and the properties the parser must demonstrate."""

    key: str
    purpose: str
    expected_pages: int
    expected_keywords: tuple[str, ...] = ()
    expected_tables: int = 0
    expects_ocr: bool = False
    expects_blank_page: bool = False
    expects_rotation: int = 0
    has_text_layer: bool = True
    notes: tuple[str, ...] = field(default=())


FIXTURES: dict[str, FixtureSpec] = {
    "single_page_en": FixtureSpec(
        key="single_page_en",
        purpose="PDF-INT-01: one-page digital English article",
        expected_pages=1,
        expected_keywords=("Deterministic", "quality gate"),
    ),
    "multi_page_en": FixtureSpec(
        key="multi_page_en",
        purpose="PDF-INT-04: three physical pages, complete pagination",
        expected_pages=3,
        expected_keywords=("Introduction", "Method", "Conclusion"),
    ),
    "single_page_ru": FixtureSpec(
        key="single_page_ru",
        purpose="PDF-INT-02: Cyrillic text survives extraction",
        expected_pages=1,
        expected_keywords=("Детерминированное", "страниц"),
    ),
    "two_column_en": FixtureSpec(
        key="two_column_en",
        purpose="Two-column layout keeps a readable element order",
        expected_pages=1,
        expected_keywords=("left column", "right column"),
        notes=("reading order is asserted as strictly increasing, not as a specific column order",),
    ),
    "table_simple": FixtureSpec(
        key="table_simple",
        purpose="PDF-INT-03: one ruled table with a header row",
        expected_pages=1,
        expected_keywords=("Model", "Transformer"),
        expected_tables=1,
    ),
    "table_two": FixtureSpec(
        key="table_two",
        purpose="Two independent tables on one page",
        expected_pages=1,
        expected_keywords=("BLEU", "Latency"),
        expected_tables=2,
        notes=("count may be lower if Docling merges neighbouring ruled blocks",),
    ),
    "figure_with_caption": FixtureSpec(
        key="figure_with_caption",
        purpose="Figure metadata and its caption are extracted separately",
        expected_pages=1,
        expected_keywords=("Figure 1", "caption"),
        notes=("figure pixels are not rendered; only geometry and caption text are checked",),
    ),
    "empty_page": FixtureSpec(
        key="empty_page",
        purpose="A blank physical page stays in the page sequence",
        expected_pages=2,
        expected_keywords=("Page one text",),
        expects_blank_page=True,
    ),
    "mixed_text_image": FixtureSpec(
        key="mixed_text_image",
        purpose="Text and an image on the same page",
        expected_pages=1,
        expected_keywords=("Mixed page",),
    ),
    "scanned_only": FixtureSpec(
        key="scanned_only",
        purpose="Image-only page has no usable text layer (OCR candidate)",
        expected_pages=1,
        expects_ocr=True,
        has_text_layer=False,
    ),
    "rotated_page": FixtureSpec(
        key="rotated_page",
        purpose="Page rotation does not distort the stored geometry",
        expected_pages=1,
        expected_keywords=("Rotated page",),
        expects_rotation=90,
    ),
}


def _image_bytes() -> bytes:
    """Deterministic raster image used by the figure/mixed/scan fixtures."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 240, 180), False)
    pixmap.clear_with(190)
    for x in range(0, 240, 20):
        for y in range(0, 180, 20):
            if (x // 20 + y // 20) % 2 == 0:
                pixmap.set_pixel(x, y, (40, 40, 120))
    return pixmap.tobytes("png")


def _insert_table(page: pymupdf.Page, *, top: float, rows: tuple[tuple[str, ...], ...]) -> None:
    """Draw a ruled table with one header row."""
    left, right = 72.0, 460.0
    row_height = 24.0
    columns = len(rows[0])
    width = (right - left) / columns
    for index in range(len(rows) + 1):
        y = top + index * row_height
        page.draw_line((left, y), (right, y), width=0.8)
    for column in range(columns + 1):
        x = left + column * width
        page.draw_line((x, top), (x, top + len(rows) * row_height), width=0.8)
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            page.insert_text(
                (left + column_index * width + 4, top + row_index * row_height + 16),
                cell,
                fontsize=9,
            )


def _build(key: str, path: Path) -> None:
    document = pymupdf.open()
    image = _image_bytes()

    if key == "single_page_en":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 90), EN_HEADING, fontsize=16)
        page.insert_text((72, 130), "Abstract", fontsize=12)
        page.insert_textbox(pymupdf.Rect(72, 150, 520, 230), EN_SENTENCE, fontsize=10)
        page.insert_text((72, 260), "1 Introduction", fontsize=12)
        page.insert_textbox(pymupdf.Rect(72, 280, 520, 340), EN_SECOND, fontsize=10)

    elif key == "multi_page_en":
        headings = ("1 Introduction", "2 Method", "3 Conclusion")
        bodies = (
            "The introduction explains why deterministic extraction matters for review.",
            "The method section describes pages, sections, tables and their provenance.",
            "The conclusion states that every element keeps its physical page number.",
        )
        for index in range(3):
            page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            page.insert_text((72, 90), headings[index], fontsize=14)
            page.insert_textbox(pymupdf.Rect(72, 120, 520, 260), bodies[index], fontsize=10)

    elif key == "single_page_ru":
        # Cyrillic needs a font with a correct ToUnicode map: the built-in Helvetica cannot
        # encode it, so the text would be stored as "?" and the test would prove nothing.
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_htmlbox(
            pymupdf.Rect(72, 70, 520, 260),
            f"<h2>{RU_HEADING}</h2><p>{RU_SENTENCE}</p>",
        )

    elif key == "two_column_en":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 90), "Two Column Layout", fontsize=15)
        page.insert_textbox(
            pymupdf.Rect(72, 120, 290, 400),
            "left column begins the argument about deterministic parsing and continues "
            "with several sentences so the block is wide enough to classify.",
            fontsize=10,
        )
        page.insert_textbox(
            pymupdf.Rect(320, 120, 540, 400),
            "right column continues the argument and mentions the quality gate that "
            "checks the extracted structure before any further processing.",
            fontsize=10,
        )

    elif key == "table_simple":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 90), "Results", fontsize=14)
        rows = (TABLE_HEADERS, *TABLE_ROWS)
        _insert_table(page, top=130, rows=rows)

    elif key == "table_two":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 80), "Two Tables", fontsize=14)
        _insert_table(page, top=110, rows=(("Model", "BLEU"), TABLE_ROWS[0], TABLE_ROWS[1]))
        _insert_table(page, top=250, rows=(("Mode", "Latency"), ("CPU", "12 ms"), ("GPU", "3 ms")))

    elif key == "figure_with_caption":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 90), "Figure Section", fontsize=14)
        page.insert_image(pymupdf.Rect(120, 130, 360, 310), stream=image)
        page.insert_text((120, 340), "Figure 1: caption describing the diagram.", fontsize=10)

    elif key == "empty_page":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text(
            (72, 100), "Page one text with enough characters to be useful.", fontsize=11
        )
        document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)  # intentionally blank

    elif key == "mixed_text_image":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 90), "Mixed page with text and an image", fontsize=14)
        page.insert_image(pymupdf.Rect(120, 130, 360, 310), stream=image)
        page.insert_textbox(
            pymupdf.Rect(72, 340, 520, 420),
            "The paragraph next to the figure describes the extraction pipeline.",
            fontsize=10,
        )

    elif key == "scanned_only":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_image(pymupdf.Rect(60, 60, 540, 780), stream=image)

    elif key == "rotated_page":
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_text((72, 100), "Rotated page text", fontsize=14)
        page.insert_textbox(
            pymupdf.Rect(72, 130, 520, 240),
            "The page is rotated by ninety degrees and the text must survive the rotation.",
            fontsize=10,
        )
        page.set_rotation(90)

    else:  # pragma: no cover - guarded by the registry
        raise ValueError(f"unknown fixture {key!r}")

    document.save(path)
    document.close()


def build_fixture(key: str, directory: Path) -> Path:
    """Create one fixture inside ``directory`` and return its path."""
    if key not in FIXTURES:
        raise KeyError(f"unknown fixture {key!r}; known: {sorted(FIXTURES)}")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.pdf"
    if not path.exists():
        _build(key, path)
    return path


def build_all(directory: Path) -> dict[str, Path]:
    """Create every fixture once and return ``{key: path}``."""
    return {key: build_fixture(key, directory) for key in FIXTURES}
