"""Quality Gate scenarios on constructed documents (TZ §9).

These tests do not need a parser: they feed the gate a document whose defect is known
exactly, which is the only way to prove that a specific check fires.  The real-parser path is
covered separately in ``test_pdf_docling_integration.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research_platform.application.ports.document_pipeline import DocumentInspection
from research_platform.application.services.quality_gate import QualityGateService
from research_platform.config import PlatformSettings
from research_platform.domain.documents.elements import (
    DocumentElement,
    ElementType,
    PageRecord,
    PageStatus,
    ProvenanceSource,
    TableRecord,
)
from research_platform.domain.documents.identity import DocumentSourceType
from research_platform.domain.documents.parsed import (
    CanonicalTextInfo,
    ParsedDocument,
    ParserProvenance,
    ParsingStatus,
)
from research_platform.domain.documents.provenance import BBox
from research_platform.domain.documents.quality import (
    QualityStatus,
)
from tests.fixtures.logging import silent_logger

DOCUMENT_ID = "doc_" + "a" * 24
RUN_ID = "run_gate_0001"


def _provenance() -> ParserProvenance:
    return ParserProvenance(
        parser_name="docling",
        parser_version="2.150.0",
        pipeline_version="standard-pdf/1",
        parser_options_hash="0123456789abcdef",
        backend_name="docling-parse",
        table_extraction_enabled=True,
        ocr_enabled=False,
    )


def _page(number: int, text: str, status: PageStatus = PageStatus.PARSED) -> PageRecord:
    return PageRecord(page_number=number, status=status, text=text)


def _element(
    text: str,
    *,
    page: int | None = 1,
    order: int = 0,
    char_start: int | None = None,
    char_end: int | None = None,
    bbox: BBox | None = None,
) -> DocumentElement:
    return DocumentElement(
        block_id=f"blk_gate_{order:04d}",
        element_type=ElementType.PARAGRAPH,
        text=text,
        page_number=page,
        reading_order=order,
        bbox=bbox,
        char_start=char_start,
        char_end=char_end,
        source=ProvenanceSource.DOCLING,
    )


def _document(
    *,
    pages: list[PageRecord],
    elements: list[DocumentElement],
    tables: list[TableRecord] | None = None,
    page_count: int | None = None,
    status: ParsingStatus = ParsingStatus.PARSED,
    source_type: DocumentSourceType = DocumentSourceType.PDF,
) -> ParsedDocument:
    return ParsedDocument(
        document_id=DOCUMENT_ID,
        run_id=RUN_ID,
        source_sha256="a" * 64,
        source_type=source_type,
        language="en",
        parser=_provenance(),
        parsing_status=status,
        page_count=page_count if page_count is not None else (len(pages) or None),
        pages=pages,
        sections=[],
        elements=elements,
        tables=tables or [],
        figures=[],
        canonical_text=CanonicalTextInfo(
            artifact_uri=(
                f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/parsed_document/"
                "art_canonical_0001"
            ),
            canonicalization_version="1.0",
            char_count=0,
            page_separator="\f",
        ),
        source_artifact_uri=(
            f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/original_document/art_src_0001"
        ),
        created_at="2026-11-16T12:00:00Z",
    )


def _gate(settings: PlatformSettings | None = None) -> QualityGateService:
    return QualityGateService(
        settings=(settings or PlatformSettings()).quality_gate, logger=silent_logger()
    )


def _long_text(repeat: int = 6) -> str:
    return (
        "Deterministic extraction keeps every element bound to its physical page. " * repeat
    ).strip()


# ------------------------------------------------------------------ happy paths


def test_clean_document_passes() -> None:
    text = _long_text()
    document = _document(
        pages=[_page(1, text)], elements=[_element(text, char_start=0, char_end=len(text))]
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    assert report.overall_status is QualityStatus.PASSED
    assert report.errors_count == 0
    assert report.warnings_count == 0


def test_warnings_only_yield_passed_with_warnings() -> None:
    text = _long_text()
    document = _document(
        pages=[_page(1, text), _page(2, "", PageStatus.EMPTY)],
        elements=[_element(text, char_start=0, char_end=len(text))],
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    assert report.overall_status is QualityStatus.PASSED_WITH_WARNINGS
    assert any(check.check_id == "page_blank_or_unknown" for check in report.checks)


# --------------------------------------------------------------------- blocking


def test_page_count_mismatch_fails() -> None:
    text = _long_text()
    document = _document(
        pages=[_page(1, text)],
        elements=[_element(text, char_start=0, char_end=len(text))],
    )
    report = _gate().evaluate(
        document=document,
        canonical_text=text,
        inspection=DocumentInspection(page_count=3),
    )
    assert report.overall_status is QualityStatus.FAILED
    check = next(
        item for item in report.checks if item.check_id == "structure_page_count_matches_source"
    )
    assert check.status.value == "failed"
    assert check.severity.value == "critical"


def test_missing_text_source_is_failed() -> None:
    document = _document(pages=[_page(1, "", PageStatus.EMPTY)], elements=[])
    report = _gate().evaluate(document=document, canonical_text="")
    assert report.overall_status is QualityStatus.FAILED


def test_replacement_characters_block_the_document() -> None:
    text = "damaged " + "\ufffd" * 60
    document = _document(
        pages=[_page(1, text)], elements=[_element(text, char_start=0, char_end=len(text))]
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    assert report.overall_status in {QualityStatus.NEEDS_HUMAN_REVIEW, QualityStatus.FAILED}
    check = next(item for item in report.checks if item.check_id == "text_replacement_characters")
    assert check.status.value == "failed"


def test_broken_character_ranges_are_detected() -> None:
    text = _long_text()
    document = _document(
        pages=[_page(1, text)],
        elements=[_element("a different string", char_start=0, char_end=17)],
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    check = next(item for item in report.checks if item.check_id == "provenance_character_ranges")
    assert check.status.value == "failed"
    assert report.overall_status is QualityStatus.NEEDS_HUMAN_REVIEW


def test_character_range_outside_the_text_is_detected() -> None:
    text = _long_text()
    document = _document(
        pages=[_page(1, text)],
        elements=[_element(text, char_start=0, char_end=len(text) + 500)],
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    check = next(item for item in report.checks if item.check_id == "provenance_character_ranges")
    assert check.status.value == "failed"


def test_bbox_outside_the_page_is_flagged() -> None:
    text = _long_text()
    element = _element(
        text,
        char_start=0,
        char_end=len(text),
        bbox=BBox(
            x0=10,
            y0=10,
            x1=590,
            y1=830,
            unit="pt",
            origin="top_left",
            page_width=600,
            page_height=840,
        ),
    )
    document = _document(pages=[_page(1, text)], elements=[element])
    document = document.model_copy(
        update={
            "pages": [
                PageRecord(
                    page_number=1,
                    page_width=300,
                    page_height=400,
                    status=PageStatus.PARSED,
                    text=text,
                )
            ]
        }
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    check = next(item for item in report.checks if item.check_id == "provenance_bbox_bounds")
    assert check.status.value == "warning"


def test_missing_page_provenance_is_flagged() -> None:
    text = _long_text()
    document = _document(
        pages=[_page(1, text)],
        elements=[_element(text, page=None, char_start=0, char_end=len(text))],
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    check = next(item for item in report.checks if item.check_id == "structure_page_provenance")
    assert check.status.value == "warning"
    assert report.overall_status is QualityStatus.PASSED_WITH_WARNINGS


# ------------------------------------------------------------------------ tables


def test_empty_table_is_not_advertised_as_extracted() -> None:
    text = _long_text()
    table = TableRecord(
        table_id="tbl_0001",
        page_number=1,
        reading_order=0,
        row_count=0,
        column_count=0,
        cells=[],
        extraction_status="empty",
        source=ProvenanceSource.DOCLING,
    )
    document = _document(
        pages=[_page(1, text)],
        elements=[_element(text, char_start=0, char_end=len(text))],
        tables=[table],
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    check = next(item for item in report.checks if item.check_id == "tables_structure")
    assert check.status.value == "failed"
    assert report.overall_status is QualityStatus.NEEDS_HUMAN_REVIEW


def test_partial_table_is_reported_as_a_warning() -> None:
    text = _long_text()
    table = TableRecord(
        table_id="tbl_0001",
        page_number=1,
        reading_order=0,
        row_count=2,
        column_count=2,
        cells=[["Model", "BLEU"], ["Transformer", ""]],
        extraction_status="partial",
        text_representation="Model | BLEU\nTransformer | ",
        source=ProvenanceSource.DOCLING,
    )
    document = _document(
        pages=[_page(1, text)],
        elements=[_element(text, char_start=0, char_end=len(text))],
        tables=[table],
    )
    report = _gate().evaluate(document=document, canonical_text=text)
    check = next(item for item in report.checks if item.check_id == "tables_structure")
    assert check.status.value == "warning"


# ------------------------------------------------------------- routing outcomes


def test_fallback_requires_human_review() -> None:
    text = _long_text()
    document = _document(
        pages=[_page(1, text)],
        elements=[_element(text, char_start=0, char_end=len(text))],
        status=ParsingStatus.PARSED_WITH_FALLBACK,
    )
    report = _gate().evaluate(document=document, canonical_text=text, used_fallback=True)
    assert report.overall_status is QualityStatus.NEEDS_HUMAN_REVIEW
    check = next(
        item for item in report.checks if item.check_id == "fallback_parse_requires_review"
    )
    assert check.status.value == "failed"


def test_text_source_with_pages_is_rejected_by_the_contract() -> None:
    """A text document cannot even be constructed with physical pages."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _document(
            pages=[_page(1, "text")],
            elements=[_element("text", char_start=0, char_end=4)],
            source_type=DocumentSourceType.TXT,
            page_count=1,
        )


