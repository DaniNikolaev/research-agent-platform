"""Path layout and path-safety helpers for the local artefact store.

Layout (all under a configurable root)::

    <root>/
        sha256/<aa>/<artifact_id><ext>     immutable objects, sharded by content hash
        metadata/<artifact_id>.json        store metadata for one artefact
        tmp/                               staging area for atomic publication

Every path is derived from validated identifiers, never from untrusted input, and the final
resolution is checked against the root so a traversal attempt cannot escape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ARTIFACT_ID_PATTERN", "ArtifactStorePaths", "extension_for_media_type"]

ARTIFACT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_\-]{2,63}$")

_MEDIA_TYPE_EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/json": ".json",
    "application/jsonl": ".jsonl",
    "application/yaml": ".yaml",
    "application/zip": ".zip",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "image/png": ".png",
}


def extension_for_media_type(media_type: str) -> str:
    """Deterministic, safe file extension for a media type."""
    return _MEDIA_TYPE_EXTENSIONS.get(media_type.lower(), ".bin")


@dataclass(frozen=True, slots=True)
class ArtifactStorePaths:
    """Resolved physical layout of one store instance."""

    root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            raise TypeError("root must be a pathlib.Path")

    @property
    def objects_dir(self) -> Path:
        """Directory holding immutable objects."""
        return self.root / "sha256"

    @property
    def metadata_dir(self) -> Path:
        """Directory holding metadata sidecars."""
        return self.root / "metadata"

    @property
    def tmp_dir(self) -> Path:
        """Staging directory on the same filesystem as the objects."""
        return self.root / "tmp"

    def ensure(self) -> None:
        """Create the directory skeleton if missing."""
        for directory in (self.root, self.objects_dir, self.metadata_dir, self.tmp_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def object_path(self, *, artifact_id: str, sha256: str, extension: str) -> Path:
        """Physical path of an object, sharded by the first two hash characters."""
        _require_artifact_id(artifact_id)
        _require_sha256(sha256)
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
            raise ValueError(f"unsafe extension {extension!r}")
        candidate = self.objects_dir / sha256[:2] / f"{artifact_id}{extension}"
        return self.contained(candidate)

    def metadata_path(self, *, artifact_id: str) -> Path:
        """Physical path of the metadata sidecar for one artefact."""
        _require_artifact_id(artifact_id)
        return self.contained(self.metadata_dir / f"{artifact_id}.json")

    def contained(self, candidate: Path) -> Path:
        """Resolve ``candidate`` and prove it stays inside the store root."""
        resolved = candidate.resolve()
        root = self.root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"path {candidate} escapes the artefact store root {root}")
        return resolved


def _require_artifact_id(artifact_id: str) -> None:
    if not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
        raise ValueError(
            f"artifact_id {artifact_id!r} must match {ARTIFACT_ID_PATTERN.pattern}; "
            "it becomes a file name, so separators, spaces, dots and non-ASCII characters "
            "are rejected"
        )


def _require_sha256(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
