"""``Agent1BVerificationReportV1`` — the verifier's own output contract.

A verification report never rewrites the Agent 1A card.  It states, per card field,
whether the original document supports the claim, and it carries its own evidence
(the verifier reads the article independently).  Corrections are a separate procedure
that produces a **new** package version; history is preserved.

Finding statuses are a closed set with distinct semantics:

``verified``
    The claim is supported by the verifier's own evidence from the original.
``partially_supported``
    The source supports part of the claim (for example the sign but not the
    magnitude, or only one of several listed items).
``unsupported``
    The source does not contain support for the claim, but does not contradict it.
``contradicted``
    The source states something incompatible with the claim (wrong sign, wrong
    number, wrong sample, reversed direction).
``not_verifiable``
    The claim cannot be checked with the material available (for example an
    unreadable OCR page); this is a limitation, not a pass.
``not_checked``
    The verifier did not examine this field; it must never be counted as verified.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    NonEmptyStr,
    ShortStr,
    UtcDatetime,
)
from research_platform.domain.evidence.v2_0 import EvidenceRecord

__all__ = [
    "Agent1BVerificationReportV1",
    "FindingSeverity",
    "FindingStatus",
    "ScientificReviewStatus",
    "VerificationFinding",
    "validate_findings_against_ledger",
]

_FIELD_PATH_PATTERN: Final = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
FieldPath = Annotated[str, StringConstraints(pattern=_FIELD_PATH_PATTERN, max_length=128)]


class FindingStatus(StrEnum):
    """Support verdict for one card field."""

    VERIFIED = "verified"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NOT_VERIFIABLE = "not_verifiable"
    NOT_CHECKED = "not_checked"


class FindingSeverity(StrEnum):
    """Impact of a finding on the usability of the card."""

    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class ScientificReviewStatus(StrEnum):
    """Review-level verdict of the whole package (ADD §13.5)."""

    PENDING = "pending"
    IN_REVIEW = "in_review"
    VERIFIED_WITH_ISSUES = "verified_with_issues"
    VERIFIED = "verified"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


_SERIOUS_FINDINGS: Final = frozenset({FindingStatus.UNSUPPORTED, FindingStatus.CONTRADICTED})
_CRITICAL_SEVERITIES: Final = frozenset({FindingSeverity.MAJOR, FindingSeverity.CRITICAL})


class VerificationFinding(ContractModel):
    """One verifier finding about one card field."""

    finding_id: Identifier
    field_path: FieldPath
    status: FindingStatus
    severity: FindingSeverity
    comment: NonEmptyStr
    agent_1a_evidence_ids: Annotated[list[Identifier], Field(default_factory=list, max_length=16)]
    agent_1b_evidence: Annotated[list[EvidenceRecord], Field(default_factory=list, max_length=16)]

    @model_validator(mode="after")
    def _status_and_evidence_agree(self) -> VerificationFinding:
        if self.status is FindingStatus.VERIFIED and not self.agent_1b_evidence:
            raise ValueError(
                "status 'verified' requires at least one of the verifier's own evidence "
                "records; a verdict without evidence is not a verification"
            )
        if self.status is FindingStatus.NOT_CHECKED and self.agent_1b_evidence:
            raise ValueError(
                "status 'not_checked' must not carry evidence; use 'not_verifiable' when "
                "the material could not be checked"
            )
        if self.status in _SERIOUS_FINDINGS and self.severity not in _CRITICAL_SEVERITIES:
            raise ValueError(
                f"status {self.status.value!r} is a serious support defect and must be "
                "reported as 'major' or 'critical' severity"
            )
        return self

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> VerificationFinding:
        ids = self.agent_1a_evidence_ids
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(
                "duplicate agent_1a_evidence_ids in one finding: " + ", ".join(duplicates)
            )
        return self


class Agent1BVerificationReportV1(ContractModel):
    """Independent verification report produced by Agent 1B."""

    contract_version: Literal["1.0"] = "1.0"
    report_id: Identifier
    agent_1a_package_id: Identifier
    agent_1a_contract_version: ShortStr

    agent_id: Literal["1B"] = "1B"
    agent_version: ShortStr
    prompt_version: ShortStr
    model_provider: ShortStr
    model_id: ShortStr
    model_configuration_fingerprint: ShortStr
    started_at: UtcDatetime
    completed_at: UtcDatetime

    scientific_review_status: ScientificReviewStatus
    summary: NonEmptyStr
    findings: Annotated[list[VerificationFinding], Field(max_length=1_000)]

    @model_validator(mode="after")
    def _timestamps_are_ordered(self) -> Agent1BVerificationReportV1:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self

    @model_validator(mode="after")
    def _finding_ids_are_unique(self) -> Agent1BVerificationReportV1:
        counts = Counter(finding.finding_id for finding in self.findings)
        duplicates = sorted(finding_id for finding_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError("duplicate finding_id values: " + ", ".join(duplicates))
        return self

    @model_validator(mode="after")
    def _one_finding_per_field(self) -> Agent1BVerificationReportV1:
        counts = Counter(finding.field_path for finding in self.findings)
        duplicates = sorted(path for path, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(
                "a field must receive exactly one verdict per report; duplicated field_path: "
                + ", ".join(duplicates)
            )
        return self

    @model_validator(mode="after")
    def _verdict_agrees_with_findings(self) -> Agent1BVerificationReportV1:
        statuses = {finding.status for finding in self.findings}
        review = self.scientific_review_status

        if review is ScientificReviewStatus.VERIFIED:
            if not self.findings:
                raise ValueError(
                    "scientific_review_status 'verified' requires at least one finding; an "
                    "empty report cannot verify anything"
                )
            if statuses != {FindingStatus.VERIFIED}:
                offending = ", ".join(
                    sorted(item.value for item in statuses - {FindingStatus.VERIFIED})
                )
                raise ValueError(
                    "scientific_review_status 'verified' conflicts with non-verified "
                    f"findings: {offending}. Use 'verified_with_issues' or "
                    "'human_review_required'"
                )
        if review is ScientificReviewStatus.VERIFIED_WITH_ISSUES and statuses == {
            FindingStatus.VERIFIED
        }:
            raise ValueError(
                "scientific_review_status 'verified_with_issues' requires at least one "
                "finding that is not 'verified'"
            )
        if review is ScientificReviewStatus.HUMAN_REVIEW_REQUIRED and not any(
            finding.severity is FindingSeverity.CRITICAL for finding in self.findings
        ):
            raise ValueError(
                "scientific_review_status 'human_review_required' requires at least one "
                "critical finding that justifies the escalation"
            )
        return self

    @property
    def has_blocking_findings(self) -> bool:
        """Whether any finding should stop automatic downstream use of the card."""
        return any(finding.status is FindingStatus.CONTRADICTED for finding in self.findings)


def validate_findings_against_ledger(
    report: Agent1BVerificationReportV1, evidence_ids: Sequence[str]
) -> None:
    """Check that every cited Agent 1A evidence id exists in the reviewed package.

    Raises:
        ValueError: a finding cites an ``evidence_id`` that is not present in the
            reviewed ledger, which would make the finding untraceable.
    """
    known = set(evidence_ids)
    for finding in report.findings:
        unknown = sorted(set(finding.agent_1a_evidence_ids) - known)
        if unknown:
            raise ValueError(
                f"finding {finding.finding_id!r} cites evidence ids absent from the "
                f"reviewed package: {', '.join(unknown)}"
            )
