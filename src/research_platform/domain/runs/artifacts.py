"""Logical artefact references and the three artefact address spaces.

The public contract never exposes a physical storage path.  Artefacts are addressed by
portable logical URIs, and the Artifact Store adapter owns the mapping from a URI to a
filesystem location.

Three address spaces exist, because not every artefact belongs to a document:

``document`` (published contract, used inside handoff packages)
    ``artifact://documents/<document_id>/runs/<run_id>/<artifact_type>/<artifact_id>``

``run`` (platform artifact, tied to one run only)
    ``artifact://runs/<run_id>/<artifact_type>/<artifact_id>``

``platform`` (platform artifact, tied to neither)
    ``artifact://platform/<artifact_type>/<artifact_id>``

``ArtifactReference`` — the *published* type that travels inside Agent 1A packages — keeps
the document address space only: the immutable package contract (2.1) must not widen, and a
package artefact always belongs to one document and one run.  Run- and platform-scoped
artefacts are addressed through the store's own metadata, which is an internal contract.

Absolute Windows paths (``D:\\...``), UNC paths, backslashes, percent-encoding and ``..``
traversal are rejected by construction, so a stored artefact can be moved between machines
and between storage backends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    Sha256Hex,
    UtcDatetime,
)

__all__ = [
    "ARTIFACT_URI_PATTERN",
    "ARTIFACT_URI_SCHEME",
    "ARTIFACT_URI_TEMPLATE",
    "DOCUMENT_ID_PATTERN",
    "MEDIA_TYPE_PATTERN",
    "PLATFORM_ARTIFACT_URI_TEMPLATE",
    "RUN_ARTIFACT_URI_TEMPLATE",
    "RUN_ID_PATTERN",
    "STORE_ARTIFACT_URI_PATTERN",
    "ArtifactReference",
    "ArtifactScope",
    "ArtifactType",
    "ArtifactUri",
    "ArtifactUriParts",
    "MediaType",
    "StoreArtifactUri",
    "build_artifact_uri",
    "parse_artifact_uri",
    "scope_for",
]

ARTIFACT_URI_SCHEME: Final = "artifact"

DOCUMENT_ID_PATTERN: Final = r"[a-z0-9][a-z0-9_\-]{2,63}"
RUN_ID_PATTERN: Final = r"[A-Za-z0-9][A-Za-z0-9_\-]{2,63}"
ARTIFACT_TYPE_PATTERN: Final = r"[a-z][a-z0-9_]{2,63}"
ARTIFACT_ID_PATTERN: Final = r"[a-z0-9][a-z0-9_\-]{2,63}"

ARTIFACT_URI_TEMPLATE: Final = (
    "artifact://documents/{document_id}/runs/{run_id}/{artifact_type}/{artifact_id}"
)
RUN_ARTIFACT_URI_TEMPLATE: Final = "artifact://runs/{run_id}/{artifact_type}/{artifact_id}"
PLATFORM_ARTIFACT_URI_TEMPLATE: Final = "artifact://platform/{artifact_type}/{artifact_id}"

MEDIA_TYPE_PATTERN: Final = r"^[a-z][a-z0-9.+-]{0,63}/[A-Za-z0-9][A-Za-z0-9.+-]{0,127}$"

ARTIFACT_URI_PATTERN: Final = (
    r"^artifact://documents/"
    rf"{DOCUMENT_ID_PATTERN}/runs/"
    rf"{RUN_ID_PATTERN}/"
    rf"{ARTIFACT_TYPE_PATTERN}/"
    rf"{ARTIFACT_ID_PATTERN}$"
)
"""Document address space — the *published* pattern used by ``ArtifactReference``."""

STORE_ARTIFACT_URI_PATTERN: Final = (
    r"^artifact://(?:"
    rf"documents/{DOCUMENT_ID_PATTERN}/runs/{RUN_ID_PATTERN}"
    rf"|runs/{RUN_ID_PATTERN}"
    r"|platform"
    rf")/{ARTIFACT_TYPE_PATTERN}/{ARTIFACT_ID_PATTERN}$"
)
"""All three address spaces — used by the store's internal metadata contract."""

