"""Local file implementation of :class:`ArtifactStorePort`.

Guarantees:

* **Three address spaces.** Document, run and platform artefacts are all supported; the
  scope is derived from the identifiers supplied and cross-checked against the URI.
* **Immutability.** An artefact id maps to exactly one content hash.  Re-storing identical
  bytes is idempotent; storing different bytes under an existing id raises
  :class:`ArtifactConflictError`.
* **Visibility.** An artefact is readable only when the object file *and* a valid,
  consistent metadata sidecar exist.  An object without a sidecar is crash residue, not an
  artefact, and is never returned by a read method.
* **Atomic publication, two files.** The object and the sidecar are published separately,
  each through a same-directory staging file and an exclusive link that fails when the
  target already exists.  Publication of the pair is *not* a single atomic operation; the
  order (object first, then sidecar) means a crash leaves at worst an invisible object,
  which can be adopted by an identical retry.
* **Fail-closed metadata publication.** If the filesystem cannot publish without a
  partial-write window, the sidecar write fails with
  :class:`ArtifactPublishUnsupportedError` instead of silently writing in place.
* **Integrity.** The SHA-256 is computed by the store, recorded in metadata, and verified on
  every read; read paths also verify size, URI and identifier agreement.
* **Path safety.** Every path is derived from validated identifiers and resolved inside the
  store root; traversal, absolute paths, percent-encoding and odd file names cannot reach
  the filesystem.
* **Diagnostics, not repairs.** :meth:`scan` reports orphan objects, orphan sidecars,
  corrupt sidecars and mismatches without deleting or "fixing" anything.

The store owns physical paths; callers only ever see logical ``artifact://`` URIs.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from research_platform.application.ports.artifact_store import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactIssue,
    ArtifactIssueKind,
    ArtifactMetadata,
    ArtifactPublishUnsupportedError,
    ArtifactScanReport,
    ArtifactScopeError,
    ArtifactStorePort,
)
from research_platform.domain.runs.artifacts import (
    ArtifactReference,
    ArtifactScope,
    ArtifactType,
    build_artifact_uri,
    scope_for,
)
from research_platform.platform.artifact_store.metadata import (
    load_metadata,
    parse_metadata_bytes,
    serialize_metadata,
)
from research_platform.platform.artifact_store.paths import (
    ArtifactStorePaths,
    extension_for_media_type,
)

__all__ = ["LocalFileArtifactStore"]

_CHUNK_SIZE = 1024 * 1024
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,63}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{2,63}$")
_OBJECT_NAME_RE = re.compile(r"^(?P<identifier>[a-z0-9][a-z0-9_\-]{2,63})(?P<ext>\.[a-z0-9]{1,8})$")


class LocalFileArtifactStore(ArtifactStorePort):
    """Content-addressed file store rooted at a configured directory."""

    backend_name = "local_file"
    backend_version = "2"

    def __init__(self, *, root: Path, clock: object | None = None) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a pathlib.Path")
        self._paths = ArtifactStorePaths(root=root)
        self._paths.ensure()
        self._clock = clock
        self._closed = False
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ location

    @property
    def root(self) -> Path:
        """Resolved store root (infrastructure detail, never part of a contract)."""
        return self._paths.root

    def _object_path(self, *, artifact_id: str, sha256: str, media_type: str) -> Path:
        return self._paths.object_path(
            artifact_id=artifact_id,
            sha256=sha256,
            extension=extension_for_media_type(media_type),
        )

    def _metadata_path(self, *, artifact_id: str) -> Path:
        return self._paths.metadata_path(artifact_id=artifact_id)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("artefact store is closed")

    def _now(self) -> datetime:
        if self._clock is not None and callable(self._clock):
            moment = self._clock()
            if not isinstance(moment, datetime):
                raise TypeError("clock must return a datetime")
            return moment.astimezone(UTC)
        return datetime.now(UTC)

    # ------------------------------------------------------------------ writing

    def _artifact_lock(self, artifact_id: str) -> threading.Lock:
        """Per-artefact lock: serialises concurrent writers of the same id in this process."""
        with self._locks_guard:
            return self._locks.setdefault(artifact_id, threading.Lock())

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
        """Store bytes atomically; see the class docstring for the guarantees."""
        self._ensure_open()
        with self._artifact_lock(artifact_id):
            return self._put_locked(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                data=data,
                media_type=media_type,
                document_id=document_id,
                run_id=run_id,
                schema_version=schema_version,
            )

    def _put_locked(
        self,
        *,
        artifact_id: str,
        artifact_type: ArtifactType,
        data: bytes,
        media_type: str,
        document_id: str | None,
        run_id: str | None,
        schema_version: str | None,
    ) -> ArtifactMetadata:
        """Body of :meth:put, executed while holding the artefact lock."""
        if not isinstance(data, bytes | bytearray):
            raise TypeError("data must be bytes")
        payload = bytes(data)

        _require(_IDENTIFIER_RE, artifact_id, "artifact_id")
        if document_id is not None:
            _require(_IDENTIFIER_RE, document_id, "document_id")
        if run_id is not None:
            _require(_RUN_ID_RE, run_id, "run_id")
        if not artifact_type:
            raise ValueError("artifact_type is required")

        scope = scope_for(document_id=document_id, run_id=run_id)
        uri = build_artifact_uri(
            document_id=document_id,
            run_id=run_id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
        )
        sha256 = hashlib.sha256(payload).hexdigest()
        size = len(payload)
        object_path = self._object_path(
            artifact_id=artifact_id, sha256=sha256, media_type=media_type
        )

        existing = self._read_metadata_if_present(artifact_id=artifact_id)
        if existing is not None:
            if existing.sha256 == sha256 and existing.size_bytes == size and existing.uri == uri:
                return existing  # idempotent re-store of identical content
            raise ArtifactConflictError(
                f"artefact {artifact_id!r} already exists with sha256 "
                f"{existing.sha256[:12]}… ({existing.scope.value} scope); a new result must "
                "get a new artifact_id"
            )

        if not object_path.exists():
            residue = self._existing_objects(artifact_id)
            if residue:
                raise ArtifactConflictError(
                    f"artefact {artifact_id!r} already has stored content "
                    f"({residue[0].name}); a new result must get a new artifact_id"
                )
            try:
                self._publish_object(object_path=object_path, data=payload)
            except FileExistsError as exc:
                # Lost a concurrent race to publish this object.  Adopt the winner's file
                # only when it holds exactly the content we were asked to store.
                if self._file_sha256(object_path) != sha256:
                    raise ArtifactConflictError(
                        f"artefact {artifact_id!r} was published concurrently with different "
                        "content; refusing to overwrite"
                    ) from exc
        elif self._file_sha256(object_path) != sha256:
            # A published object without a sidecar is crash residue; adopt it only when the
            # content matches, otherwise refuse: an object is never overwritten.
            raise ArtifactConflictError(
                f"object for artefact {artifact_id!r} already exists with different content; "
                "refusing to overwrite"
            )

        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            uri=uri,
            sha256=sha256,
            size_bytes=size,
            media_type=media_type,
            run_id=run_id,
            document_id=document_id,
            schema_version=schema_version,
            created_at=self._now(),
            storage_backend=self.backend_name,
            metadata={"backend_version": self.backend_version, "scope": scope.value},
        )
        self._publish_metadata(metadata)
        return metadata

    # -------------------------------------------------------------- object write

    def _publish_object(self, *, object_path: Path, data: bytes) -> None:
        """Stage bytes in ``tmp/`` and publish them under the final name."""
        object_path.parent.mkdir(parents=True, exist_ok=True)
        staged = self._stage_bytes(self._paths.tmp_dir, data, prefix="stage-")
        try:
            written = staged.stat().st_size
            if written != len(data):
                raise ArtifactIntegrityError(
                    f"staged artefact has {written} bytes, expected {len(data)}"
                )
            self._publish_no_overwrite(staged=staged, final=object_path, kind="object")
            published = object_path.stat().st_size
            if published != len(data):
                object_path.unlink(missing_ok=True)
                raise ArtifactIntegrityError(
                    f"published artefact has {published} bytes, expected {len(data)}"
                )
        finally:
            staged.unlink(missing_ok=True)

    # ------------------------------------------------------------ metadata write

    def _publish_metadata(self, metadata: ArtifactMetadata) -> None:
        """Publish the sidecar atomically, never overwriting an existing file.

        Sequence: serialize (validating the model first) → stage in the metadata directory
        → read the staging file back and validate it → publish exclusively → read the
        published file back and validate it again.  A failure at any step leaves either no
        sidecar (the artefact stays invisible) or the previous one untouched.
        """
        target = self._metadata_path(artifact_id=metadata.artifact_id)
        payload = serialize_metadata(metadata)
        staged = self._stage_bytes(self._paths.metadata_dir, payload, prefix=".sidecar-")
        published = False
        try:
            self._verify_sidecar(staged, metadata)
            try:
                self._publish_no_overwrite(staged=staged, final=target, kind="metadata")
            except FileExistsError as exc:
                # A concurrent writer published first.  Adopt only an identical sidecar.
                existing = load_metadata(target)
                if existing != metadata:
                    raise ArtifactConflictError(
                        f"metadata for artefact {metadata.artifact_id!r} was published "
                        "concurrently with different content"
                    ) from exc
                return
            published = True
            self._verify_sidecar(target, metadata)
        except Exception:
            if published:
                # We created the sidecar exclusively in this operation, so removing the
                # failed one restores the documented state "no sidecar, object invisible".
                target.unlink(missing_ok=True)
            raise
        finally:
            staged.unlink(missing_ok=True)

    def _verify_sidecar(self, path: Path, expected: ArtifactMetadata) -> None:
        """Re-read a sidecar and prove it parses to exactly the metadata we intended."""
        try:
            actual = parse_metadata_bytes(path.read_bytes())
        except ValueError as exc:
            raise ArtifactIntegrityError(
                f"metadata sidecar {path.name} is not readable: {exc}"
            ) from exc
        if actual != expected:
            raise ArtifactIntegrityError(
                f"metadata sidecar {path.name} does not round-trip: published record differs "
                "from the record that was written"
            )

    # ----------------------------------------------------------- low-level publish

    def _stage_bytes(self, directory: Path, data: bytes, *, prefix: str) -> Path:
        """Write ``data`` to a fresh staging file next to its final destination."""
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix=prefix, suffix=".part", delete=False
        ) as handle:
            staged = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        return self._paths.contained(staged)

    def _publish_no_overwrite(self, *, staged: Path, final: Path, kind: str) -> None:
        """Publish ``staged`` as ``final`` without ever overwriting an existing file.

        The primary mechanism is ``os.link``: creating a hard link is atomic and fails with
        ``FileExistsError`` when the target exists, so readers never observe a partially
        written final file.  For objects a copy fallback keeps the store usable on
        filesystems without hard links; a partially copied object is invisible anyway,
        because visibility requires a validated sidecar.  For metadata there is no fallback:
        a partially written sidecar could be read as valid JSON, so the operation fails
        closed instead.
        """
        try:
            os.link(staged, final)
            return
        except FileExistsError as exc:
            raise FileExistsError(f"{kind} {final.name!r} already exists") from exc
        except OSError as exc:
            if kind == "metadata":
                raise ArtifactPublishUnsupportedError(
                    "cannot publish metadata atomically without overwriting: hard links are "
                    f"unavailable ({exc}). Refusing to write the sidecar in place, because a "
                    "reader could observe a partially written JSON document."
                ) from exc
        try:
            with final.open("xb") as destination, staged.open("rb") as source:
                while chunk := source.read(_CHUNK_SIZE):
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        except FileExistsError as exc:
            raise FileExistsError(f"{kind} {final.name!r} already exists") from exc

    # ------------------------------------------------------------------- reading

    def _load_visible_metadata(self, artifact_id: str) -> ArtifactMetadata:
        path = self._metadata_path(artifact_id=artifact_id)
        if not path.exists():
            raise FileNotFoundError(
                f"artefact {artifact_id!r} is not visible: no metadata sidecar "
                "(an object without metadata is crash residue, not an artefact)"
            )
        try:
            return load_metadata(path)
        except ValueError as exc:
            raise ArtifactIntegrityError(
                f"metadata sidecar for artefact {artifact_id!r} is corrupt: {exc}"
            ) from exc

    def get_metadata_by_id(self, artifact_id: str) -> ArtifactMetadata:
        """Load the sidecar of any artefact by id."""
        self._ensure_open()
        return self._load_visible_metadata(artifact_id)

    def _metadata_for_reference(self, reference: ArtifactReference) -> ArtifactMetadata:
        metadata = self._load_visible_metadata(reference.artifact_id)
        mismatches: list[str] = []
        if metadata.uri != reference.uri:
            mismatches.append(f"uri {metadata.uri!r} != {reference.uri!r}")
        if metadata.sha256 != reference.sha256:
            mismatches.append("sha256 differs")
        if metadata.size_bytes != reference.size_bytes:
            mismatches.append("size_bytes differs")
        if metadata.artifact_type is not reference.artifact_type:
            mismatches.append("artifact_type differs")
        if mismatches:
            raise ArtifactIntegrityError(
                f"artefact {reference.artifact_id!r} does not match its reference: "
                + ", ".join(mismatches)
            )
        return metadata

    def get_metadata(self, reference: ArtifactReference) -> ArtifactMetadata:
        """Load the sidecar of a document artefact and cross-check it with the reference."""
        self._ensure_open()
        return self._metadata_for_reference(reference)

    def _read_verified(self, metadata: ArtifactMetadata) -> bytes:
        path = self._object_path(
            artifact_id=metadata.artifact_id,
            sha256=metadata.sha256,
            media_type=metadata.media_type,
        )
        if not path.exists():
            raise ArtifactIntegrityError(
                f"artefact {metadata.artifact_id!r} has metadata but its object is missing"
            )
        payload = path.read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if actual != metadata.sha256:
            raise ArtifactIntegrityError(
                f"artefact {metadata.artifact_id!r} is corrupt: stored sha256 is "
                f"{actual[:12]}…, metadata says {metadata.sha256[:12]}…"
            )
        if len(payload) != metadata.size_bytes:
            raise ArtifactIntegrityError(
                f"artefact {metadata.artifact_id!r} has {len(payload)} bytes, metadata says "
                f"{metadata.size_bytes}"
            )
        return payload

    def get(self, reference: ArtifactReference) -> bytes:
        """Read a document artefact, verifying sidecar, size and checksum."""
        self._ensure_open()
        metadata = self._metadata_for_reference(reference)
        return self._read_verified(metadata)

    def get_by_id(self, artifact_id: str) -> bytes:
        """Read any artefact by id with full verification."""
        self._ensure_open()
        return self._read_verified(self._load_visible_metadata(artifact_id))

    def open_read(self, reference: ArtifactReference) -> BinaryIO:
        """Open a read-only stream over a document artefact."""
        self._ensure_open()
        metadata = self._metadata_for_reference(reference)
        path = self._object_path(
            artifact_id=metadata.artifact_id,
            sha256=metadata.sha256,
            media_type=metadata.media_type,
        )
        if not path.exists():
            raise FileNotFoundError(
                f"artefact {reference.artifact_id!r} has metadata but its object is missing"
            )
        return path.open("rb")

    def _is_visible(self, metadata: ArtifactMetadata) -> bool:
        path = self._object_path(
            artifact_id=metadata.artifact_id,
            sha256=metadata.sha256,
            media_type=metadata.media_type,
        )
        return path.exists() and path.stat().st_size == metadata.size_bytes

    def exists(self, reference: ArtifactReference) -> bool:
        """Whether a visible document artefact matches the reference."""
        self._ensure_open()
        try:
            metadata = self._metadata_for_reference(reference)
        except (FileNotFoundError, ArtifactIntegrityError):
            return False
        return self._is_visible(metadata)

    def exists_id(self, artifact_id: str) -> bool:
        """Whether a visible artefact with this id exists (any address space)."""
        self._ensure_open()
        try:
            metadata = self._load_visible_metadata(artifact_id)
        except (FileNotFoundError, ArtifactIntegrityError):
            return False
        return self._is_visible(metadata)

    def verify_checksum(self, reference: ArtifactReference) -> bool:
        """Recompute the checksum and compare it with the reference."""
        self._ensure_open()
        try:
            metadata = self._metadata_for_reference(reference)
        except (FileNotFoundError, ArtifactIntegrityError):
            return False
        try:
            return (
                self._file_sha256(
                    self._object_path(
                        artifact_id=metadata.artifact_id,
                        sha256=metadata.sha256,
                        media_type=metadata.media_type,
                    )
                )
                == reference.sha256
            )
        except FileNotFoundError:
            return False

    # ---------------------------------------------------------------- enumerating

    def list_by_run(self, run_id: str) -> list[ArtifactMetadata]:
        """All artefacts of one run (document and run scopes)."""
        return [item for item in self._iter_metadata() if item.run_id == run_id]

    def list_by_document(self, document_id: str) -> list[ArtifactMetadata]:
        """All document artefacts of one document."""
        return [
            item
            for item in self._iter_metadata()
            if item.document_id == document_id and item.scope is ArtifactScope.DOCUMENT
        ]

    def list_platform(self) -> list[ArtifactMetadata]:
        """All platform-scoped artefacts."""
        return [item for item in self._iter_metadata() if item.scope is ArtifactScope.PLATFORM]

    def _iter_metadata(self) -> Iterator[ArtifactMetadata]:
        self._ensure_open()
        directory = self._paths.metadata_dir
        if not directory.exists():
            return
        items: list[ArtifactMetadata] = []
        for path in sorted(directory.glob("*.json")):
            try:
                items.append(load_metadata(path))
            except ValueError:  # a corrupt sidecar must not break listing
                continue
        items.sort(key=lambda item: (item.created_at, item.artifact_id))
        yield from items

    def _read_metadata_if_present(self, *, artifact_id: str) -> ArtifactMetadata | None:
        path = self._metadata_path(artifact_id=artifact_id)
        if not path.exists():
            return None
        try:
            return load_metadata(path)
        except ValueError as exc:
            raise ArtifactIntegrityError(
                f"metadata sidecar for artefact {artifact_id!r} is corrupt: {exc}; "
                "run scan() and decide how to recover it"
            ) from exc

    def _existing_objects(self, artifact_id: str) -> list[Path]:
        """Every stored object that claims this artefact id, whatever its content hash."""
        return sorted(self._paths.objects_dir.glob(f"*/{artifact_id}.*"))

    # ---------------------------------------------------------------- diagnostics

    def scan(self) -> ArtifactScanReport:
        """Report inconsistencies without repairing or deleting anything."""
        self._ensure_open()
        issues: list[ArtifactIssue] = []
        metadata_by_id: dict[str, ArtifactMetadata] = {}
        sidecar_paths = sorted(self._paths.metadata_dir.glob("*.json"))
        for path in sidecar_paths:
            try:
                metadata = load_metadata(path)
            except ValueError as exc:
                issues.append(
                    ArtifactIssue(
                        kind=ArtifactIssueKind.CORRUPT_METADATA,
                        artifact_id=path.stem,
                        detail=str(exc),
                    )
                )
                continue
            metadata_by_id[metadata.artifact_id] = metadata
            if not self._is_visible(metadata):
                issues.append(
                    ArtifactIssue(
                        kind=ArtifactIssueKind.METADATA_WITHOUT_OBJECT,
                        artifact_id=metadata.artifact_id,
                        detail="metadata exists but the object is missing or has another size",
                    )
                )

        objects = [
            path
            for path in self._paths.objects_dir.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        ]
        object_ids: set[str] = set()
        for path in objects:
            match = _OBJECT_NAME_RE.match(path.name)
            if match is None:
                issues.append(
                    ArtifactIssue(
                        kind=ArtifactIssueKind.OBJECT_WITHOUT_METADATA,
                        artifact_id=path.name,
                        detail="object file name does not match the identifier pattern",
                    )
                )
                continue
            artifact_id = match.group("identifier")
            object_ids.add(artifact_id)
            metadata = metadata_by_id.get(artifact_id)
            if metadata is None:
                issues.append(
                    ArtifactIssue(
                        kind=ArtifactIssueKind.OBJECT_WITHOUT_METADATA,
                        artifact_id=artifact_id,
                        detail="object exists without a metadata sidecar (crash residue?)",
                    )
                )
                continue
            size = path.stat().st_size
            if size != metadata.size_bytes:
                issues.append(
                    ArtifactIssue(
                        kind=ArtifactIssueKind.SIZE_MISMATCH,
                        artifact_id=artifact_id,
                        detail=f"object has {size} bytes, metadata says {metadata.size_bytes}",
                    )
                )
            digest = self._file_sha256(path)
            if digest != metadata.sha256:
                issues.append(
                    ArtifactIssue(
                        kind=ArtifactIssueKind.SHA_MISMATCH,
                        artifact_id=artifact_id,
                        detail=(
                            f"object sha256 is {digest[:12]}…, metadata says "
                            f"{metadata.sha256[:12]}…"
                        ),
                    )
                )
            expected = self._object_path(
                artifact_id=artifact_id,
                sha256=metadata.sha256,
                media_type=metadata.media_type,
            )
            if expected != path:
                issues.append(
                    ArtifactIssue(
                        kind=ArtifactIssueKind.URI_MISMATCH,
                        artifact_id=artifact_id,
                        detail=f"object is stored at {path.relative_to(self.root)}, but its "
                        f"metadata addresses {expected.relative_to(self.root)}",
                    )
                )
        for artifact_id in metadata_by_id:
            if artifact_id not in object_ids:
                continue  # already reported as METADATA_WITHOUT_OBJECT
        return ArtifactScanReport(
            issues=tuple(issues),
            objects_scanned=len(objects),
            metadata_scanned=len(sidecar_paths),
        )

    # -------------------------------------------------------------------- health

    def check_writable(self) -> bool:
        """Prove the store can write, read and remove a file without leaving artefacts."""
        self._ensure_open()
        self._paths.ensure()
        probe = self._paths.tmp_dir / ".health-probe"
        try:
            probe.write_bytes(b"probe")
            return probe.read_bytes() == b"probe"
        except OSError:
            return False
        finally:
            probe.unlink(missing_ok=True)

    def close(self) -> None:
        """Mark the store as closed (no handles are held between calls)."""
        self._closed = True

    # --------------------------------------------------------------------- misc

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()


def _require(pattern: re.Pattern[str], value: str, name: str) -> None:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ArtifactScopeError(
            f"{name} {value!r} must match {pattern.pattern}; it becomes part of a logical "
            "URI and a file name, so separators, spaces and non-ASCII characters are rejected"
        )
