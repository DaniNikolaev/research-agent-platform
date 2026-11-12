"""Artifact service: the application-facing way to store and verify artefacts.

The service is thin on purpose — it adds policy on top of the store:

* identifiers must describe the artefact they claim to describe (no cross-scope confusion);
* content is checksummed before it reaches the store and verified after it comes back;
* a write conflict is reported as such, never retried silently with a new id;
* only document-scoped artefacts can become a *published* ``ArtifactReference`` (the
  immutable package contract addresses artefacts by document and run); run- and
  platform-scoped artefacts are read back by id through the store metadata;
* every write and every integrity failure is logged with the required event names.
"""

from __future__ import annotations

import hashlib

from research_platform.application.ports.artifact_store import (
    ArtifactConflictError,
    ArtifactMetadata,
    ArtifactScanReport,
    ArtifactStorePort,
)
from research_platform.application.ports.logging import (
    ARTIFACT_CREATED,
    ARTIFACT_VALIDATION_FAILED,
    LoggerPort,
)
from research_platform.domain.runs.artifacts import (
    ArtifactReference,
    ArtifactScope,
    ArtifactType,
    build_artifact_uri,
)

__all__ = ["ArtifactService", "ArtifactServiceError"]


class ArtifactServiceError(RuntimeError):
    """A storage operation could not be completed as requested."""


class ArtifactService:
    """Store, read and verify artefacts through :class:`ArtifactStorePort`."""

    def __init__(self, *, store: ArtifactStorePort, logger: LoggerPort) -> None:
        self._store = store
        self._logger = logger

    # ------------------------------------------------------------------ writes

    def store_bytes(
        self,
        *,
        data: bytes,
        artifact_id: str,
        artifact_type: ArtifactType,
        media_type: str,
        document_id: str | None = None,
        run_id: str | None = None,
        schema_version: str | None = None,
        correlation_id: str | None = None,
    ) -> ArtifactMetadata:
        """Persist bytes under a new artefact id and return their metadata."""
        expected_sha = hashlib.sha256(data).hexdigest()
        log = self._logger.bind(
            artifact_id=artifact_id,
            artifact_type=artifact_type.value,
            run_id=run_id,
            document_id=document_id,
            correlation_id=correlation_id,
        )
        try:
            metadata = self._store.put(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                data=data,
                media_type=media_type,
                document_id=document_id,
                run_id=run_id,
                schema_version=schema_version,
            )
        except ArtifactConflictError:
            log.event(
                ARTIFACT_VALIDATION_FAILED,
                error_code="artifact_conflict",
                size_bytes=len(data),
            )
            raise
        if metadata.sha256 != expected_sha:
            log.event(
                ARTIFACT_VALIDATION_FAILED,
                error_code="artifact_checksum_mismatch",
                expected_sha256=expected_sha,
                actual_sha256=metadata.sha256,
            )
            raise ArtifactServiceError(
                f"store returned sha256 {metadata.sha256!r} for {artifact_id!r}, "
                f"expected {expected_sha!r}"
            )
        log.event(
            ARTIFACT_CREATED,
            size_bytes=metadata.size_bytes,
            media_type=metadata.media_type,
            sha256=metadata.sha256,
            schema_version=schema_version,
            scope=metadata.scope.value,
        )
        return metadata

    # ------------------------------------------------------------------- reads

    def read_bytes(
        self, reference: ArtifactReference, *, correlation_id: str | None = None
    ) -> bytes:
        """Read a document artefact and verify its checksum."""
        data = self._store.get(reference)
        actual = hashlib.sha256(data).hexdigest()
        if actual != reference.sha256:
            self._logger.bind(
                artifact_id=reference.artifact_id,
                correlation_id=correlation_id,
            ).event(
                ARTIFACT_VALIDATION_FAILED,
                error_code="artifact_checksum_mismatch",
                expected_sha256=reference.sha256,
                actual_sha256=actual,
            )
            raise ArtifactServiceError(
                f"artefact {reference.artifact_id!r} failed checksum verification"
            )
        return data

    def read_by_id(self, artifact_id: str) -> bytes:
        """Read any artefact (document, run or platform scope) by id."""
        return self._store.get_by_id(artifact_id)

    def exists(self, reference: ArtifactReference) -> bool:
        """Whether the artefact is stored and visible."""
        return self._store.exists(reference)

    def exists_id(self, artifact_id: str) -> bool:
        """Whether a visible artefact with this id exists."""
        return self._store.exists_id(artifact_id)

    def verify(self, reference: ArtifactReference) -> bool:
        """Recompute and compare the checksum of a stored artefact."""
        return self._store.verify_checksum(reference)

    def metadata(self, reference: ArtifactReference) -> ArtifactMetadata:
        """Load stored metadata for one document artefact."""
        return self._store.get_metadata(reference)

    def metadata_by_id(self, artifact_id: str) -> ArtifactMetadata:
        """Load stored metadata for any artefact."""
        return self._store.get_metadata_by_id(artifact_id)

    def list_for_run(self, run_id: str) -> list[ArtifactMetadata]:
        """All artefacts of a run (document and run scopes)."""
        return self._store.list_by_run(run_id)

    def list_for_document(self, document_id: str) -> list[ArtifactMetadata]:
        """All document artefacts of a document."""
        return self._store.list_by_document(document_id)

    def list_platform(self) -> list[ArtifactMetadata]:
        """All platform-scoped artefacts."""
        return self._store.list_platform()

    def scan(self) -> ArtifactScanReport:
        """Diagnose store inconsistencies (read-only; nothing is repaired)."""
        return self._store.scan()

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def reference_for(metadata: ArtifactMetadata) -> ArtifactReference:
        """Build the published reference for stored metadata.

        Raises:
            ValueError: the artefact is not document-scoped.  The published reference
                contract addresses artefacts by document and run only; a run- or
                platform-scoped artefact is read by id instead of being forced into a
                shape it does not have.
        """
        if metadata.scope is not ArtifactScope.DOCUMENT:
            raise ValueError(
                f"artefact {metadata.artifact_id!r} is {metadata.scope.value}-scoped and "
                "cannot become a published ArtifactReference (document scope only)"
            )
        return ArtifactReference(
            artifact_id=metadata.artifact_id,
            artifact_type=metadata.artifact_type,
            uri=metadata.uri,
            media_type=metadata.media_type,
            size_bytes=metadata.size_bytes,
            sha256=metadata.sha256,
            version="1",
            created_at=metadata.created_at,
        )

    @staticmethod
    def uri_for(
        *,
        artifact_type: ArtifactType,
        artifact_id: str,
        document_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        """The canonical logical URI of an artefact in the right address space."""
        return build_artifact_uri(
            document_id=document_id,
            run_id=run_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
        )
