"""Evidence contract 2.1: candidate (LLM proposal) vs record (deterministic result).

The split is the core provenance guarantee of the platform:

``EvidenceCandidate``
    What a model is allowed to *propose*: a presumed verbatim quote plus **hints**
    (page, section, block id) and the surrounding context.  Hints are named "hint" on
    purpose — they carry no authority.  The model cannot express a character span, a
    bounding box, a source hash, an occurrence index or a resolution status: those
    fields do not exist on the model, and ``extra="forbid"`` rejects an attempt to add
    them.

``EvidenceRecord``
    What the deterministic Python resolver emits after checking the candidate against
    the canonical parsed document: page number, section path, character span, bounding
    box, resolution status, match type, occurrence index, and the parser/resolver
    provenance needed to reproduce the result.

A ``fuzzy`` match can never be reported as a resolved exact match; a repeated quote
never gets a location the model asked for.
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
    "EvidenceCandidateList",
    "EvidenceRecord",
    "ExtractionMethod",
    "MatchType",
    "ResolutionStatus",
    "TableRef",
    "VerificationMethod",
]

FIELD_PATH_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
FieldPath = Annotated[str, StringConstraints(pattern=FIELD_PATH_PATTERN, max_length=128)]

_QUOTE_MAX_CHARS: Final = 2000
_CONTEXT_MAX_CHARS: Final = 500


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


class ResolutionStatus(StrEnum):
    """Outcome of deterministic resolution (does the quote have a trustworthy location?).

    ``resolved``
        Exactly one location was found and verified.
    ``ambiguous``
        The quote text matches at more than one location (or the span/context check
        cannot single one out).  The resolver must **not** pick one at random and must
        not report a fabricated position as certain.
    ``unresolved``
        No exact (or acceptably normalized) match was found.  A fuzzy similarity is
        reported through :class:`MatchType` and never upgrades this status.
    """

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class MatchType(StrEnum):
    """How the quote text compared with the source text."""

    EXACT = "exact"
    NORMALIZED = "normalized"
    FUZZY = "fuzzy"
    NONE = "none"


class VerificationMethod(StrEnum):
    """Mechanism that produced the comparison result; recorded for audit."""

    EXACT_STRING_MATCH = "exact_string_match"
    NORMALIZED_STRING_MATCH = "normalized_string_match"
    FUZZY_STRING_MATCH = "fuzzy_string_match"
    NO_MATCH_FOUND = "no_match_found"


class ExtractionMethod(StrEnum):
    """How the underlying text was obtained (OCR provenance, ADD §9.3)."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    UNKNOWN = "unknown"


_MATCH_TYPE_TO_METHOD: Final[dict[MatchType, VerificationMethod]] = {
    MatchType.EXACT: VerificationMethod.EXACT_STRING_MATCH,
    MatchType.NORMALIZED: VerificationMethod.NORMALIZED_STRING_MATCH,
    MatchType.FUZZY: VerificationMethod.FUZZY_STRING_MATCH,
    MatchType.NONE: VerificationMethod.NO_MATCH_FOUND,
}

_ALLOWED_PAIRS: Final[frozenset[tuple[ResolutionStatus, MatchType]]] = frozenset(
    {
        (ResolutionStatus.RESOLVED, MatchType.EXACT),
        (ResolutionStatus.RESOLVED, MatchType.NORMALIZED),
        (ResolutionStatus.AMBIGUOUS, MatchType.EXACT),
        (ResolutionStatus.AMBIGUOUS, MatchType.NORMALIZED),
        (ResolutionStatus.AMBIGUOUS, MatchType.FUZZY),
        (ResolutionStatus.UNRESOLVED, MatchType.FUZZY),
        (ResolutionStatus.UNRESOLVED, MatchType.NONE),
    }
)


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
    """A model's proposal of evidence, expressed only as a quote plus hints.

    Required: the quote.  Everything else is an untrusted hint that the resolver may
    confirm, ignore or contradict.  ``block_id_hint`` is additionally checked by the
    workflow against the set of blocks that were actually supplied to the model; the
    contract itself can only constrain the shape.
    """

    candidate_id: Identifier
    document_id: Identifier
    field_path: FieldPath
    quote: Annotated[
        str, StringConstraints(min_length=1, max_length=_QUOTE_MAX_CHARS, strip_whitespace=True)
    ]
    page_hint: Annotated[int | None, Field(default=None, ge=1)] = None
    section_hint: ShortStr | None = None
    block_id_hint: ShortStr | None = None
    context_before: Annotated[str | None, StringConstraints(max_length=_CONTEXT_MAX_CHARS)] = None
    context_after: Annotated[str | None, StringConstraints(max_length=_CONTEXT_MAX_CHARS)] = None
    confidence: Annotated[float | None, Field(default=None, ge=0.0, le=1.0)] = None
    note: NonEmptyStr | None = None


