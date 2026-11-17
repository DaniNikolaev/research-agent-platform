"""Internal extraction contracts of the Agent 1A workflow (iteration 3).

These models describe *technical* extraction output, not a scientific card:

* they are internal (not part of the published Agent 1A → 1B package),
* they carry ``execution_mode=simulation`` for the current fake-adapter stage,
* they keep evidence only as :class:`EvidenceCandidate` — nothing is resolved, verified or
  promoted to a canonical ``EvidenceRecord``.

The twenty-seven field keys come from the existing card contract, so the groups below cannot
drift away from the platform's own field vocabulary.  A field with no value is reported as
``empty``; it is never turned into ``not_found``, because a fake adapter returning no
candidate proves nothing about the source document.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    Sha256Hex,
    ShortStr,
    UtcDatetime,
)
from research_platform.domain.cards.models import CARD_FIELD_PATHS
from research_platform.domain.evidence.models import EvidenceCandidate

__all__ = [
    "EXTRACTION_RESULT_SCHEMA_VERSION",
    "EXTRACTION_WORKFLOW_VERSION",
    "GROUP_FIELD_KEYS",
    "PROMPT_SET_VERSION",
    "ExecutionMode",
    "ExtractionFieldValue",
    "ExtractionGroup",
    "ExtractionPlan",
    "ExtractionResult",
    "ExtractionRunManifest",
    "FieldValueStatus",
    "GroupExtractionResult",
    "GroupStatus",
    "PlanStep",
    "field_keys_of",
    "groups_in_order",
]

EXTRACTION_WORKFLOW_VERSION: Final = "1.0"
EXTRACTION_RESULT_SCHEMA_VERSION: Final = "1.0"
PROMPT_SET_VERSION: Final = "1.0"


class ExecutionMode(StrEnum):
    """How the result was produced."""

    SIMULATION = "simulation"
    """Deterministic fake adapter: no scientific content and no real model call."""

    REAL_MODEL = "real_model"
    """Reserved for a future provider adapter; never used in this iteration."""


class ExtractionGroup(StrEnum):
    """The four sequential extraction groups."""

    G1_BIBLIOGRAPHY = "g1_bibliography"
    G2_RESEARCH_DESIGN = "g2_research_design"
    G3_METHODS_DATA_VARIABLES = "g3_methods_data_variables"
    G4_FINDINGS_IMPLICATIONS = "g4_findings_implications"

    @property
    def order(self) -> int:
        """1-based position in the fixed sequence."""
        return list(ExtractionGroup).index(self) + 1

    @property
    def group_label(self) -> str:
        """Human-readable group name (``title`` is reserved by the contract model)."""
        return {
            ExtractionGroup.G1_BIBLIOGRAPHY: "Bibliography",
            ExtractionGroup.G2_RESEARCH_DESIGN: "Research Design",
            ExtractionGroup.G3_METHODS_DATA_VARIABLES: "Methods, Data & Variables",
            ExtractionGroup.G4_FINDINGS_IMPLICATIONS: "Findings & Implications",
        }[self]


GROUP_FIELD_KEYS: Final[dict[ExtractionGroup, tuple[str, ...]]] = {
    ExtractionGroup.G1_BIBLIOGRAPHY: (
        "bibliography.title",
        "bibliography.authors",
        "bibliography.year",
        "bibliography.journal",
        "bibliography.doi",
        "bibliography.keywords",
    ),
    ExtractionGroup.G2_RESEARCH_DESIGN: (
        "research_design.research_problem",
        "research_design.research_objective",
        "research_design.research_questions",
        "research_design.hypotheses",
        "research_design.theoretical_framework",
        "research_design.literature_context",
    ),
    ExtractionGroup.G3_METHODS_DATA_VARIABLES: (
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
    ),
    ExtractionGroup.G4_FINDINGS_IMPLICATIONS: (
        "findings.main_findings",
        "findings.authors_conclusions",
        "limitations",
        "future_research",
        "contribution",
    ),
}
"""Field keys per group; together they cover exactly the 27 card fields."""


def groups_in_order() -> tuple[ExtractionGroup, ...]:
    """The groups in their mandatory execution order."""
    return tuple(sorted(ExtractionGroup, key=lambda group: group.order))


def field_keys_of(group: ExtractionGroup) -> tuple[str, ...]:
    """Field keys of one group."""
    return GROUP_FIELD_KEYS[group]


class GroupStatus(StrEnum):
    """Status of one extraction group."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    VALIDATION_FAILED = "validation_failed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class FieldValueStatus(StrEnum):
    """Status of one proposed field value."""

    CANDIDATE = "candidate"
    """The model proposed a value; it is unverified."""
    EMPTY = "empty"
    """The model proposed nothing.  This is *not* ``not_found``."""


class ExtractionFieldValue(ContractModel):
    """One proposed field value from one group call."""

    field_key: Annotated[str, StringConstraints(min_length=3, max_length=64)]
    status: FieldValueStatus
    proposed_value: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _key_is_a_card_field(self) -> ExtractionFieldValue:
        if self.field_key not in CARD_FIELD_PATHS:
            raise ValueError(
                f"unknown field key {self.field_key!r}; fields come from the 27-field card contract"
            )
        return self

    @model_validator(mode="after")
    def _status_matches_the_value(self) -> ExtractionFieldValue:
        if self.status is FieldValueStatus.CANDIDATE and not (
            self.proposed_value and self.proposed_value.strip()
        ):
            raise ValueError("a 'candidate' field must carry a non-empty proposed value")
        if self.status is FieldValueStatus.EMPTY and self.proposed_value:
            raise ValueError("an 'empty' field must not carry a value")
        return self