_DOCUMENT_URI_RE: Final = re.compile(
    rf"^artifact://documents/(?P<document_id>{DOCUMENT_ID_PATTERN})/runs/"
    rf"(?P<run_id>{RUN_ID_PATTERN})/(?P<artifact_type>{ARTIFACT_TYPE_PATTERN})/"
    rf"(?P<artifact_id>{ARTIFACT_ID_PATTERN})$"
)
_RUN_URI_RE: Final = re.compile(
    rf"^artifact://runs/(?P<run_id>{RUN_ID_PATTERN})/"
    rf"(?P<artifact_type>{ARTIFACT_TYPE_PATTERN})/(?P<artifact_id>{ARTIFACT_ID_PATTERN})$"
)
_PLATFORM_URI_RE: Final = re.compile(
    rf"^artifact://platform/(?P<artifact_type>{ARTIFACT_TYPE_PATTERN})/"
    rf"(?P<artifact_id>{ARTIFACT_ID_PATTERN})$"
)


class ArtifactType(StrEnum):
    """Closed set of artefact kinds produced or consumed by agent workflows."""

    ORIGINAL_DOCUMENT = "original_document"
    PARSED_DOCUMENT = "parsed_document"
    QUALITY_REPORT = "quality_report"
    RETRIEVAL_INDEX_MANIFEST = "retrieval_index_manifest"
    EXTRACTION_CANDIDATES = "extraction_candidates"
    EVIDENCE_LEDGER = "evidence_ledger"
    VALIDATION_REPORT = "validation_report"
    HANDOFF_PACKAGE = "handoff_package"
    GENERATED_MARKDOWN = "generated_markdown"
    RUN_LOG = "run_log"
    TRACE = "trace"


class ArtifactScope(StrEnum):
    """Which address space an artefact belongs to."""

    DOCUMENT = "document"
    """Belongs to one document version and one run (published contract)."""

    RUN = "run"
    """Belongs to one run; not tied to a single document."""

    PLATFORM = "platform"
    """Belongs to the platform; tied to neither a document nor a run."""


MediaType = Annotated[str, StringConstraints(pattern=MEDIA_TYPE_PATTERN, max_length=128)]
"""Concrete MIME type, for example ``application/pdf``."""

ArtifactUri = Annotated[str, StringConstraints(pattern=ARTIFACT_URI_PATTERN, max_length=512)]
"""Published logical artefact URI (document address space only)."""

StoreArtifactUri = Annotated[
    str, StringConstraints(pattern=STORE_ARTIFACT_URI_PATTERN, max_length=512)
]
"""Logical artefact URI in any of the three address spaces (store-level contract)."""


@dataclass(frozen=True, slots=True)
class ArtifactUriParts:
    """Parsed parts of a logical artefact URI."""

    scope: ArtifactScope
    artifact_type: ArtifactType
    artifact_id: str
    document_id: str | None = None
    run_id: str | None = None


def scope_for(*, document_id: str | None, run_id: str | None) -> ArtifactScope:
    """Derive the address space from the identifiers a caller supplied.

    Raises:
        ValueError: ``document_id`` without ``run_id`` — partial document addressing is
            not a supported address space, because a document artefact is always produced
            by a run and the published URI has no shape for "document only".
    """
    if document_id is not None and run_id is None:
        raise ValueError(
            "document_id requires run_id: a document artefact is always produced by a run, "
            "and 'artifact://documents/<id>' without a run is not an address space"
        )
    if document_id is not None:
        return ArtifactScope.DOCUMENT
    if run_id is not None:
        return ArtifactScope.RUN
    return ArtifactScope.PLATFORM


def build_artifact_uri(
    *,
    artifact_type: ArtifactType | str,
    artifact_id: str,
    document_id: str | None = None,
    run_id: str | None = None,
) -> str:
    """Build the canonical logical URI for an artefact in the right address space."""
    artifact_type_value = ArtifactType(artifact_type).value
    scope = scope_for(document_id=document_id, run_id=run_id)
    if scope is ArtifactScope.DOCUMENT:
        uri = (
            f"{ARTIFACT_URI_SCHEME}://documents/{document_id}/runs/{run_id}/"
            f"{artifact_type_value}/{artifact_id}"
        )
    elif scope is ArtifactScope.RUN:
        uri = f"{ARTIFACT_URI_SCHEME}://runs/{run_id}/{artifact_type_value}/{artifact_id}"
    else:
        uri = f"{ARTIFACT_URI_SCHEME}://platform/{artifact_type_value}/{artifact_id}"
    parse_artifact_uri(uri)  # validate eagerly: never emit an unparsable reference
    return uri


