"""Iteration 2: document identity, ingestion, decoding, parsing and the Quality Gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.fixtures.logging import silent_logger

from research_platform.application.ports.document_pipeline import (
    DocumentInspection,
    PageInspection,
    ParsedPayload,
    ParseRequest,
)
from research_platform.application.services.document_errors import (
    DocumentIngestionError,
)
from research_platform.application.services.document_ingestion import (
    DocumentIngestionService,
)
from research_platform.application.services.language_detection import LanguageDetector
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
from research_platform.domain.documents.identity import (
    DocumentIdentity,
    DocumentSourceType,
    document_id_for,
)
from research_platform.domain.documents.parsed import ParserProvenance, ParsingStatus
from research_platform.platform.document_parsing.txt.decoder import TxtDecoder

PDF_SHA = "a" * 64
TXT_SHA = "b" * 64


# ------------------------------------------------------------------- identity


def test_document_id_is_stable_for_the_same_bytes() -> None:
    first = document_id_for(PDF_SHA, DocumentSourceType.PDF)
    second = document_id_for(PDF_SHA, DocumentSourceType.PDF)
    assert first == second
    assert first.startswith("doc_")
    assert len(first) == 4 + 24


def test_document_id_depends_on_source_type() -> None:
    assert document_id_for(PDF_SHA, DocumentSourceType.PDF) != document_id_for(
        PDF_SHA, DocumentSourceType.TXT
    )


def test_document_id_rejects_a_malformed_hash() -> None:
    with pytest.raises(ValueError):
        document_id_for("not-a-hash", DocumentSourceType.PDF)


def test_identity_model_rejects_a_foreign_id() -> None:
    with pytest.raises(ValueError):
        DocumentIdentity(
            document_id="doc_000000000000000000000000",
            source_sha256=PDF_SHA,
            source_type=DocumentSourceType.PDF,
        )


# ------------------------------------------------------------------- decoding


@pytest.mark.parametrize(
    ("payload", "encoding"),
    [
        (b"plain ascii text", "utf-8"),
        ("текст на русском".encode(), "utf-8"),
        ("\ufeffbom prefixed".encode("utf-8-sig"), "utf-8"),
        ("кириллица cp1251".encode("cp1251"), "cp1251"),
    ],
)
def test_decoder_recognises_encodings(payload: bytes, encoding: str) -> None:
    decoded = TxtDecoder().decode(payload)
    assert decoded.encoding == encoding
    assert decoded.encoding_uncertain is False
    assert decoded.replacement_count == 0


def test_decoder_survives_a_bom_less_utf16_file() -> None:
    decoded = TxtDecoder().decode("utf16 text".encode("utf-16-le"))
    assert decoded.encoding == "utf-16-le"
    assert decoded.text.strip() == "utf16 text"


# ----------------------------------------------------------- language detection


def test_language_detection_ru_and_en() -> None:
    detector = LanguageDetector()
    assert detector.detect("этот текст написан по-русски " * 20).language == "ru"
    assert detector.detect("this text is written in english " * 20).language == "en"


def test_language_detection_of_short_text_is_unknown() -> None:
    detection = LanguageDetector().detect("слишком коротко")
    assert detection.language == "unknown"
    assert detection.warnings


# ------------------------------------------------------------------ ingestion


@dataclass
class _Reader:
    data: bytes
    name: str = "source.txt"

    def read(self, path: str, *, max_size_bytes: int):
        from research_platform.application.ports.document_pipeline import SourceFile

        if len(self.data) > max_size_bytes:
            raise DocumentIngestionError("file_too_large", "too large")
        return SourceFile(
            data=self.data,
            size_bytes=len(self.data),
            file_name=self.name,
            extension=Path(self.name).suffix.lower(),
        )

    def fingerprint(self, path: str) -> tuple[int, int]:
        return len(self.data), 0


def _ingestion(reader, settings=None):
    import tempfile

    from research_platform.application.services.artifact_service import ArtifactService
    from research_platform.platform.artifact_store import LocalFileArtifactStore

    store = LocalFileArtifactStore(root=Path(tempfile.mkdtemp(prefix="ing-")) / "artifacts")
    service = ArtifactService(store=store, logger=silent_logger())
    return (
        DocumentIngestionService(
            reader=reader,
            artifact_service=service,
            settings=(settings or PlatformSettings()).document_ingestion,
            logger=silent_logger(),
            decoder=TxtDecoder(),
        ),
        service,
    )


def test_ingestion_rejects_an_unsupported_extension() -> None:
    service, _ = _ingestion(_Reader(b"data", name="file.docx"))
    with pytest.raises(DocumentIngestionError) as excinfo:
        service.ingest(input_path="file.docx", run_id="run_1")
    assert excinfo.value.code == "unsupported_format"


def test_ingestion_rejects_a_renamed_binary_file() -> None:
    service, _ = _ingestion(_Reader(b"not a pdf", name="file.pdf"))
    with pytest.raises(DocumentIngestionError) as excinfo:
        service.ingest(input_path="file.pdf", run_id="run_1")
    assert excinfo.value.code == "invalid_file_signature"


def test_ingestion_stores_the_original_and_a_manifest() -> None:
    payload = "заметка о конвейере\n\nвторой абзац текста".encode()
    service, artifacts = _ingestion(_Reader(payload, name="note.txt"))
    ingested = service.ingest(input_path="note.txt", run_id="run_1")
    assert ingested.manifest.source_sha256
    assert ingested.manifest.original_encoding == "utf-8"
    stored = artifacts.list_for_run("run_1")
    assert len(stored) == 2  # original + manifest
    assert artifacts.read_by_id(stored[0].artifact_id) == payload


# ---------------------------------------------------------------- quality gate


def _parser_provenance(name: str = "test-parser") -> ParserProvenance:
    return ParserProvenance(
        parser_name=name,
        parser_version="1.0",
        pipeline_version="test/1",
        parser_options_hash="0123456789abcdef",
        backend_name="test",
        table_extraction_enabled=True,
        ocr_enabled=False,
    )


def _document(
    *,
    pages,
    elements,
    tables=(),
    page_count=None,
    status=ParsingStatus.PARSED,
    source_type=DocumentSourceType.PDF,
):
    from research_platform.domain.documents.parsed import CanonicalTextInfo, ParsedDocument

    return ParsedDocument(
        document_id="doc_" + "a" * 24,
        run_id="run_test_0001",
        source_sha256="a" * 64,
        source_type=source_type,
        language="en",
        parser=_parser_provenance(),
        parsing_status=status,
        page_count=page_count if page_count is not None else len(pages) or None,
        pages=list(pages),
        sections=[],
        elements=list(elements),
        tables=list(tables),
        figures=[],
        canonical_text=CanonicalTextInfo(
            artifact_uri=(
                "artifact://documents/doc_" + "a" * 24 + "/runs/run_test_0001/"
                "parsed_document/art_canonical_0001"
            ),
            canonicalization_version="1.0",
            char_count=0,
            page_separator="\f",
        ),
        source_artifact_uri=(
            "artifact://documents/doc_" + "a" * 24 + "/runs/run_test_0001/"
            "original_document/art_source_0001"
        ),
        created_at="2026-11-14T12:00:00Z",
    )


def _gate(settings: PlatformSettings | None = None) -> QualityGateService:
    return QualityGateService(
        settings=(settings or PlatformSettings()).quality_gate, logger=silent_logger()
    )


def _page(number: int, text: str, status=PageStatus.PARSED) -> PageRecord:
    body = text
    return PageRecord(page_number=number, status=status, text=body, char_count=len(body))


def _element(text: str, *, page: int = 1, order: int = 0) -> DocumentElement:
    return DocumentElement(
        block_id=f"blk_test_{page:02d}{order:02d}",
        element_type=ElementType.PARAGRAPH,
        text=text,
        page_number=page,
        reading_order=order,
        source=ProvenanceSource.DOCLING,
    )


def test_gate_passes_a_clean_document() -> None:
    text = "A clean paragraph with enough characters to be meaningful. " * 3
    document = _document(pages=[_page(1, text)], elements=[_element(text)])
    report = _gate().evaluate(
        document=document,
        canonical_text=text,
        inspection=DocumentInspection(
            page_count=1,
            pages=(PageInspection(page_number=1, width=595, height=842, has_native_text=True),),
        ),
    )
    assert report.overall_status.value == "passed"
    assert report.errors_count == 0


def test_gate_detects_a_page_count_mismatch() -> None:
    text = "text " * 40
    document = _document(pages=[_page(1, text)], elements=[_element(text)])
    report = _gate().evaluate(
        document=document,
        canonical_text=text,
        inspection=DocumentInspection(page_count=3),
    )
    assert report.overall_status.value == "failed"
    check = next(
        item for item in report.checks if item.check_id == "structure_page_count_matches_source"
    )
    assert check.status.value == "failed"


def test_gate_requires_ocr_for_an_image_only_page() -> None:
    document = _document(
        pages=[_page(1, "", status=PageStatus.EMPTY)],
        elements=[],
    )
    report = _gate().evaluate(
        document=document,
        canonical_text="",
        inspection=DocumentInspection(
            page_count=1,
            pages=(
                PageInspection(
                    page_number=1,
                    width=595,
                    height=842,
                    has_native_text=False,
                    image_count=1,
                    image_area_ratio=0.95,
                ),
            ),
        ),
    )
    assert report.overall_status.value in {"requires_ocr", "needs_human_review"}
    assert report.ocr_required_pages == [1]


def test_gate_fails_when_no_text_was_extracted() -> None:
    document = _document(pages=[_page(1, "", status=PageStatus.EMPTY)], elements=[])
    report = _gate().evaluate(
        document=document,
        canonical_text="",
        inspection=DocumentInspection(page_count=1),
    )
    assert report.overall_status.value == "failed"
    assert any(item.check_id == "text_extracted" for item in report.checks)


def test_gate_flags_a_broken_table() -> None:
    text = "table " * 60
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
    document = _document(pages=[_page(1, text)], elements=[_element(text)], tables=[table])
    report = _gate().evaluate(
        document=document, canonical_text=text, inspection=DocumentInspection(page_count=1)
    )
    assert report.overall_status.value in {"needs_human_review", "passed_with_warnings"}
    assert any(item.check_id == "tables_structure" for item in report.checks)


def test_gate_treats_a_fallback_parse_as_needing_review() -> None:
    text = "fallback text " * 30
    document = _document(
        pages=[_page(1, text)], elements=[_element(text)], status=ParsingStatus.PARSED_WITH_FALLBACK
    )
    report = _gate().evaluate(
        document=document,
        canonical_text=text,
        inspection=DocumentInspection(page_count=1),
        used_fallback=True,
    )
    assert report.overall_status.value == "needs_human_review"


def test_gate_rejects_text_source_pagination() -> None:
    document = _document(pages=[], elements=[], source_type=DocumentSourceType.TXT, page_count=None)
    report = _gate().evaluate(document=document, canonical_text="")
    assert report.overall_status.value == "failed"


# ------------------------------------------------------------------- CLI wiring


def test_cli_document_commands_exist() -> None:
    from research_platform.cli import build_parser

    parser = build_parser()
    subcommands = {
        action.dest: sorted(action.choices)  # type: ignore[union-attr]
        for action in parser._actions
        if getattr(action, "choices", None)
    }
    assert "document" in subcommands["command"]


def test_document_process_and_report_round_trip(tmp_path: Path) -> None:
    from research_platform.cli import EXIT_OK, main

    source = tmp_path / "note.txt"
    source.write_text(
        "Deterministic Pipeline\n\nThis note describes how the platform parses text.\n"
        "It keeps provenance for every element.\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    assert (
        main(["document", "process", "--input", str(source), "--project-root", str(project)])
        == EXIT_OK
    )

    reports = list((project / "data" / "artifacts" / "metadata").glob("*.json"))
    assert reports
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in reports]
    report_meta = next(item for item in payloads if item["artifact_type"] == "quality_report")
    run_id = report_meta["run_id"]
    assert (
        main(["document", "report", "--run-id", run_id, "--project-root", str(project)]) == EXIT_OK
    )


# --------------------------------------------------------------- real Docling


@pytest.fixture(scope="module")
def digital_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import pymupdf

    directory = tmp_path_factory.mktemp("it2-pdf")
    path = directory / "digital.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 90), "A Synthetic Research Article", fontsize=16)
    page.insert_text((72, 130), "Abstract", fontsize=12)
    page.insert_text(
        (72, 160),
        "This article describes a deterministic extraction pipeline for scientific documents.",
        fontsize=10,
    )
    document.save(path)
    document.close()
    return path


@pytest.mark.parsing
def test_real_docling_parses_a_digital_pdf(digital_pdf: Path) -> None:
    from research_platform.platform.document_parsing.docling.pdf_parser import (
        DoclingPdfParser,
    )

    payload: ParsedPayload = DoclingPdfParser().parse_pdf(
        ParseRequest(
            data=digital_pdf.read_bytes(),
            document_id="doc_" + "b" * 24,
            run_id="run_test_0001",
            file_name="digital.pdf",
        )
    )
    assert payload.page_count == 1
    assert payload.pages[0].page_number == 1
    assert payload.elements, "Docling extracted no elements"
    assert all(element.page_number == 1 for element in payload.elements)
    assert payload.parser.parser_name == "docling"
    assert payload.parser.ocr_enabled is False
    bbox = payload.elements[0].bbox
    assert bbox is not None
    assert bbox.unit.value == "pt"
