"""Document manifest: identity and provenance of the original source.

The manifest answers three questions without any infrastructure access:

* *what* was processed (``document_id``, physical page count, language);
* *which exact bytes* were processed (``source_sha256``);
* *where the immutable original* lives (``original_artifact_uri``).

``page_count`` may be ``None`` only for a non-paginated ``text/plain`` source.  The
rule is explicit so that a missing page count can never be silently interpreted as
"page 1" downstream (legacy defect D2).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    Sha256Hex,
    ShortStr,
    UtcDatetime,
)
from research_platform.domain.documents.identity import DocumentSourceType
from research_platform.domain.runs.artifacts import (
    ArtifactType,
    ArtifactUri,
    parse_artifact_uri,
)

SOURCE_MANIFEST_CONTRACT_VERSION: Final = "1.0"
"""Version of the ingestion manifest contract (Iteration 2)."""

__all__ = [
    "SOURCE_MANIFEST_CONTRACT_VERSION",
    "DocumentLanguage",
    "DocumentManifest",
    "SourceDocumentManifest",
    "SourceMediaType",
]


class SourceMediaType(StrEnum):
    """Supported original source media types."""

    PDF = "application/pdf"
    TEXT = "text/plain"


class DocumentLanguage(StrEnum):
    """Detected source language."""

    RU = "ru"
    EN = "en"
    UNKNOWN = "unknown"


class DocumentManifest(ContractModel):
    """Immutable description of the original document version."""

    document_id: Identifier
    source_file_name: ShortStr
    source_media_type: SourceMediaType
    language: DocumentLanguage
    page_count: int | None = Field(default=None, ge=1)
    source_sha256: Sha256Hex
    original_artifact_uri: ArtifactUri

    @model_validator(mode="after")
    def _file_name_is_not_a_path(self) -> DocumentManifest:
        if any(separator in self.source_file_name for separator in ("/", "\\", ":")):
            raise ValueError(
                "source_file_name must be a bare file name; absolute or relative paths are "
                "storage details and must not become part of the public contract"
            )
        return self

    @model_validator(mode="after")
    def _page_count_is_explicitly_absent_only_for_text(self) -> DocumentManifest:
        if self.page_count is None and self.source_media_type is not SourceMediaType.TEXT:
            raise ValueError(
                "page_count may be null only for a non-paginated text/plain source; "
                "a paginated format must declare its physical page count"
            )
        return self

    @model_validator(mode="after")
    def _original_artifact_is_the_declared_document(self) -> DocumentManifest:
        parts = parse_artifact_uri(self.original_artifact_uri)
        if parts.artifact_type is not ArtifactType.ORIGINAL_DOCUMENT:
            raise ValueError(
                "original_artifact_uri must reference an artifact of type "
                f"'original_document', got {parts.artifact_type.value!r}"
            )
        if parts.document_id != self.document_id:
            raise ValueError(
                f"original_artifact_uri belongs to document {parts.document_id!r} but the "
                f"manifest declares document_id {self.document_id!r}"
            )
        return self


class SourceDocumentManifest(ContractModel):
    """Ingestion-side manifest of one received document version (contract 1.0).

    This is the *Iteration 2* contract.  The published :class:`DocumentManifest` above is
    part of the immutable Agent 1A package schema and is deliberately left untouched: it
    describes the package view (identity, pages, language, original bytes), while the
    ingestion manifest additionally records how the file was received (source type, size,
    detected encoding, language detection method, processing status of the pipeline).

    Keeping the two apart is what makes the new fields additive: no published schema moves.
    """

    document_contract_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = (
        SOURCE_MANIFEST_CONTRACT_VERSION
    )
    document_id: Identifier
    run_id: ShortStr
    source_file_name: NonEmptyStr
    source_type: DocumentSourceType
    source_media_type: SourceMediaType
    source_sha256: Sha256Hex
    source_size_bytes: Annotated[int, Field(gt=0)]
    source_artifact_uri: ArtifactUri
    original_encoding: ShortStr | None = None
    encoding_uncertain: bool = False
    language: DocumentLanguage = DocumentLanguage.UNKNOWN
    language_detection_method: Annotated[
        str, StringConstraints(pattern=r"^(not_detected|character_ratio|bom_and_ratio)$")
    ] = "not_detected"
    created_at: UtcDatetime
    processing_status: Annotated[
        str,
        StringConstraints(
            pattern=r"^(ingested|parsing|parsed|quality_checked|requires_ocr|"
            r"needs_human_review|failed)$"
        ),
    ] = "ingested"

    @model_validator(mode="after")
    def _file_name_is_not_a_path(self) -> SourceDocumentManifest:
        if any(separator in self.source_file_name for separator in ("/", "\\", ":")):
            raise ValueError(
                "source_file_name must be a bare file name; a path is a storage detail, not "
                "a contract value"
            )
        return self

    @model_validator(mode="after")
    def _media_type_matches_the_source_type(self) -> SourceDocumentManifest:
        expected = SourceMediaType(self.source_type.media_type)
        if self.source_media_type is not expected:
            raise ValueError(
                f"source_media_type {self.source_media_type.value!r} does not match source "
                f"type {self.source_type.value!r} (expected {expected.value!r})"
            )
        return self

    @model_validator(mode="after")
    def _encoding_is_declared_only_for_text(self) -> SourceDocumentManifest:
        if self.source_type is DocumentSourceType.TXT:
            if self.original_encoding is None and not self.encoding_uncertain:
                raise ValueError(
                    "a decoded text source must record either its detected encoding or that "
                    "the encoding is uncertain"
                )
        elif self.original_encoding is not None or self.encoding_uncertain:
            raise ValueError(
                "PDF has no text encoding at ingestion; original_encoding belongs to TXT"
            )
        return self

    @model_validator(mode="after")
    def _artifact_belongs_to_the_document(self) -> SourceDocumentManifest:
        parts = parse_artifact_uri(self.source_artifact_uri)
        if parts.artifact_type is not ArtifactType.ORIGINAL_DOCUMENT:
            raise ValueError("source_artifact_uri must reference an 'original_document' artefact")
        if parts.document_id != self.document_id:
            raise ValueError(
                f"source_artifact_uri belongs to document {parts.document_id!r} but the "
                f"manifest declares document_id {self.document_id!r}"
            )
        if parts.run_id != self.run_id:
            raise ValueError(
                f"source_artifact_uri belongs to run {parts.run_id!r} but the manifest "
                f"declares run_id {self.run_id!r}"
            )
        return self
