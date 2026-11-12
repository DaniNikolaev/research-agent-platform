"""Document repository port.

Stores document manifests (identity, language, page count, checksum).  Large binary
originals belong to the Artifact Store; this port stores metadata only.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_platform.domain.documents.models import DocumentManifest

__all__ = ["DocumentRepositoryPort"]


@runtime_checkable
class DocumentRepositoryPort(Protocol):
    """Register and look up document versions."""

    def register(self, manifest: DocumentManifest) -> None:
        """Register a document version.

        Registering the same ``document_id`` with a different ``source_sha256`` is a
        new version and must be stored as such, never as an overwrite.
        """
        ...

    def get(self, document_id: str) -> DocumentManifest:
        """Load the latest registered manifest for a document.

        Raises:
            LookupError: the document is unknown.
        """
        ...

    def find_by_sha256(self, source_sha256: str) -> DocumentManifest | None:
        """Find a manifest by exact source checksum, if it was ever registered."""
        ...
