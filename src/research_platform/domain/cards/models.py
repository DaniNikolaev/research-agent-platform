"""Agent 1A domain card: nine sections, twenty-seven content fields.

The field names, field groups and status vocabulary are inherited from the legacy
``card_schema.json`` v1.0 without semantic change (see
``docs/migration/v1_to_v2_mapping.md``).  What v2 adds is provenance: a field no
longer embeds a free-form ``evidence`` object with model-chosen page numbers, it
*references* ``evidence_id`` values of the package-level evidence ledger, and the
ledger is produced deterministically.

Status rules (all four statuses are checked by code, not only by documentation):

===============  ==============================  ============================
status           value                           evidence_refs
===============  ==============================  ============================
``found``        non-empty                       at least one reference
``not_found``    ``null`` / ``[]``               empty
``not_applicable`` ``null`` / ``[]``             empty
``unclear``      partial value *or* references   references *or* a value
===============  ==============================  ============================

``unclear`` cannot become a place to hide a format error: a field with neither a
value nor an evidence reference is not "unclear", it is ``not_found``.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Annotated, Any, Final

from pydantic import Field, model_validator

from research_platform.domain.base import ContractModel, Identifier, NonEmptyStr

__all__ = [
    "CARD_FIELD_PATHS",
    "CARD_SECTIONS",
    "Agent1ACardV2",
    "Bibliography",
    "DataSection",
    "EvidenceReference",
    "FieldStatus",
    "Findings",
    "ResearchDesign",
    "StringListField",
    "TextField",
    "VariablesSection",
    "YearField",
    "iter_card_fields",
]


class FieldStatus(StrEnum):
    """Extraction status of a single card field."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    UNCLEAR = "unclear"
    NOT_APPLICABLE = "not_applicable"


_EMPTY_STATUSES: Final = (FieldStatus.NOT_FOUND, FieldStatus.NOT_APPLICABLE)


class EvidenceReference(ContractModel):
    """Reference from a card field to an element of the evidence ledger.

    ``quote``, ``page`` and ``section_path`` are optional *denormalized read fields*
    for human review.  When present they must be byte-identical to the referenced
    ledger record; the package-level validator enforces that, so they can never
    drift into a second, competing source of truth.
    """

    evidence_id: Identifier
    quote: NonEmptyStr | None = None
    page: Annotated[int | None, Field(default=None, ge=1)] = None
    section_path: Annotated[list[NonEmptyStr] | None, Field(default=None, max_length=16)] = None


def _check_status_rules(
    status: FieldStatus,
    value_is_empty: bool,
    evidence_count: int,
) -> None:
    if status is FieldStatus.FOUND:
        if value_is_empty:
            raise ValueError("status 'found' requires a non-empty value")
        if evidence_count == 0:
            raise ValueError("status 'found' requires at least one evidence reference")
        return
    if status in _EMPTY_STATUSES:
        if not value_is_empty:
            raise ValueError(
                f"status {status.value!r} requires an empty value; "
                "a non-empty value must be reported as 'found' or 'unclear'"
            )
        if evidence_count:
            raise ValueError(f"status {status.value!r} requires an empty evidence reference list")
        return
    # FieldStatus.UNCLEAR
    if value_is_empty and evidence_count == 0:
        raise ValueError(
            "status 'unclear' requires either a partial value or at least one evidence "
            "reference; use 'not_found' when the document really has nothing"
        )


