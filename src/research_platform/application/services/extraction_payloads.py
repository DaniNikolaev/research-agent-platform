"""Model-facing payload of one extraction group.

This is deliberately *not* the stored result: it is the smallest shape a model may return
(field keys with proposed values and quotes) and it is strictly validated before anything is
persisted.  Identifiers, provider metadata, prompt versions and the simulation marker are added
by the workflow, never by the model.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import ContractModel, NonEmptyStr
from research_platform.domain.extraction.models import (
    ExtractionGroup,
    FieldValueStatus,
    field_keys_of,
)

__all__ = ["GroupPayload", "PayloadCandidate", "PayloadField"]


class PayloadField(ContractModel):
    """One proposed field value inside a model answer."""

    field_key: NonEmptyStr
    status: FieldValueStatus
    proposed_value: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _status_matches_value(self) -> PayloadField:
        if self.status is FieldValueStatus.CANDIDATE and not (
            self.proposed_value and self.proposed_value.strip()
        ):
            raise ValueError("a 'candidate' entry must carry a proposed value")
        if self.status is FieldValueStatus.EMPTY and self.proposed_value:
            raise ValueError("an 'empty' entry must not carry a value")
        return self


class PayloadCandidate(ContractModel):
    """One quote the model offers as support for a field."""

    field_path: NonEmptyStr
    quote: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    page_hint: int | None = Field(default=None, ge=1)


class GroupPayload(ContractModel):
    """Strict shape of one structured extraction answer."""

    group: ExtractionGroup
    fields: Annotated[list[PayloadField], Field(min_length=0, max_length=32)] = Field(
        default_factory=list
    )
    evidence_candidates: Annotated[
        list[PayloadCandidate], Field(default_factory=list, max_length=200)
    ] = Field(default_factory=list)
    warnings: Annotated[list[NonEmptyStr], Field(default_factory=list, max_length=50)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _fields_belong_to_the_group(self) -> GroupPayload:
        allowed = set(field_keys_of(self.group))
        seen: set[str] = set()
        for field in self.fields:
            if field.field_key not in allowed:
                raise ValueError(
                    f"field {field.field_key!r} does not belong to group {self.group.value}"
                )
            if field.field_key in seen:
                raise ValueError(f"field {field.field_key!r} appears twice")
            seen.add(field.field_key)
        return self

    @model_validator(mode="after")
    def _candidates_reference_group_fields(self) -> GroupPayload:
        allowed = set(field_keys_of(self.group))
        for candidate in self.evidence_candidates:
            if candidate.field_path not in allowed:
                raise ValueError(
                    f"candidate field {candidate.field_path!r} does not belong to "
                    f"group {self.group.value}"
                )
        return self
