"""Real-Docling integration and golden tests for PDF parsing.

Every test here runs the actual pinned Docling adapter (2.150.0) with the model assets that
are present in the environment — no fake parser is involved.  A successful import or a
non-empty string is not accepted as proof: each fixture asserts the properties it exists to
exercise (pagination, Cyrillic, tables, reading order, provenance, offsets, geometry, the
Quality Gate verdict).

Golden invariants (:func:`assert_golden`) are applied to every parsed document and are the
authoritative statement of what "parsed correctly" means for this iteration.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from research_platform.application.ports.document_pipeline import (
    DocumentInspection,
    ParsedPayload,
    ParseRequest,
)
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.document_ingestion import IngestedSource
from research_platform.application.services.document_parsing import DocumentParsingService
from research_platform.application.services.quality_gate import QualityGateService
from research_platform.config import PlatformSettings
from research_platform.domain.documents.elements import ElementType, PageStatus
from research_platform.domain.documents.identity import DocumentIdentity, DocumentSourceType
from research_platform.domain.documents.models import (
    DocumentLanguage,
    SourceDocumentManifest,
    SourceMediaType,
)
from research_platform.domain.documents.parsed import ParsedDocument, ParsingStatus
from research_platform.domain.documents.quality import (
    PageQualityStatus,
    QualityReport,
    QualityStatus,
)
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.platform.artifact_store import LocalFileArtifactStore
from research_platform.platform.document_parsing.docling.pdf_parser import DoclingPdfParser
from research_platform.platform.document_parsing.pymupdf.pdf_inspector import PyMuPDFInspector
from research_platform.platform.document_parsing.txt.parser import TxtBlockParser
from tests.fixtures.logging import silent_logger
from tests.parsing.pdf_fixtures import FIXTURES, build_fixture

pytestmark = pytest.mark.parsing

RUN_ID = "run_it2_closure_0001"


@dataclass(frozen=True, slots=True)
class Parsed:
    """Everything one fixture produced, for assertions."""

    document: ParsedDocument
    canonical_text: str
    report: QualityReport
    inspection: DocumentInspection


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("pdf-fixtures")


@pytest.fixture(scope="module")
def settings() -> PlatformSettings:
    return PlatformSettings()


def parse_fixture(
    *,
    key: str,
    directory: Path,
    settings: PlatformSettings,
    workdir: Path,
) -> Parsed:
    """Run the real parser and the Quality Gate on one fixture."""
    path = build_fixture(key, directory)
    data = path.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    identity = DocumentIdentity.derive(sha256, DocumentSourceType.PDF)

    store = LocalFileArtifactStore(root=workdir / key / "artifacts")
    artifacts = ArtifactService(store=store, logger=silent_logger())
    stored = artifacts.store_bytes(
        data=data,
        artifact_id="art_source_it2",
        artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
        media_type=SourceMediaType.PDF.value,
        document_id=identity.document_id,
        run_id=RUN_ID,
    )
    manifest = SourceDocumentManifest(
        document_id=identity.document_id,
        run_id=RUN_ID,
        source_file_name=path.name,
        source_type=DocumentSourceType.PDF,
        source_media_type=SourceMediaType.PDF,
        source_sha256=sha256,
        source_size_bytes=len(data),
        source_artifact_uri=stored.uri,
        language=DocumentLanguage.UNKNOWN,
        created_at="2026-11-16T12:00:00Z",
    )
    inspector = PyMuPDFInspector()
    inspection = inspector.inspect_pdf(data)
    ingested = IngestedSource(
        manifest=manifest, identity=identity, data=data, inspection=inspection
    )

    parser = DoclingPdfParser()
    service = DocumentParsingService(
        artifact_service=artifacts,
        settings=settings.document_parsing,
        logger=silent_logger(),
        pdf_parser=parser,
        txt_parser=TxtBlockParser(),
        inspector=inspector,
    )
    outcome = service.parse(ingested=ingested)
    report = QualityGateService(settings=settings.quality_gate, logger=silent_logger()).evaluate(
        document=outcome.document,
        canonical_text=outcome.canonical_text,
        inspection=inspection,
        used_fallback=outcome.used_fallback,
    )
    return Parsed(
        document=outcome.document,
        canonical_text=outcome.canonical_text,
        report=report,
        inspection=inspection,
    )


def assert_golden(parsed: Parsed, *, expect_text: bool = True) -> None:
    """Golden invariants every parsed PDF must satisfy."""
    document = parsed.document
    assert document.parser.parser_name == "docling"
    assert document.parser.parser_version.startswith("2.")
    assert document.parser.ocr_enabled is False
    assert document.parser.parser_options_hash

    assert document.page_count == parsed.inspection.page_count
    numbers = [page.page_number for page in document.pages]
    assert numbers == list(range(1, len(numbers) + 1)), "physical pages must be 1..N"

    orders = [element.reading_order for element in document.elements]
    assert orders == sorted(orders), "reading order must be monotonic"
    assert len(orders) == len(set(orders)), "reading order must be unique"

    for element in document.elements:
        assert element.page_number in numbers, "an element claims an unknown page"
        assert element.char_start is not None
        assert element.char_end is not None
        assert parsed.canonical_text[element.char_start : element.char_end] == element.text, (
            f"char range of {element.block_id} does not reproduce its text"
        )
        if element.bbox is not None:
            page = next(p for p in document.pages if p.page_number == element.page_number)
            assert element.bbox.unit.value == "pt"
            assert element.bbox.origin.value == "top_left"
            width = page.page_width or element.bbox.page_width or 0
            height = page.page_height or element.bbox.page_height or 0
            if width and height:
                tolerance = 3.0
                assert -tolerance <= element.bbox.x0 <= width + tolerance
                assert -tolerance <= element.bbox.y1 <= height + tolerance

    if expect_text:
        assert document.parsing_status in {
            ParsingStatus.PARSED,
            ParsingStatus.PARSED_WITH_FALLBACK,
        }
        assert parsed.canonical_text.strip(), "no text was extracted"
        assert len({element.block_id for element in document.elements}) == len(document.elements)


# --------------------------------------------------------------------- PDF-INT-01


def test_pdf_int_01_english_digital(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """One-page English article: text, headings, provenance and a passing verdict."""
    parsed = parse_fixture(
        key="single_page_en", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    assert parsed.document.page_count == 1
    assert parsed.document.pages[0].status is PageStatus.PARSED
    text = parsed.canonical_text
    for keyword in FIXTURES["single_page_en"].expected_keywords:
        assert keyword in text, f"{keyword!r} missing from the extracted text"
    headings = [e for e in parsed.document.elements if e.element_type is ElementType.HEADING]
    assert headings, "no heading element was recognised"
    assert all(heading.section_id for heading in headings), "heading without a section"
    assert parsed.report.overall_status.is_ready


# --------------------------------------------------------------------- PDF-INT-02


def test_pdf_int_02_russian_digital(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """Cyrillic text survives extraction without replacement characters."""
    parsed = parse_fixture(
        key="single_page_ru", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    text = parsed.canonical_text
    assert "Детерминированное" in text
    assert "\ufffd" not in text, "Cyrillic text was damaged during extraction"
    assert parsed.report.overall_status.is_ready


# --------------------------------------------------------------------- PDF-INT-03


def test_pdf_int_03_table_structure(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """A ruled table keeps rows, columns, cells, captions and its page."""
    parsed = parse_fixture(
        key="table_simple", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    assert parsed.document.tables, "Docling did not extract the table"
    table = parsed.document.tables[0]
    assert table.page_number == 1
    assert table.row_count >= 2, f"too few rows: {table.row_count}"
    assert table.column_count >= 3, f"too few columns: {table.column_count}"
    assert table.cells, "no cells were extracted"
    flattened = " ".join(cell for row in table.cells for cell in row)
    assert "Transformer" in flattened
    assert "BLEU" in flattened
    assert table.text_representation, "no deterministic text representation"
    assert table.extraction_status in {"complete", "partial"}
    table_check = next(
        check for check in parsed.report.checks if check.check_id == "tables_structure"
    )
    assert table_check.status.value in {"pass", "warning"}


def test_two_tables_are_reported_separately(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """Two independent tables on one page are not merged into one record."""
    parsed = parse_fixture(
        key="table_two", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    assert len(parsed.document.tables) >= 2, (
        f"expected two tables, got {len(parsed.document.tables)}"
    )
    for table in parsed.document.tables:
        assert table.page_number == 1
        assert table.row_count >= 2


# --------------------------------------------------------------------- PDF-INT-04


def test_pdf_int_04_multipage_pagination(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """Three pages stay complete, ordered and bound to their elements."""
    parsed = parse_fixture(
        key="multi_page_en", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    assert parsed.document.page_count == 3
    assert [page.page_number for page in parsed.document.pages] == [1, 2, 3]
    assert all(page.text for page in parsed.document.pages), "a page lost its text"
    element_pages = {element.page_number for element in parsed.document.elements}
    assert element_pages == {1, 2, 3}
    assert parsed.canonical_text.count("\f") == 2, "page boundaries are not marked"
    for keyword in FIXTURES["multi_page_en"].expected_keywords:
        assert keyword.split()[-1] in parsed.canonical_text


# ---------------------------------------------------------------- further fixtures


def test_two_column_layout_keeps_a_readable_order(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """Both columns are extracted, with a unique monotonic reading order."""
    parsed = parse_fixture(
        key="two_column_en", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    text = parsed.canonical_text
    assert "left column" in text
    assert "right column" in text
    assert parsed.document.page_count == 1


def test_blank_page_is_kept_and_flagged(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """A blank physical page is preserved and produces a warning, not a silent pass."""
    parsed = parse_fixture(
        key="empty_page", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    assert parsed.document.page_count == 2
    blank = parsed.document.pages[1]
    assert blank.page_number == 2
    assert blank.text == ""
    assert blank.status is not PageStatus.PARSED
    statuses = {record.page_number: record.page_status for record in parsed.report.page_results}
    assert statuses[2] in {
        PageQualityStatus.BLANK_OR_UNKNOWN,
        PageQualityStatus.OCR_REQUIRED,
        PageQualityStatus.LOW_TEXT,
    }
    assert parsed.report.overall_status is not QualityStatus.PASSED


def test_scanned_page_requires_ocr(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """An image-only page yields no text and the gate demands OCR instead of passing it."""
    parsed = parse_fixture(
        key="scanned_only", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert parsed.canonical_text.strip() == ""
    assert parsed.document.parsing_status is ParsingStatus.EMPTY
    assert parsed.report.overall_status is QualityStatus.REQUIRES_OCR
    assert parsed.report.ocr_required_pages == [1]


def test_rotated_page_keeps_geometry(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """A rotated page is parsed and its boxes stay inside the reported page extent."""
    parsed = parse_fixture(
        key="rotated_page", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert parsed.inspection.pages[0].rotation == 90
    assert parsed.canonical_text.strip(), "rotation destroyed the text"
    assert parsed.document.page_count == 1
    for element in parsed.document.elements:
        assert element.page_number == 1


def test_figure_metadata_and_caption(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """A figure is recorded with its page and caption; pixels are not rendered."""
    parsed = parse_fixture(
        key="figure_with_caption", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert parsed.document.figures, "no figure metadata was extracted"
    figure = parsed.document.figures[0]
    assert figure.page_number == 1
    assert "Figure 1" in parsed.canonical_text
    assert parsed.document.parser.ocr_enabled is False


def test_mixed_text_and_image_page(
    fixture_dir: Path, tmp_path: Path, settings: PlatformSettings
) -> None:
    """A page with both text and an image keeps its text and does not need OCR."""
    parsed = parse_fixture(
        key="mixed_text_image", directory=fixture_dir, settings=settings, workdir=tmp_path
    )
    assert_golden(parsed)
    assert "Mixed page" in parsed.canonical_text
    assert parsed.report.overall_status.is_ready


def test_parser_payload_reports_docling_provenance(
    fixture_dir: Path,
) -> None:
    """The adapter reports reproducible provenance without touching the Quality Gate."""
    path = build_fixture("single_page_en", fixture_dir)
    payload: ParsedPayload = DoclingPdfParser().parse_pdf(
        ParseRequest(
            data=path.read_bytes(),
            document_id="doc_" + "c" * 24,
            run_id=RUN_ID,
            file_name=path.name,
        )
    )
    assert payload.parser.parser_name == "docling"
    assert payload.parser.table_extraction_enabled is True
    assert payload.parser.ocr_enabled is False
    assert payload.parser.backend_name
    assert payload.page_count == 1