def test_policy_thresholds_are_recorded_in_the_report() -> None:
    settings = PlatformSettings()
    text = _long_text()
    document = _document(
        pages=[_page(1, text)], elements=[_element(text, char_start=0, char_end=len(text))]
    )
    report = _gate(settings).evaluate(document=document, canonical_text=text)
    assert report.policy.policy_version == settings.quality_gate.policy_version
    assert (
        report.policy.minimum_meaningful_text_chars
        == settings.quality_gate.minimum_meaningful_text_chars
    )
    assert report.quality_policy_version == settings.quality_gate.policy_version


@pytest.mark.parametrize("artifact_kind", ["quality_report", "diagnostics"])
def test_controlled_stop_keeps_diagnostics_directory(tmp_path: Path, artifact_kind: str) -> None:
    """A controlled stop must leave its artefacts behind, not erase them."""
    from research_platform.application.services.artifact_service import ArtifactService
    from research_platform.platform.artifact_store import LocalFileArtifactStore

    store = LocalFileArtifactStore(root=tmp_path / "artifacts")
    artifacts = ArtifactService(store=store, logger=silent_logger())
    metadata = artifacts.store_bytes(
        data=b"{}",
        artifact_id=f"art_{artifact_kind}_0001",
        artifact_type=__import__(
            "research_platform.domain.runs.artifacts", fromlist=["ArtifactType"]
        ).ArtifactType.QUALITY_REPORT
        if artifact_kind == "quality_report"
        else __import__(
            "research_platform.domain.runs.artifacts", fromlist=["ArtifactType"]
        ).ArtifactType.RUN_LOG,
        media_type="application/json",
        document_id=DOCUMENT_ID,
        run_id=RUN_ID,
    )
    assert artifacts.exists_id(metadata.artifact_id)
    assert metadata.uri.startswith(f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/")