class EvidenceCandidateList(ContractModel):
    """Serialisable envelope of one extraction call — the structured-output contract.

    This is the artefact type ``extraction_candidates``: what the model returns, before
    any resolver has run.
    """

    contract_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")] = "2.1"
    document_id: Identifier
    field_group: ShortStr
    candidates: Annotated[list[EvidenceCandidate], Field(default_factory=list, max_length=200)]

    @model_validator(mode="after")
    def _candidates_belong_to_the_document(self) -> EvidenceCandidateList:
        foreign = sorted({item.document_id for item in self.candidates} - {self.document_id})
        if foreign:
            raise ValueError(
                "candidates must belong to the envelope document; foreign document ids: "
                + ", ".join(foreign)
            )
        ids = [item.candidate_id for item in self.candidates]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError("duplicate candidate_id values: " + ", ".join(duplicates))
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
    quote: Annotated[
        str, StringConstraints(min_length=1, max_length=_QUOTE_MAX_CHARS, strip_whitespace=True)
    ]
    char_start: Annotated[int | None, Field(default=None, ge=0)] = None
    char_end: Annotated[int | None, Field(default=None, gt=0)] = None
    bbox: BoundingBox | None = None
    table_ref: TableRef | None = None
    occurrence_index: Annotated[int | None, Field(default=None, ge=0)] = None
    extraction_method: ExtractionMethod = ExtractionMethod.UNKNOWN
    match_status: ResolutionStatus
    match_type: MatchType
    verification_method: VerificationMethod
    parser_name: ShortStr
    parser_version: ShortStr
    resolver_version: ShortStr
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
    def _match_type_matches_verification_method(self) -> EvidenceRecord:
        expected = _MATCH_TYPE_TO_METHOD[self.match_type]
        if self.verification_method is not expected:
            raise ValueError(
                f"match_type {self.match_type.value!r} requires verification_method "
                f"{expected.value!r}, got {self.verification_method.value!r}"
            )
        return self

    @model_validator(mode="after")
    def _resolution_and_match_type_are_compatible(self) -> EvidenceRecord:
        pair = (self.match_status, self.match_type)
        if pair not in _ALLOWED_PAIRS:
            raise ValueError(
                f"match_status {self.match_status.value!r} cannot be combined with "
                f"match_type {self.match_type.value!r}; a fuzzy match never becomes a "
                "resolved exact match"
            )
        return self

    @model_validator(mode="after")
    def _resolved_record_has_a_verified_span(self) -> EvidenceRecord:
        if self.match_status is ResolutionStatus.RESOLVED:
            if self.char_start is None or self.char_end is None:
                raise ValueError(
                    "a resolved record requires the verified character span "
                    "(char_start and char_end)"
                )
            if self.occurrence_index is None:
                raise ValueError(
                    "a resolved record requires occurrence_index: the resolver records which "
                    "occurrence of the quote it verified"
                )
        return self

    @model_validator(mode="after")
    def _unresolved_record_has_no_position(self) -> EvidenceRecord:
        if self.match_status is ResolutionStatus.UNRESOLVED and self.occurrence_index is not None:
            raise ValueError("an unresolved record must not claim an occurrence_index")
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