class GroupExtractionResult(ContractModel):
    """Validated, persisted result of one extraction group."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = EXTRACTION_RESULT_SCHEMA_VERSION
    group: ExtractionGroup
    group_order: Annotated[int, Field(ge=1, le=4)]
    document_id: Identifier
    run_id: ShortStr
    document_run_id: ShortStr
    provider_id: ShortStr
    model_id: ShortStr
    execution_mode: ExecutionMode
    prompt_id: ShortStr
    prompt_version: ShortStr
    attempts: Annotated[int, Field(ge=1, le=10)] = 1
    status: GroupStatus
    fields: Annotated[list[ExtractionFieldValue], Field(min_length=0, max_length=32)] = Field(
        default_factory=list
    )
    evidence_candidates: Annotated[
        list[EvidenceCandidate], Field(default_factory=list, max_length=200)
    ] = Field(default_factory=list)
    warnings: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=50)] = Field(
        default_factory=list
    )
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _order_matches_the_group(self) -> GroupExtractionResult:
        if self.group_order != self.group.order:
            raise ValueError(
                f"group_order {self.group_order} does not match {self.group.value} "
                f"(order {self.group.order})"
            )
        return self

    @model_validator(mode="after")
    def _fields_belong_to_the_group(self) -> GroupExtractionResult:
        allowed = set(field_keys_of(self.group))
        for field_value in self.fields:
            if field_value.field_key not in allowed:
                raise ValueError(
                    f"field {field_value.field_key!r} does not belong to {self.group.value}"
                )
        return self

    @model_validator(mode="after")
    def _simulation_is_consistent(self) -> GroupExtractionResult:
        if self.provider_id == "fake" and self.execution_mode is not ExecutionMode.SIMULATION:
            raise ValueError(
                "the fake provider must report execution_mode='simulation'; its output is not "
                "a scientific extraction result"
            )
        return self


class PlanStep(ContractModel):
    """One planned group execution."""

    group: ExtractionGroup
    order: Annotated[int, Field(ge=1, le=4)]
    field_keys: Annotated[list[str], Field(min_length=1, max_length=16)]
    prompt_id: ShortStr
    prompt_version: ShortStr
    schema_id: ShortStr


class ExtractionPlan(ContractModel):
    """Fixed, versioned plan of the extraction run."""

    plan_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = EXTRACTION_WORKFLOW_VERSION
    document_id: Identifier
    run_id: ShortStr
    document_run_id: ShortStr
    retrieval_mode: Annotated[str, Field(pattern=r"^(full_context|rag|hybrid|not_applicable)$")] = (
        "full_context"
    )
    steps: Annotated[list[PlanStep], Field(min_length=1, max_length=4)]

    @model_validator(mode="after")
    def _steps_are_the_four_groups_in_order(self) -> ExtractionPlan:
        expected = [group.value for group in groups_in_order()]
        actual = [step.group.value for step in self.steps]
        if actual != expected:
            raise ValueError(
                f"the plan must contain the four groups in order {expected}, got {actual}"
            )
        return self


class ExtractionResult(ContractModel):
    """Aggregate of the four groups for one run."""

    schema_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = EXTRACTION_RESULT_SCHEMA_VERSION
    run_id: ShortStr
    document_run_id: ShortStr
    document_id: Identifier
    source_sha256: Sha256Hex
    execution_mode: ExecutionMode
    completed_groups: Annotated[list[ExtractionGroup], Field(default_factory=list, max_length=4)]
    group_results: Annotated[
        list[GroupExtractionResult], Field(default_factory=list, max_length=4)
    ] = Field(default_factory=list)
    warnings: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=50)] = Field(
        default_factory=list
    )
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _completed_groups_match_the_results(self) -> ExtractionResult:
        from_results = {
            result.group for result in self.group_results if result.status is GroupStatus.COMPLETED
        }
        if set(self.completed_groups) != from_results:
            raise ValueError("completed_groups must match the groups whose status is 'completed'")
        return self


class ExtractionRunManifest(ContractModel):
    """Technical manifest of one Agent 1A run: enough to reconstruct it by ``run_id``."""

    manifest_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = EXTRACTION_WORKFLOW_VERSION
    run_id: ShortStr
    document_run_id: ShortStr
    document_id: Identifier
    source_sha256: Sha256Hex
    agent_id: ShortStr
    agent_version: ShortStr
    workflow_version: ShortStr
    graph_state_version: ShortStr
    retrieval_mode: Annotated[str, Field(pattern=r"^(full_context|rag|hybrid|not_applicable)$")]
    execution_mode: ExecutionMode
    provider_id: ShortStr
    model_id: ShortStr
    prompt_set_version: ShortStr
    group_statuses: Annotated[dict[ShortStr, ShortStr], Field(default_factory=dict)]
    group_result_uris: Annotated[dict[ShortStr, ShortStr], Field(default_factory=dict)] = Field(
        default_factory=dict
    )
    quality_status: ShortStr
    source_manifest_uri: ShortStr
    parsed_document_uri: ShortStr
    canonical_text_uri: ShortStr
    quality_report_uri: ShortStr
    extraction_plan_uri: ShortStr | None = None
    extraction_result_uri: ShortStr | None = None
    final_status: ShortStr
    retry_counts: Annotated[dict[ShortStr, int], Field(default_factory=dict)] = Field(
        default_factory=dict
    )
    warnings: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=100)] = Field(
        default_factory=list
    )
    errors: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=50)] = Field(
        default_factory=list
    )
    started_at: UtcDatetime
    completed_at: UtcDatetime | None = None
    created_at: UtcDatetime
