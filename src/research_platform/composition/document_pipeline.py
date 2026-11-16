"""Composition of the document pipeline.

The composition root builds adapters and services; nothing here contains policy.  Two facts
are worth stating explicitly:

* the Docling parser is constructed with OCR disabled, and the constructor itself refuses
  ``do_ocr=True`` — an accidental configuration cannot enable OCR without the owner;
* the PyMuPDF inspector is shared by ingestion (page count, encryption, corruption) and by
  parsing (fallback), so both stages look at the same facts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from research_platform.application.ports.document_pipeline import (
    DocumentInspectorPort,
    DocumentParserPort,
)
from research_platform.application.ports.logging import LoggerPort
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.document_ingestion import DocumentIngestionService
from research_platform.application.services.document_parsing import DocumentParsingService
from research_platform.application.services.quality_gate import QualityGateService
from research_platform.application.services.run_manager import RunManager
from research_platform.config import PlatformSettings
from research_platform.platform.document_parsing.docling.options import DoclingParserOptions
from research_platform.platform.document_parsing.docling.pdf_parser import DoclingPdfParser
from research_platform.platform.document_parsing.pymupdf.pdf_inspector import PyMuPDFInspector
from research_platform.platform.document_parsing.txt.decoder import TxtDecoder
from research_platform.platform.document_parsing.txt.parser import TxtBlockParser
from research_platform.platform.documents.file_reader import LocalSourceReader
from research_platform.workflows.document_workflow import (
    DocumentRuntimeCache,
    DocumentWorkflow,
    DocumentWorkflowDependencies,
    build_document_workflow,
)

__all__ = ["DocumentPipeline", "build_document_pipeline"]


@dataclass(frozen=True, slots=True)
class DocumentPipeline:
    """Document components assembled for one application context."""

    ingestion: DocumentIngestionService
    parsing: DocumentParsingService
    quality_gate: QualityGateService
    inspector: DocumentInspectorPort
    pdf_parser: DocumentParserPort
    txt_parser: DocumentParserPort
    reader: LocalSourceReader

    def workflow(self, dependencies: DocumentWorkflowDependencies) -> DocumentWorkflow:
        """Build the LangGraph workflow for one run."""
        return build_document_workflow(dependencies)


def build_document_pipeline(
    *,
    settings: PlatformSettings,
    artifacts: ArtifactService,
    run_manager: RunManager,
    logger: LoggerPort,
    clock: Callable[[], datetime] | None = None,
) -> DocumentPipeline:
    """Create every document component from settings."""
    inspector = PyMuPDFInspector()
    pdf_options = DoclingParserOptions(
        do_ocr=settings.document_parsing.docling.do_ocr,
        do_table_structure=settings.document_parsing.docling.do_table_structure,
        generate_picture_images=settings.document_parsing.docling.generate_picture_images,
        do_cell_matching=settings.document_parsing.docling.do_cell_matching,
    )
    pdf_parser = DoclingPdfParser(options=pdf_options)
    txt_parser = TxtBlockParser()
    decoder = TxtDecoder()

    ingestion = DocumentIngestionService(
        reader=LocalSourceReader(),
        artifact_service=artifacts,
        settings=settings.document_ingestion,
        logger=logger,
        inspector=inspector,
        decoder=decoder,
        clock=clock,
    )
    parsing = DocumentParsingService(
        artifact_service=artifacts,
        settings=settings.document_parsing,
        logger=logger,
        pdf_parser=pdf_parser,
        txt_parser=txt_parser,
        inspector=inspector,
        clock=clock,
    )
    quality_gate = QualityGateService(settings=settings.quality_gate, logger=logger, clock=clock)
    return DocumentPipeline(
        ingestion=ingestion,
        parsing=parsing,
        quality_gate=quality_gate,
        inspector=inspector,
        pdf_parser=pdf_parser,
        txt_parser=txt_parser,
        reader=LocalSourceReader(),
    )


def document_dependencies(
    *,
    settings: PlatformSettings,
    pipeline: DocumentPipeline,
    run_manager: RunManager,
    artifacts: ArtifactService,
    logger: LoggerPort,
) -> DocumentWorkflowDependencies:
    """Dependency bundle for one workflow execution (fresh cache per run)."""
    return DocumentWorkflowDependencies(
        settings=settings,
        run_manager=run_manager,
        artifacts=artifacts,
        ingestion=pipeline.ingestion,
        parsing=pipeline.parsing,
        quality_gate=pipeline.quality_gate,
        inspector=pipeline.inspector,
        logger=logger,
        cache=DocumentRuntimeCache(),
    )
