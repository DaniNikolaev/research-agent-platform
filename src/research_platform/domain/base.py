"""Shared primitives for every contract model of the platform.

Everything in the contract layer is immutable (``frozen=True``) and closed
(``extra="forbid"``): a handoff package, a card or an evidence record must never
grow fields silently, and a downstream agent must not be able to mutate a package
it received.  Mutation attempts therefore fail loudly instead of degrading
provenance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

__all__ = [
    "IDENTIFIER_PATTERN",
    "SHA256_PATTERN",
    "CodeStr",
    "ContractModel",
    "Identifier",
    "MediumStr",
    "NonEmptyStr",
    "Sha256Hex",
    "ShortStr",
    "UtcDatetime",
]

IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9_\-]{2,63}$"
"""Logical identifier pattern inherited from the legacy ``document_id`` contract."""


class ContractModel(BaseModel):
    """Base class for every versioned contract object.

    * ``extra="forbid"`` — an unexpected field is a contract violation, not noise.
    * ``frozen=True`` — handoff packages and cards are immutable by construction.
    * ``str_strip_whitespace=True`` — surrounding whitespace never reaches a
      checksum, a quote comparison, or a rendered identifier.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_default=True,
        validate_assignment=False,
        ser_json_timedelta="iso8601",
    )


NonEmptyStr = Annotated[str, StringConstraints(min_length=1, max_length=8192)]
"""A string that must carry content; empty strings are rejected."""

ShortStr = Annotated[str, StringConstraints(min_length=1, max_length=256)]
"""Short label-like string (names, versions, provider identifiers)."""

MediumStr = Annotated[str, StringConstraints(min_length=1, max_length=512)]
"""Identifier-like string with room for logical URIs."""

Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN)]
"""Logical identifier: lowercase, ``[a-z0-9_-]``, 3..64 characters."""

CodeStr = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", min_length=2, max_length=64),
]
"""Machine-readable error/check code, e.g. ``evidence_missing``."""


SHA256_PATTERN = r"^[0-9a-f]{64}$"
"""Lower-case SHA-256 hex digest.

Lower case is required rather than normalised so that the Pydantic rule and the
exported JSON Schema ``pattern`` agree exactly; a producer normalises before building
a contract object.
"""

Sha256Hex = Annotated[str, StringConstraints(pattern=SHA256_PATTERN, max_length=64)]
"""Lower-case SHA-256 hex digest; absolute paths and truncated digests are rejected."""


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            "datetime must be timezone-aware; a naive datetime cannot be ordered "
            "across runs and is rejected"
        )
    if value.utcoffset() != timedelta(0):
        raise ValueError(
            "datetime must be expressed in UTC (offset +00:00) to keep run metadata "
            "reproducible; convert the value before building the contract object"
        )
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
"""Timezone-aware UTC datetime; ISO 8601 serialization is handled by Pydantic."""
