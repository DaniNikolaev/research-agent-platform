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

from pydantic import Field, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    Sha256Hex,
    ShortStr,
)
from research_platform.domain.runs.artifacts import (
    ArtifactType,
    ArtifactUri,
    parse_artifact_uri,
)

__all__ = ["DocumentLanguage", "DocumentManifest", "SourceMediaType"]


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