def _check_unique_references(references: list[EvidenceReference]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for reference in references:
        if reference.evidence_id in seen:
            duplicates.add(reference.evidence_id)
        seen.add(reference.evidence_id)
    if duplicates:
        raise ValueError(
            "duplicate evidence_id references in one field: " + ", ".join(sorted(duplicates))
        )


class TextField(ContractModel):
    """Free-text card field (legacy ``textField``)."""

    value: NonEmptyStr | None = None
    status: FieldStatus
    evidence_refs: list[EvidenceReference] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _status_rules(self) -> TextField:
        _check_status_rules(self.status, self.value is None, len(self.evidence_refs))
        _check_unique_references(self.evidence_refs)
        return self


class StringListField(ContractModel):
    """List-of-strings card field (legacy ``stringListField``)."""

    value: Annotated[list[NonEmptyStr] | None, Field(default=None, max_length=200)] = None
    status: FieldStatus
    evidence_refs: list[EvidenceReference] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _status_rules(self) -> StringListField:
        is_empty = self.value is None or len(self.value) == 0
        _check_status_rules(self.status, is_empty, len(self.evidence_refs))
        _check_unique_references(self.evidence_refs)
        return self

    @model_validator(mode="after")
    def _items_are_unique(self) -> StringListField:
        if self.value is None:
            return self
        seen: set[str] = set()
        duplicates: set[str] = set()
        for item in self.value:
            if item in seen:
                duplicates.add(item)
            seen.add(item)
        if duplicates:
            raise ValueError(
                "duplicate list items in a stringListField: " + ", ".join(sorted(duplicates))
            )
        return self


class YearField(ContractModel):
    """Publication-year card field (legacy ``yearField``)."""

    value: Annotated[int | None, Field(default=None, ge=1500, le=2100)] = None
    status: FieldStatus
    evidence_refs: list[EvidenceReference] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _status_rules(self) -> YearField:
        _check_status_rules(self.status, self.value is None, len(self.evidence_refs))
        _check_unique_references(self.evidence_refs)
        return self


class Bibliography(ContractModel):
    """G1 — bibliographic identification of the article."""

    title: TextField
    authors: StringListField
    year: YearField
    journal: TextField
    doi: TextField
    keywords: StringListField


class ResearchDesign(ContractModel):
    """G2 — research problem, objective, questions, hypotheses, framework."""

    research_problem: TextField
    research_objective: TextField
    research_questions: StringListField
    hypotheses: StringListField
    theoretical_framework: StringListField
    literature_context: TextField


class DataSection(ContractModel):
    """G3 — data description."""

    sample: TextField
    period: TextField
    geographical_scope: TextField
    data_sources: StringListField
    observations: TextField


class VariablesSection(ContractModel):
    """G3 — variable roles."""

    dependent: StringListField
    independent: StringListField
    controls: StringListField
    other_variables: StringListField


class Findings(ContractModel):
    """G4 — results and the authors' own conclusions."""

    main_findings: StringListField
    authors_conclusions: StringListField


CARD_SECTIONS: Final[tuple[str, ...]] = (
    "bibliography",
    "research_design",
    "methodology",
    "data",
    "variables",
    "findings",
    "limitations",
    "future_research",
    "contribution",
)

CARD_FIELD_PATHS: Final[tuple[str, ...]] = (
    "bibliography.title",
    "bibliography.authors",
    "bibliography.year",
    "bibliography.journal",
    "bibliography.doi",
    "bibliography.keywords",
    "research_design.research_problem",
    "research_design.research_objective",
    "research_design.research_questions",
    "research_design.hypotheses",
    "research_design.theoretical_framework",
    "research_design.literature_context",
    "methodology",
    "data.sample",
    "data.period",
    "data.geographical_scope",
    "data.data_sources",
    "data.observations",
    "variables.dependent",
    "variables.independent",
    "variables.controls",
    "variables.other_variables",
    "findings.main_findings",
    "findings.authors_conclusions",
    "limitations",
    "future_research",
    "contribution",
)
"""The twenty-seven legacy content fields, as dotted paths."""


class Agent1ACardV2(ContractModel):
    """Agent 1A card contract v2 — nine sections, twenty-seven fields."""

    bibliography: Bibliography
    research_design: ResearchDesign
    methodology: StringListField
    data: DataSection
    variables: VariablesSection
    findings: Findings
    limitations: StringListField
    future_research: StringListField
    contribution: StringListField


def iter_card_fields(
    card: Agent1ACardV2,
) -> Iterator[tuple[str, TextField | StringListField | YearField]]:
    """Yield ``(dotted_path, field)`` for all twenty-seven content fields."""
    for path in CARD_FIELD_PATHS:
        node: Any = card
        for part in path.split("."):
            node = getattr(node, part)
        yield path, node
