"""Document parsing service: canonical text, offsets and the parsed-document artefact.

Responsibilities, in order:

1. choose the adapter for the source type (Docling for PDF, block parser for TXT);
2. for PDF, fall back to plain text extraction only when the primary parser fails *technically*,
   and label the result as ``parsed_with_fallback``;
3. build the canonical text deterministically and derive every element's character range;
4. assemble and validate the :class:`ParsedDocument`;
5. store the canonical text and the parsed document as immutable artefacts.

Canonicalisation rules (version ``1.0``):

* elements are emitted in parser reading order;
* each element's text is stripped of leading/trailing whitespace;
* elements are separated by ``"\\n\\n"``;
* physical pages are separated by a form feed ``"\\f"`` — a boundary that is explicit in the
  text stream and carries no invented content;
* a text source has no page boundaries at all;
* nothing else is rewritten: no case folding, no punctuation changes, no de-hyphenation.

Character ranges therefore point *by construction* into the canonical text: they are
computed while it is assembled, never guessed afterwards.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from research_platform.application.ports.document_pipeline import (
    DocumentInspectorPort,
    DocumentParserPort,
    ParsedPayload,
    ParseRequest,
)
from research_platform.application.ports.logging import LoggerPort
from research_platform.application.services.artifact_ids import run_artifact_id
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.document_errors import DocumentParsingError
from research_platform.application.services.document_ingestion import IngestedSource
from research_platform.application.services.language_detection import LanguageDetector
from research_platform.config import DocumentParsingSettings
from research_platform.domain.documents.elements import (
    CANONICALIZATION_VERSION,
    DocumentElement,
    ElementType,
    PageRecord,
    PageStatus,
    ParsedSection,
    ProvenanceSource,
)
from research_platform.domain.documents.identity import DocumentSourceType
from research_platform.domain.documents.parsed import (
    PARSED_DOCUMENT_CONTRACT_VERSION,
    CanonicalTextInfo,
    ParsedDocument,
    ParserProvenance,
    ParsingStatus,
)
from research_platform.domain.runs.artifacts import ArtifactType

__all__ = ["PAGE_SEPARATOR", "DocumentParsingService", "ParsedOutcome"]

PAGE_SEPARATOR = "\f"
ELEMENT_SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class ParsedOutcome:
    """Result of the parsing stage."""

    document: ParsedDocument
    canonical_text: str
    used_fallback: bool
    warnings: tuple[str, ...] = ()
    parsed_document_uri: str = ""


class DocumentParsingService:
    """Parse an ingested source into the platform document model."""

    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        settings: DocumentParsingSettings,
        logger: LoggerPort,
        pdf_parser: DocumentParserPort,
        txt_parser: DocumentParserPort,
        inspector: DocumentInspectorPort | None = None,
        language_detector: LanguageDetector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._artifacts = artifact_service
        self._settings = settings
        self._logger = logger
        self._pdf_parser = pdf_parser
        self._txt_parser = txt_parser
        self._inspector = inspector
        self._language_detector = language_detector or LanguageDetector()
        self._clock = clock

    def _now(self) -> datetime:
        if self._clock is not None:
            assert self._clock is not None
            return self._clock().astimezone(UTC)
        return datetime.now(UTC)

    # -------------------------------------------------------------------- public

    def parse(
        self,
        *,
        ingested: IngestedSource,
        correlation_id: str | None = None,
    ) -> ParsedOutcome:
        """Parse the stored source of an ingested document.

        Raises:
            DocumentParsingError: the primary parser failed and no fallback is allowed or
                the fallback produced nothing usable.
        """
        manifest = ingested.manifest
        log = self._logger.bind(
            run_id=manifest.run_id,
            document_id=manifest.document_id,
            correlation_id=correlation_id,
        )
        request = ParseRequest(
            data=ingested.data,
            document_id=manifest.document_id,
            run_id=manifest.run_id,
            file_name=manifest.source_file_name,
            language=manifest.language.value,
        )

        warnings: list[str] = []
        used_fallback = False
        try:
            if manifest.source_type is DocumentSourceType.TXT:
                payload = self._parse_txt(request)
            else:
                payload = self._pdf_parser.parse_pdf(request)
        except Exception as exc:
            fallback = self._fallback(request, ingested)
            if fallback is None:
                raise DocumentParsingError(
                    f"parsing failed: {type(exc).__name__}: {exc}",
                    correlation_id=correlation_id or "corr_unset",
                ) from exc
            payload, fallback_warnings = fallback
            used_fallback = True
            warnings.extend(fallback_warnings)
            warnings.append(
                f"primary parser failed ({type(exc).__name__}); plain-text fallback used"
            )
            log.event(
                "stage_failed",
                level="WARNING",
                stage="parsing",
                error_code="primary_parser_failed",
                detail=str(exc)[:200],
            )

        elements, sections, canonical_text = self._canonicalize(payload)
        language = manifest.language.value
        if language == "unknown":
            detection = self._language_detector.detect(canonical_text)
            language = detection.language

        canonical_metadata = self._artifacts.store_bytes(
            data=canonical_text.encode("utf-8"),
            artifact_id=run_artifact_id(
                "art_canonical", document_id=manifest.document_id, run_id=manifest.run_id
            ),
            artifact_type=ArtifactType.PARSED_DOCUMENT,
            media_type="text/plain",
            document_id=manifest.document_id,
            run_id=manifest.run_id,
            schema_version=CANONICALIZATION_VERSION,
            correlation_id=correlation_id,
        )

        parsing_status = payload.status
        if used_fallback and parsing_status is ParsingStatus.PARSED:
            parsing_status = ParsingStatus.PARSED_WITH_FALLBACK

        document = ParsedDocument(
            document_id=manifest.document_id,
            run_id=manifest.run_id,
            source_sha256=manifest.source_sha256,
            source_type=manifest.source_type,
            language=language,
            parser=payload.parser,
            parsing_status=parsing_status,
            page_count=payload.page_count,
            pages=list(payload.pages),
            sections=list(sections),
            elements=list(elements),
            tables=list(payload.tables),
            figures=list(payload.figures),
            canonical_text=CanonicalTextInfo(
                artifact_uri=canonical_metadata.uri,
                canonicalization_version=CANONICALIZATION_VERSION,
                char_count=len(canonical_text),
                page_separator=PAGE_SEPARATOR,
            ),
            source_artifact_uri=manifest.source_artifact_uri,
            created_at=self._now(),
        )

        warnings.extend(payload.warnings)
        stored_document = self._artifacts.store_bytes(
            data=document.model_dump_json(indent=2).encode("utf-8"),
            artifact_id=run_artifact_id(
                "art_parsed", document_id=manifest.document_id, run_id=manifest.run_id
            ),
            artifact_type=ArtifactType.PARSED_DOCUMENT,
            media_type="application/json",
            document_id=manifest.document_id,
            run_id=manifest.run_id,
            schema_version=PARSED_DOCUMENT_CONTRACT_VERSION,
            correlation_id=correlation_id,
        )
        log.event(
            "stage_completed",
            stage="parsing",
            page_count=document.page_count,
            element_count=len(document.elements),
            table_count=len(document.tables),
            figure_count=len(document.figures),
            canonical_chars=len(canonical_text),
            parser=document.parser.parser_name,
            fallback=used_fallback,
        )
        return ParsedOutcome(
            document=document,
            canonical_text=canonical_text,
            used_fallback=used_fallback,
            warnings=tuple(dict.fromkeys(warnings)),
            parsed_document_uri=stored_document.uri,
        )

    # ------------------------------------------------------------------ internals

    def _parse_txt(self, request: ParseRequest) -> ParsedPayload:
        """Text parsing receives decoded UTF-8 bytes; decoding happened at ingestion."""
        text = request.data.decode("utf-8", errors="strict")
        return self._txt_parser.parse_txt(
            ParseRequest(
                data=text.encode("utf-8"),
                document_id=request.document_id,
                run_id=request.run_id,
                file_name=request.file_name,
                language=request.language,
            )
        )

    def _fallback(
        self, request: ParseRequest, ingested: IngestedSource
    ) -> tuple[ParsedPayload, list[str]] | None:
        """Plain-text fallback through the inspector, when configured and possible."""
        if self._settings.fallback_pdf_parser != "pymupdf" or self._inspector is None:
            return None
        if ingested.manifest.source_type is not DocumentSourceType.PDF:
            return None
        try:
            extracted = self._inspector.extract_text_fallback(request.data)
        except ValueError as exc:
            raise DocumentParsingError(
                f"fallback extraction also failed: {exc}",
                correlation_id="corr_unset",
            ) from exc
        if extracted.total_chars == 0:
            return None

        pages: list[PageRecord] = []
        elements: list[DocumentElement] = []
        order = 0
        for page_number, text in extracted.pages:
            body = text.strip()
            digest = hashlib.sha256(
                f"{request.document_id}|{page_number}|{body[:200]}".encode()
            ).hexdigest()[:16]
            if body:
                elements.append(
                    DocumentElement(
                        block_id=f"blk_{digest}",
                        element_type=ElementType.PARAGRAPH,
                        text=body,
                        page_number=page_number,
                        reading_order=order,
                        source=ProvenanceSource.PYMUPDF_FALLBACK,
                    )
                )
                order += 1
            pages.append(
                PageRecord(
                    page_number=page_number,
                    status=PageStatus.PARSED if body else PageStatus.EMPTY,
                    text=body,
                    char_count=len(body),
                    block_ids=[f"blk_{digest}"] if body else [],
                )
            )
        payload = ParsedPayload(
            parser=ParserProvenance(
                parser_name="pymupdf",
                parser_version=_pymupdf_version(),
                pipeline_version="text-fallback/1",
                parser_options_hash=hashlib.sha256(b"pymupdf-text-fallback-v1").hexdigest()[:16],
                backend_name="pymupdf",
                table_extraction_enabled=False,
                ocr_enabled=False,
            ),
            status=ParsingStatus.PARSED_WITH_FALLBACK,
            pages=tuple(pages),
            sections=(),
            elements=tuple(elements),
            tables=(),
            figures=(),
            warnings=(
                "fallback extraction provides plain text only: no layout, no tables, no "
                "section structure",
            ),
            page_count=extracted.page_count,
        )
        return payload, list(extracted.warnings)

    def _canonicalize(
        self, payload: ParsedPayload
    ) -> tuple[list[DocumentElement], tuple[ParsedSection, ...], str]:
        """Build canonical text and derive exact character ranges for every element."""
        parts: list[str] = []
        cursor = 0
        previous_page: int | None = None
        elements: list[DocumentElement] = []
        canonical: list[str] = []

        for element in sorted(payload.elements, key=lambda item: item.reading_order):
            text = element.text.strip()
            if not text:
                continue
            if (
                element.page_number is not None
                and previous_page is not None
                and element.page_number != previous_page
            ):
                canonical.append(PAGE_SEPARATOR)
                cursor += len(PAGE_SEPARATOR)
            start = cursor
            canonical.append(text)
            cursor += len(text)
            canonical.append(ELEMENT_SEPARATOR)
            cursor += len(ELEMENT_SEPARATOR)
            previous_page = element.page_number
            elements.append(
                DocumentElement.model_validate(
                    {
                        **element.model_dump(),
                        "text": text,
                        "char_start": start,
                        "char_end": start + len(text),
                    }
                )
            )

        text = "".join(canonical)
        if text.endswith(ELEMENT_SEPARATOR):
            text = text[: -len(ELEMENT_SEPARATOR)]
        del parts
        return elements, payload.sections, text


def _pymupdf_version() -> str:
    import importlib.metadata as metadata

    try:
        return metadata.version("pymupdf")
    except metadata.PackageNotFoundError:  # pragma: no cover
        return "unavailable"
