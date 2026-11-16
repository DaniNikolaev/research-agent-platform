"""Stable document identity.

A document is identified by its *content*, not by its file name: the same bytes with the
same source type always produce the same ``document_id``, whatever the file is called and
wherever it lives.  The full SHA-256 stays the authoritative content identifier; the short
id is a convenient, sortable handle derived from it.

The type is mixed into the derivation, so a PDF and a TXT with identical bytes (a synthetic
case, but possible) cannot collide accidentally.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Final

from pydantic import model_validator

from research_platform.domain.base import (
    ContractModel,
    Sha256Hex,
    ShortStr,
)

__all__ = [
    "DOCUMENT_ID_HEX_LENGTH",
    "DocumentIdentity",
    "DocumentSourceType",
    "document_id_for",
]

DOCUMENT_ID_HEX_LENGTH: Final = 24
"""Number of SHA-256 hex characters kept in ``document_id``."""

_IDENTITY_DOMAIN: Final = "research-agent-platform/document/v1"


class DocumentSourceType(StrEnum):
    """Source format of an ingested document."""

    PDF = "pdf"
    TXT = "txt"

    @property
    def media_type(self) -> str:
        """MIME type of the original bytes."""
        return "application/pdf" if self is DocumentSourceType.PDF else "text/plain"

    @property
    def extension(self) -> str:
        """Canonical lower-case extension, including the dot."""
        return f".{self.value}"


def document_id_for(source_sha256: str, source_type: DocumentSourceType) -> str:
    """Derive a stable document id from the content hash and the source type.

    The derivation hashes a domain-separated tuple ``(application, type, sha256)`` rather
    than truncating the file hash directly, so the id is not a second copy of a prefix of
    the content hash and a future change of the scheme is versioned by the domain tag.

    Raises:
        ValueError: ``source_sha256`` is not 64 lower-case hex characters.
    """
    if len(source_sha256) != 64 or any(char not in "0123456789abcdef" for char in source_sha256):
        raise ValueError("source_sha256 must be 64 lower-case hexadecimal characters")
    digest = hashlib.sha256(
        f"{_IDENTITY_DOMAIN}|{source_type.value}|{source_sha256}".encode()
    ).hexdigest()
    return f"doc_{digest[:DOCUMENT_ID_HEX_LENGTH]}"


class DocumentIdentity(ContractModel):
    """Stable identity of one document version."""

    document_id: ShortStr
    source_sha256: Sha256Hex
    source_type: DocumentSourceType

    @model_validator(mode="after")
    def _id_is_derived_from_the_content(self) -> DocumentIdentity:
        expected = document_id_for(self.source_sha256, self.source_type)
        if self.document_id != expected:
            raise ValueError(
                f"document_id {self.document_id!r} does not match the content-derived id "
                f"{expected!r}; identity is a function of bytes and source type"
            )
        return self

    @staticmethod
    def derive(source_sha256: str, source_type: DocumentSourceType) -> DocumentIdentity:
        """Build the identity that belongs to a content hash."""
        return DocumentIdentity(
            document_id=document_id_for(source_sha256, source_type),
            source_sha256=source_sha256,
            source_type=source_type,
        )