def parse_artifact_uri(uri: str) -> ArtifactUriParts:
    """Parse and validate a logical artefact URI in any address space.

    Raises:
        ValueError: the URI is not a portable logical artefact reference in one of the
            three supported address spaces.  The message names the expected templates so a
            caller never has to guess why an absolute path was refused.
    """
    candidate = uri.strip()
    if "\\" in candidate:
        raise ValueError(
            "artifact URI must not contain backslashes; a Windows path is a storage "
            f"implementation detail, not a contract value (got {uri!r})"
        )
    if ".." in candidate:
        raise ValueError(f"artifact URI must not contain '..' path traversal (got {uri!r})")
    if "%" in candidate:
        raise ValueError(
            "artifact URI must not contain percent-encoding; identifiers are plain "
            f"lowercase tokens (got {uri!r})"
        )
    if not candidate.startswith(f"{ARTIFACT_URI_SCHEME}://"):
        raise ValueError(
            f"artifact URI must start with {ARTIFACT_URI_SCHEME}:// (got {uri!r}); a physical "
            "path is never a contract value"
        )
    for pattern, scope in (
        (_DOCUMENT_URI_RE, ArtifactScope.DOCUMENT),
        (_RUN_URI_RE, ArtifactScope.RUN),
        (_PLATFORM_URI_RE, ArtifactScope.PLATFORM),
    ):
        match = pattern.fullmatch(candidate)
        if match is not None:
            return _parts_from_match(match, scope)
    templates = " | ".join(
        (ARTIFACT_URI_TEMPLATE, RUN_ARTIFACT_URI_TEMPLATE, PLATFORM_ARTIFACT_URI_TEMPLATE)
    )
    raise ValueError(
        f"artifact URI must match one of {templates!r} (relative logical scheme only), got {uri!r}"
    )


def _parts_from_match(match: re.Match[str], scope: ArtifactScope) -> ArtifactUriParts:
    groups = match.groupdict()
    raw_type = groups["artifact_type"]
    try:
        artifact_type = ArtifactType(raw_type)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ArtifactType)
        raise ValueError(
            f"unknown artifact type {raw_type!r} in URI; allowed values: {allowed}"
        ) from exc
    return ArtifactUriParts(
        scope=scope,
        artifact_type=artifact_type,
        artifact_id=groups["artifact_id"],
        document_id=groups.get("document_id"),
        run_id=groups.get("run_id"),
    )


class ArtifactReference(ContractModel):
    """Immutable, checksummed reference to a stored artefact.

    The reference is self-describing: it states the logical URI, the media type,
    the size, the SHA-256 of the bytes and the artefact version, so that a reader
    can detect substitution or truncation without trusting the writer.
    """

    # Address space: this published type carries document-scoped URIs only.  It travels
    # inside immutable handoff packages, where every artefact belongs to one document
    # version and one run, so the 2.1 schema must not widen.  Run- and platform-scoped
    # artefacts are addressed through the store's metadata (an internal contract).  The
    # docstring above is part of the published JSON Schema and must not be edited.
    artifact_id: Identifier
    artifact_type: ArtifactType
    uri: ArtifactUri
    media_type: MediaType
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Sha256Hex
    version: NonEmptyStr
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _uri_matches_declared_identity(self) -> ArtifactReference:
        parts = parse_artifact_uri(self.uri)
        if parts.scope is not ArtifactScope.DOCUMENT:
            raise ValueError(
                f"ArtifactReference.uri must be document-scoped "
                f"({ARTIFACT_URI_TEMPLATE}); got {parts.scope.value!r} scope. Run- and "
                "platform-scoped artefacts are addressed through store metadata."
            )
        if parts.artifact_type is not self.artifact_type:
            raise ValueError(
                f"uri declares artifact_type {parts.artifact_type.value!r} but the "
                f"reference declares {self.artifact_type.value!r}"
            )
        if parts.artifact_id != self.artifact_id:
            raise ValueError(
                f"uri declares artifact_id {parts.artifact_id!r} but the reference "
                f"declares {self.artifact_id!r}"
            )
        return self
