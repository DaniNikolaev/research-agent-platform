"""Frozen snapshot of the evidence contract as published in package contract **2.0**.

Historical record only.  Contract ``2.1`` replaces the ``match_status`` vocabulary with
``match_status`` (:class:`~research_platform.domain.evidence.models.ResolutionStatus`)
plus ``match_type``, requires the parser/resolver provenance and drops the
model-supplied ``quote_occurrence``.

Do not edit this module to satisfy a new requirement: publish a new version instead.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    Sha256Hex,
    ShortStr,
    UtcDatetime,
)

__all__ = [
    "BoundingBox",
    "ElementType",
    "EvidenceCandidate",
    "EvidenceRecord",
    "ExtractionMethod",
    "MatchStatus",
    "TableRef",
    "VerificationMethod",
]

_FIELD_PATH_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
FieldPath = Annotated[str, StringConstraints(pattern=_FIELD_PATH_PATTERN, max_length=128)]


class ElementType(StrEnum):
    """Type of the source element an evidence quote was taken from."""

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    TABLE_CELL = "table_cell"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    LIST_ITEM = "list_item"
    FORMULA = "formula"
    OTHER = "other"


class MatchStatus(StrEnum):
    """Outcome of deterministic quote resolution against the parsed document."""

    EXACT = "exact"
    NORMALIZED_EXACT = "normalized_exact"
    FUZZY_CANDIDATE = "fuzzy_candidate"
    UNRESOLVED = "unresolved"


class VerificationMethod(StrEnum):
    """Mechanism that produced ``match_status``; recorded for audit."""

    EXACT_STRING_MATCH = "exact_string_match"
    NORMALIZED_STRING_MATCH = "normalized_string_match"
    FUZZY_STRING_MATCH = "fuzzy_string_match"
    RESOLVER_UNRESOLVED = "resolver_unresolved"


class ExtractionMethod(StrEnum):
    """How the underlying text was obtained (OCR provenance, ADD §9.3)."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    UNKNOWN = "unknown"


_MATCH_TO_METHOD: Final[dict[MatchStatus, VerificationMethod]] = {
    MatchStatus.EXACT: VerificationMethod.EXACT_STRING_MATCH,
    MatchStatus.NORMALIZED_EXACT: VerificationMethod.NORMALIZED_STRING_MATCH,
    MatchStatus.FUZZY_CANDIDATE: VerificationMethod.FUZZY_STRING_MATCH,
    MatchStatus.UNRESOLVED: VerificationMethod.RESOLVER_UNRESOLVED,
}


class BoundingBox(BaseModel):
    """Axis-aligned box on a physical page, in PDF points (origin top-left)."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _is_a_positive_area(self) -> BoundingBox:
        for name, value in (("x0", self.x0), ("y0", self.y0), ("x1", self.x1), ("y1", self.y1)):
            if not math.isfinite(value):
                raise ValueError(f"BoundingBox.{name} must be a finite number")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("BoundingBox must have x1 > x0 and y1 > y0")
        return self


class TableRef(BaseModel):
    """Locator inside a table element (ADD §12.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    table_id: Identifier
    row_index: Annotated[int, Field(ge=0)]
    column_index: Annotated[int, Field(ge=0)]
    cell_id: ShortStr | None = None


class EvidenceCandidate(ContractModel):
    """A model's *proposal* of evidence — not yet evidence.

    Deliberately narrow: it can point at a ``block_id`` that was supplied to the
    model, quote a fragment, and state which occurrence inside the supplied context
    it means.  Coordinates and hashes are the resolver's job.
    """

    candidate_id: Identifier
    document_id: Identifier
    field_path: FieldPath
    block_id: ShortStr | None = None
    quote: NonEmptyStr | None = None
    quote_occurrence: Annotated[int | None, Field(default=None, ge=0)] = None
    confidence: Annotated[float | None, Field(default=None, ge=0.0, le=1.0)] = None
    note: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _has_a_usable_locator(self) -> EvidenceCandidate:
        if self.block_id is None and self.quote is None:
            raise ValueError(
                "an evidence candidate must carry at least one of block_id or quote; "
                "a candidate without a locator cannot be resolved"
            )
        if self.quote_occurrence is not None and self.quote is None:
            raise ValueError("quote_occurrence is meaningless without a quote")
        return self


class EvidenceRecord(ContractModel):
    """Canonical evidence element of the ``evidence_ledger``.

    ``evidence_id`` is unique inside one package; the ledger is the single source of
    truth, and card fields reference it by id only.
    """

    evidence_id: Identifier
    document_id: Identifier
    source_sha256: Sha256Hex
    block_id: ShortStr
    page: Annotated[int | None, Field(default=None, ge=1)] = None
    printed_page_label: ShortStr | None = None
    section_path: Annotated[list[NonEmptyStr] | None, Field(default=None, max_length=16)] = None
    element_type: ElementType
    quote: NonEmptyStr
    char_start: Annotated[int | None, Field(default=None, ge=0)] = None
    char_end: Annotated[int | None, Field(default=None, gt=0)] = None
    bbox: BoundingBox | None = None
    table_ref: TableRef | None = None
    extraction_method: ExtractionMethod = ExtractionMethod.UNKNOWN
    match_status: MatchStatus
    verification_method: VerificationMethod
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _char_span_is_well_formed(self) -> EvidenceRecord:
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start and char_end must be provided together")
        if (
            self.char_start is not None
            and self.char_end is not None
            and self.char_end <= self.char_start
        ):
            raise ValueError("char_end must be greater than char_start")
        return self

    @model_validator(mode="after")
    def _match_status_matches_verification_method(self) -> EvidenceRecord:
        expected = _MATCH_TO_METHOD[self.match_status]
        if self.verification_method is not expected:
            raise ValueError(
                f"match_status {self.match_status.value!r} requires "
                f"verification_method {expected.value!r}, got "
                f"{self.verification_method.value!r}"
            )
        return self

    @model_validator(mode="after")
    def _geometry_requires_a_page(self) -> EvidenceRecord:
        if self.bbox is not None and self.page is None:
            raise ValueError("bbox is a position on a physical page and requires page to be set")
        return self

    @model_validator(mode="after")
    def _table_reference_matches_element_type(self) -> EvidenceRecord:
        if self.table_ref is not None and self.element_type is not ElementType.TABLE_CELL:
            raise ValueError(
                "table_ref is only meaningful for element_type='table_cell', got "
                f"{self.element_type.value!r}"
            )
        return self
