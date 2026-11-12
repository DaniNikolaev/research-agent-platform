"""Serialization of artefact metadata sidecars.

Rules:

* one deterministic JSON encoding (sorted keys, UTF-8, trailing newline), so the same
  metadata always yields the same bytes and a checksum comparison is meaningful;
* metadata is validated by the Pydantic model *before* it is written, and re-parsed from
  the file after writing, so a malformed document can never be published;
* the size is bounded
  (:data:`~research_platform.application.ports.artifact_store.MAX_METADATA_BYTES`); a sidecar
  is a small record, not a payload store.

The store writes the bytes atomically; this module only encodes and decodes them.
"""

from __future__ import annotations

import json
from pathlib import Path

from research_platform.application.ports.artifact_store import (
    MAX_METADATA_BYTES,
    ArtifactMetadata,
)

__all__ = ["load_metadata", "parse_metadata_bytes", "serialize_metadata"]


def serialize_metadata(metadata: ArtifactMetadata) -> bytes:
    """Encode validated metadata as deterministic UTF-8 JSON.

    Raises:
        ValueError: the encoded sidecar exceeds :data:`MAX_METADATA_BYTES`.
    """
    payload = metadata.model_dump(mode="json")
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    data = text.encode("utf-8")
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError(
            f"metadata for artefact {metadata.artifact_id!r} is {len(data)} bytes, "
            f"above the {MAX_METADATA_BYTES}-byte limit; a sidecar must stay a small record"
        )
    return data


def parse_metadata_bytes(data: bytes) -> ArtifactMetadata:
    """Decode and validate metadata bytes.

    Raises:
        ValueError: the bytes are not UTF-8 JSON or do not satisfy the metadata contract.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"metadata is not valid UTF-8: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("metadata must be a JSON object")
    return ArtifactMetadata.model_validate(payload)


def load_metadata(path: Path) -> ArtifactMetadata:
    """Load and validate metadata from a file.

    Raises:
        FileNotFoundError: the sidecar does not exist.
        ValueError: the sidecar is not valid metadata.
    """
    return parse_metadata_bytes(path.read_bytes())
