"""Document ingestion: accept, identify, store and describe a source document.

The service is the only place that turns a user-supplied file into platform state.  The
order is deliberate, because every later stage depends on it:

1. the extension must be allowed (anything else is ``unsupported_format``);
2. the adapter reads the bytes with size limits applied *before* loading;
3. the file signature is checked against the extension (a renamed file is rejected);
4. the content hash and the stable ``document_id`` are derived from the bytes;
5. the original bytes are stored as an immutable artefact and re-hashed;
6. the source file is fingerprinted again — if it changed meanwhile, ingestion fails with
   ``source_changed_during_ingestion`` rather than mixing two versions;
7. the ingestion manifest is stored as its own artefact.

The parser never reads the user's directory: it receives the stored bytes of the immutable
copy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from research_platform.application.ports.document_pipeline import (
    DocumentInspection,
    DocumentInspectorPort,
    SourceReaderPort,
    TextDecoderPort,
)
from research_platform.application.ports.logging import LoggerPort
from research_platform.application.services.artifact_ids import run_artifact_id
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.document_errors import DocumentIngestionError
from research_platform.application.services.language_detection import LanguageDetector
from research_platform.config import DocumentIngestionSettings
from research_platform.domain.documents.identity import (
    DocumentIdentity,
    DocumentSourceType,
)
from research_platform.domain.documents.models import (
    DocumentLanguage,
    SourceDocumentManifest,
    SourceMediaType,
)
from research_platform.domain.runs.artifacts import ArtifactType

__all__ = ["DocumentIngestionService", "IngestedSource"]

_PDF_SIGNATURE = b"%PDF-"


@dataclass(frozen=True, slots=True)
class IngestedSource:
    """Everything later stages need about the received document."""

    manifest: SourceDocumentManifest
    identity: DocumentIdentity
    data: bytes
    inspection: DocumentInspection | None
    decoded_text: str | None = None


class DocumentIngestionService:
    """Validate, identify and store one source document."""

    def __init__(
        self,
        *,
        reader: SourceReaderPort,
        artifact_service: ArtifactService,
        settings: DocumentIngestionSettings,
        logger: LoggerPort,
        inspector: DocumentInspectorPort | None = None,
        decoder: TextDecoderPort,
        language_detector: LanguageDetector | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._reader = reader
        self._artifacts = artifact_service
        self._settings = settings
        self._logger = logger
        self._inspector = inspector
        self._decoder = decoder
        self._language_detector = language_detector or LanguageDetector()
        self._clock = clock

    def _now(self) -> datetime:
        if self._clock is not None:
            assert self._clock is not None
            return self._clock().astimezone(UTC)
        return datetime.now(UTC)

    def validate_input(self, input_path: str) -> None:
        """Cheap pre-checks before a run is created: extension, existence, size.

        Raises:
            DocumentIngestionError: the source is not acceptable.
        """
        probe = self._reader.read(
            input_path, max_size_bytes=self._max_size_hint()
        )  # single read keeps size/existence checks honest
        extension = probe.extension
        if extension not in self._settings.allowed_extensions:
            raise DocumentIngestionError(
                "unsupported_format",
                f"extension {extension or '<none>'!r} is not accepted; allowed: "
                f"{', '.join(self._settings.allowed_extensions)}",
            )

    def probe_identity(self, input_path: str) -> DocumentIdentity:
        """Resolve the document identity without storing anything (used to name the run).

        Raises:
            DocumentIngestionError: the extension is not supported.
        """
        probe = self._reader.read(input_path, max_size_bytes=self._max_size_hint())
        if probe.extension not in self._settings.allowed_extensions:
            raise DocumentIngestionError(
                "unsupported_format",
                f"extension {probe.extension or '<none>'!r} is not accepted",
            )
        source_type = DocumentSourceType(probe.extension.lstrip("."))
        return DocumentIdentity.derive(hashlib.sha256(probe.data).hexdigest(), source_type)

    def ingest(
        self,
        *,
        input_path: str,
        run_id: str,
        correlation_id: str | None = None,
    ) -> IngestedSource:
        """Run the ingestion stage for one file.

        Raises:
            DocumentIngestionError: with a normalised code describing the first problem.
        """
        log = self._logger.bind(run_id=run_id, correlation_id=correlation_id)
        source = self._reader.read(input_path, max_size_bytes=self._max_size_hint())

        extension = source.extension
        if extension not in self._settings.allowed_extensions:
            raise DocumentIngestionError(
                "unsupported_format",
                f"extension {extension or '<none>'!r} is not accepted; allowed: "
                f"{', '.join(self._settings.allowed_extensions)}",
                correlation_id=correlation_id or "corr_unset",
            )
        source_type = DocumentSourceType(extension.lstrip("."))
        limit = self._settings.max_size_bytes[extension]
        if source.size_bytes > limit:
            raise DocumentIngestionError(
                "file_too_large",
                f"the {source_type.value.upper()} file is {source.size_bytes} bytes, above "
                f"the {limit}-byte limit",
                correlation_id=correlation_id or "corr_unset",
            )

        self._check_signature(source.data, source_type, correlation_id=correlation_id)
        sha256 = hashlib.sha256(source.data).hexdigest()
        identity = DocumentIdentity.derive(sha256, source_type)
        self._check_identity_collision(identity, correlation_id=correlation_id)

        inspection: DocumentInspection | None = None
        if source_type is DocumentSourceType.PDF:
            inspection = self._inspect(source.data, correlation_id=correlation_id)

        decoded_text: str | None = None
        encoding: str | None = None
        encoding_uncertain = False
        language = DocumentLanguage.UNKNOWN
        method = "not_detected"
        if source_type is DocumentSourceType.TXT:
            decoded = self._decoder.decode(source.data)
            decoded_text = decoded.text
            encoding = decoded.encoding
            encoding_uncertain = decoded.encoding_uncertain
            detection = self._language_detector.detect(decoded.text)
            language = DocumentLanguage(detection.language)
            method = detection.method

        metadata = self._artifacts.store_bytes(
            data=source.data,
            artifact_id=run_artifact_id(
                "art_source", document_id=identity.document_id, run_id=run_id
            ),
            artifact_type=ArtifactType.ORIGINAL_DOCUMENT,
            media_type=SourceMediaType(source_type.media_type).value,
            document_id=identity.document_id,
            run_id=run_id,
            correlation_id=correlation_id,
        )
        if metadata.sha256 != sha256:
            raise DocumentIngestionError(
                "source_integrity_error",
                f"stored artefact hash {metadata.sha256[:12]}… differs from the source hash "
                f"{sha256[:12]}…",
                correlation_id=correlation_id or "corr_unset",
            )
        if self._settings.verify_source_unchanged and not self._verify_source(input_path, sha256):
            raise DocumentIngestionError(
                "source_changed_during_ingestion",
                "the input file changed while it was being ingested; nothing was parsed",
                correlation_id=correlation_id or "corr_unset",
            )

        manifest = SourceDocumentManifest(
            document_id=identity.document_id,
            run_id=run_id,
            source_file_name=source.file_name,
            source_type=source_type,
            source_media_type=SourceMediaType(source_type.media_type),
            source_sha256=sha256,
            source_size_bytes=source.size_bytes,
            source_artifact_uri=metadata.uri,
            original_encoding=encoding,
            encoding_uncertain=encoding_uncertain,
            language=language,
            language_detection_method=method,
            created_at=self._now(),
            processing_status="ingested",
        )
        self._artifacts.store_bytes(
            data=manifest.model_dump_json(indent=2).encode("utf-8"),
            artifact_id=run_artifact_id(
                "art_manifest", document_id=identity.document_id, run_id=run_id
            ),
            artifact_type=ArtifactType.HANDOFF_PACKAGE,
            media_type="application/json",
            document_id=identity.document_id,
            run_id=run_id,
            schema_version=manifest.document_contract_version,
            correlation_id=correlation_id,
        )
        log.event(
            "artifact_created",
            stage="registration",
            document_id=identity.document_id,
            source_type=source_type.value,
            size_bytes=source.size_bytes,
            sha256=sha256,
        )
        return IngestedSource(
            manifest=manifest,
            identity=identity,
            data=source.data,
            inspection=inspection,
            decoded_text=decoded_text,
        )

    # ------------------------------------------------------------------ helpers

    def _max_size_hint(self) -> int:
        return max(self._settings.max_size_bytes.values())

    def _check_signature(
        self, data: bytes, source_type: DocumentSourceType, *, correlation_id: str | None
    ) -> None:
        if source_type is DocumentSourceType.PDF:
            if not data.lstrip()[:5].startswith(_PDF_SIGNATURE):
                raise DocumentIngestionError(
                    "invalid_file_signature",
                    "the file is named .pdf but does not start with a PDF signature",
                    correlation_id=correlation_id or "corr_unset",
                )
            return
        if b"\x00" in data[:4096]:
            raise DocumentIngestionError(
                "invalid_file_signature",
                "the file is named .txt but contains binary NUL bytes",
                correlation_id=correlation_id or "corr_unset",
            )

    def _inspect(self, data: bytes, *, correlation_id: str | None) -> DocumentInspection:
        if self._inspector is None:  # pragma: no cover - injected by the bootstrap
            raise DocumentIngestionError(
                "document_ingestion_failed",
                "no PDF inspector is configured",
                correlation_id=correlation_id or "corr_unset",
            )
        try:
            inspection = self._inspector.inspect_pdf(data)
        except ValueError as exc:
            raise DocumentIngestionError(
                "pdf_corrupt", str(exc), correlation_id=correlation_id or "corr_unset"
            ) from exc
        if inspection.is_encrypted:
            raise DocumentIngestionError(
                "pdf_encrypted",
                "the PDF is encrypted; no credentials were provided",
                correlation_id=correlation_id or "corr_unset",
            )
        if inspection.is_corrupt:
            raise DocumentIngestionError(
                "pdf_corrupt",
                "the PDF structure is damaged",
                correlation_id=correlation_id or "corr_unset",
            )
        if inspection.page_count == 0:
            raise DocumentIngestionError(
                "pdf_empty",
                "the PDF reports zero pages",
                correlation_id=correlation_id or "corr_unset",
            )
        if inspection.page_count > self._settings.max_pdf_pages:
            raise DocumentIngestionError(
                "pdf_page_limit_exceeded",
                f"the PDF has {inspection.page_count} pages, above the "
                f"{self._settings.max_pdf_pages}-page limit",
                correlation_id=correlation_id or "corr_unset",
            )
        return inspection

    def _check_identity_collision(
        self, identity: DocumentIdentity, *, correlation_id: str | None
    ) -> None:
        """Refuse a short-id collision: same id must mean the same bytes."""
        existing = self._artifacts.list_for_document(identity.document_id)
        if not existing:
            return
        for metadata in existing:
            if metadata.sha256 == identity.source_sha256:
                return
        raise DocumentIngestionError(
            "document_identity_collision",
            f"document_id {identity.document_id!r} already exists for different content "
            f"({existing[0].sha256[:12]}…); refusing to merge two documents under one id",
            correlation_id=correlation_id or "corr_unset",
        )

    def _verify_source(self, input_path: str, expected_sha256: str) -> bool:
        try:
            current = self._reader.fingerprint(input_path)
            reread = self._reader.read(input_path, max_size_bytes=self._max_size_hint())
        except DocumentIngestionError:
            return False
        except OSError:
            return False
        if not current:
            return False
        return hashlib.sha256(reread.data).hexdigest() == expected_sha256
