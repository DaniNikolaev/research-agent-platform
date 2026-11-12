"""Artifact Store port and its (internal) metadata contract.

The store owns the mapping between a logical ``artifact://`` URI and a physical location
(local content-addressed directory now; S3-compatible object storage later).  The port never
exposes a physical path to the caller — only bytes, streams and metadata.

Three address spaces are supported (see :mod:`research_platform.domain.runs.artifacts`):

* ``artifact://documents/<document_id>/runs/<run_id>/<type>/<id>`` — document artefact;
* ``artifact://runs/<run_id>/<type>/<id>`` — run artefact (no single document);
* ``artifact://platform/<type>/<id>`` — platform artefact (no document, no run).

Metadata is a *store-level* concept, deliberately separate from the published
``ArtifactReference`` contract: the reference appears inside immutable handoff packages
(contract 2.1) and must not grow fields or address spaces, while the store needs
run/document linkage, schema version and a backend marker for auditing and cleanup.

An artefact is **visible** (readable through this port) only when the object file and a
valid, consistent metadata sidecar both exist.  An object without a sidecar is a crash
residue, not an artefact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, BinaryIO, Protocol, runtime_checkable

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    ShortStr,
    UtcDatetime,
)
from research_platform.domain.runs.artifacts import (
    RUN_ID_PATTERN,
    ArtifactReference,
    ArtifactScope,
    ArtifactType,
    StoreArtifactUri,
    parse_artifact_uri,
    scope_for,
)

__all__ = [
    "MAX_METADATA_BYTES",
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactIssue",
    "ArtifactIssueKind",
    "ArtifactMetadata",
    "ArtifactPublishUnsupportedError",
    "ArtifactScanReport",
    "ArtifactScopeError",
    "ArtifactStorePort",
]

MAX_METADATA_BYTES = 64 * 1024
"""Upper bound on a serialized metadata sidecar; larger means the caller made a mistake."""

_SECRET_HINT = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|credential|bearer|sk-[a-z0-9]{6,})"
)
"""Conservative detector for credentials in the free-form metadata block."""


class ArtifactConflictError(FileExistsError):
    """Raised when an artefact id already exists with different content."""


class ArtifactIntegrityError(ValueError):
    """Raised when stored bytes, a sidecar or their pairing do not match."""


class ArtifactScopeError(ValueError):
    """Raised when identifiers and a URI disagree about the address space."""


class ArtifactPublishUnsupportedError(OSError):
    """Raised when the filesystem cannot publish a file without a partial-write window."""


class ArtifactIssueKind(StrEnum):
    """Kinds of problems found by :meth:`ArtifactStorePort.scan`."""

    OBJECT_WITHOUT_METADATA = "object_without_metadata"
    METADATA_WITHOUT_OBJECT = "metadata_without_object"
    CORRUPT_METADATA = "corrupt_metadata"
    SHA_MISMATCH = "sha_mismatch"
    SIZE_MISMATCH = "size_mismatch"
    URI_MISMATCH = "uri_mismatch"


@dataclass(frozen=True, slots=True)
class ArtifactIssue:
    """One problem found during a diagnostic scan (never repaired automatically)."""

    kind: ArtifactIssueKind
    artifact_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class ArtifactScanReport:
    """Result of a diagnostic scan; an empty issue list means a consistent store."""

    issues: tuple[ArtifactIssue, ...] = ()
    objects_scanned: int = 0
    metadata_scanned: int = 0

    @property
    def ok(self) -> bool:
        """Whether the store is internally consistent."""
        return not self.issues

    def by_kind(self, kind: ArtifactIssueKind) -> tuple[ArtifactIssue, ...]:
        """Issues of one kind."""
        return tuple(issue for issue in self.issues if issue.kind is kind)


class ArtifactMetadata(ContractModel):
    """Everything the store knows about one stored artefact."""

    artifact_id: Identifier
    artifact_type: ArtifactType
    uri: StoreArtifactUri
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$", max_length=64)]
    size_bytes: Annotated[int, Field(ge=0)]
    media_type: NonEmptyStr
    run_id: Annotated[str | None, StringConstraints(pattern=rf"^{RUN_ID_PATTERN}$")] = None
    document_id: Identifier | None = None
    schema_version: Annotated[str | None, StringConstraints(pattern=r"^\d+\.\d+$")] = None
    created_at: UtcDatetime
    storage_backend: ShortStr
    metadata: Annotated[dict[ShortStr, ShortStr], Field(default_factory=dict, max_length=32)] = (
        Field(default_factory=dict)
    )

    @model_validator(mode="after")
    def _uri_matches_scope_and_identity(self) -> ArtifactMetadata:
        parts = parse_artifact_uri(self.uri)
        expected_scope = scope_for(document_id=self.document_id, run_id=self.run_id)
        if parts.scope is not expected_scope:
            raise ArtifactScopeError(
                f"uri scope {parts.scope.value!r} does not match the declared identifiers "
                f"(document_id={self.document_id!r}, run_id={self.run_id!r} → "
                f"{expected_scope.value!r} scope)"
            )
        if parts.artifact_id != self.artifact_id:
            raise ArtifactScopeError(
                f"uri declares artifact_id {parts.artifact_id!r} but metadata declares "
                f"{self.artifact_id!r}"
            )
        if parts.artifact_type is not self.artifact_type:
            raise ArtifactScopeError(
                f"uri declares artifact_type {parts.artifact_type.value!r} but metadata "
                f"declares {self.artifact_type.value!r}"
            )
        if parts.document_id != self.document_id:
            raise ArtifactScopeError(
                f"uri declares document_id {parts.document_id!r} but metadata declares "
                f"{self.document_id!r}"
            )
        if parts.run_id != self.run_id:
            raise ArtifactScopeError(
                f"uri declares run_id {parts.run_id!r} but metadata declares {self.run_id!r}"
            )
        return self

    @model_validator(mode="after")
    def _no_absolute_path_leaks(self) -> ArtifactMetadata:
        for value in (self.uri, self.media_type):
            if "\\" in value or ":" in value.split("//", 1)[-1].split("/", 1)[0]:
                raise ArtifactScopeError(
                    "metadata must not carry a physical path; the URI is a logical address"
                )
        return self

    @model_validator(mode="after")
    def _extras_carry_no_secrets(self) -> ArtifactMetadata:
        for key, value in self.metadata.items():
            if _SECRET_HINT.search(key) or _SECRET_HINT.search(value):
                raise ValueError(
                    f"metadata[{key!r}] looks like a credential; a sidecar is not a secret store"
                )
        return self

    @property
    def scope(self) -> ArtifactScope:
        """Address space implied by the identifiers."""
        return scope_for(document_id=self.document_id, run_id=self.run_id)


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Write, read, verify and enumerate immutable artefacts."""

    def put(
        self,
        *,
        artifact_id: str,
        artifact_type: ArtifactType,
        data: bytes,
        media_type: str,
        document_id: str | None = None,
        run_id: str | None = None,
        schema_version: str | None = None,
    ) -> ArtifactMetadata:
        """Store bytes atomically and return their metadata.

        The address space follows the identifiers supplied: ``document_id`` and
        ``run_id`` → document artefact; ``run_id`` only → run artefact; neither →
        platform artefact.  ``document_id`` without ``run_id`` is rejected.

        Raises:
            ArtifactScopeError: the identifiers or the derived URI are inconsistent.
            ArtifactConflictError: the artefact id exists with different content.
            ArtifactPublishUnsupportedError: the filesystem cannot publish safely.
        """
        ...

    def get(self, reference: ArtifactReference) -> bytes:
        """Read a document artefact and verify it against the reference.

        Raises:
            FileNotFoundError: no visible artefact (object and sidecar must both exist).
            ArtifactIntegrityError: the sidecar, size or checksum disagree.
        """
        ...

    def get_by_id(self, artifact_id: str) -> bytes:
        """Read any artefact (all address spaces) by id, with full verification."""
        ...

    def open_read(self, reference: ArtifactReference) -> BinaryIO:
        """Open a safe read-only stream over a document artefact."""
        ...

    def exists(self, reference: ArtifactReference) -> bool:
        """Whether a *visible* document artefact matches the reference."""
        ...

    def exists_id(self, artifact_id: str) -> bool:
        """Whether a visible artefact with this id exists (any address space)."""
        ...

    def verify_checksum(self, reference: ArtifactReference) -> bool:
        """Recompute the stored checksum and compare it with the reference."""
        ...

    def get_metadata(self, reference: ArtifactReference) -> ArtifactMetadata:
        """Load the sidecar of a document artefact and cross-check it with the reference."""
        ...

    def get_metadata_by_id(self, artifact_id: str) -> ArtifactMetadata:
        """Load the sidecar of any artefact by id."""
        ...

    def list_by_run(self, run_id: str) -> list[ArtifactMetadata]:
        """All artefacts of one run (document and run scopes), by creation time."""
        ...

    def list_by_document(self, document_id: str) -> list[ArtifactMetadata]:
        """All document artefacts of one document, by creation time."""
        ...

    def list_platform(self) -> list[ArtifactMetadata]:
        """All platform-scoped artefacts, by creation time."""
        ...

    def scan(self) -> ArtifactScanReport:
        """Diagnose inconsistencies without repairing or deleting anything."""
        ...

    def check_writable(self) -> bool:
        """Verify the backing storage can be written and read without side effects."""
        ...

    def close(self) -> None:
        """Release resources held by the adapter."""
        ...
