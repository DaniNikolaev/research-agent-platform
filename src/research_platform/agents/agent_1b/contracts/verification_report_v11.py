"""``Agent1BVerificationReportV11`` — verification report contract 1.1.

Changes relative to 1.0:

* a new finding status ``omitted`` — the source contains the information but the Agent 1A
  card does not (the coverage trail is what makes this detectable at all);
* an optional per-finding ``coverage_verdict`` with which the verifier can confirm or
  dispute the extractor's claim of complete search;
* ``coverage_reviewed`` records that the verifier actually inspected the coverage trail.

The report still never rewrites the Agent 1A card: corrections are a separate procedure
producing a new package version.
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
from research_platform.domain.evidence.models import EvidenceRecord

__all__ = [
    "Agent1BVerificationReportV11",
    "CoverageVerdict",
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
    OMITTED = "omitted"
    """The source states it, the card does not contain it (a completeness defect)."""

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


class CoverageVerdict(StrEnum):
    """How the verifier rates the extractor's coverage claim for one field."""

    CONFIRMED_COMPLETE = "confirmed_complete"
    CONFIRMED_PARTIAL = "confirmed_partial"
    DISPUTED_COMPLETE = "disputed_complete"
    """The extractor claimed complete search; the verifier found more material."""

    COVERAGE_NOT_RECORDED = "coverage_not_recorded"
    """The received package predates the coverage trail; coverage cannot be judged."""


_SERIOUS_FINDINGS: Final = frozenset(
    {
        FindingStatus.UNSUPPORTED,
        FindingStatus.CONTRADICTED,
        FindingStatus.OMITTED,
    }
)
_CRITICAL_SEVERITIES: Final = frozenset({FindingSeverity.MAJOR, FindingSeverity.CRITICAL})

_SEVERITY_RANK: Final[dict[FindingSeverity, int]] = {
    FindingSeverity.INFO: 0,
    FindingSeverity.MINOR: 1,
    FindingSeverity.MAJOR: 2,
    FindingSeverity.CRITICAL: 3,
}


class VerificationFinding(ContractModel):
    """One verifier finding about one card field."""

    finding_id: Identifier
    field_path: FieldPath
    status: FindingStatus
    severity: FindingSeverity
    comment: NonEmptyStr
    agent_1a_evidence_ids: Annotated[list[Identifier], Field(default_factory=list, max_length=16)]
    agent_1b_evidence: Annotated[list[EvidenceRecord], Field(default_factory=list, max_length=16)]
    coverage_verdict: CoverageVerdict | None = None

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
        if self.status is FindingStatus.OMITTED:
            if not self.agent_1b_evidence:
                raise ValueError(
                    "status 'omitted' requires the verifier's own evidence: a completeness "
                    "defect must show the material the card missed"
                )
            if self.severity not in _CRITICAL_SEVERITIES:
                raise ValueError(
                    "status 'omitted' is a completeness defect and must be reported as "
                    "'major' or 'critical' severity"
                )
        if self.status in _SERIOUS_FINDINGS and self.severity not in _CRITICAL_SEVERITIES:
            raise ValueError(
                f"status {self.status.value!r} is a serious support defect and must be "
                "reported as 'major' or 'critical' severity"
            )
        return self

    @model_validator(mode="after")
    def _coverage_verdict_is_evidenced(self) -> VerificationFinding:
        if (
            self.coverage_verdict is CoverageVerdict.DISPUTED_COMPLETE
            and not self.agent_1b_evidence
        ):
            raise ValueError(
                "coverage_verdict 'disputed_complete' requires the verifier's own evidence: "
                "disputing a complete-search claim needs material the extractor missed"
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


class Agent1BVerificationReportV11(ContractModel):
    """Independent verification report produced by Agent 1B (contract 1.1)."""

    contract_version: Literal["1.1"] = "1.1"
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
    coverage_reviewed: bool = False
    summary: NonEmptyStr
    findings: Annotated[list[VerificationFinding], Field(max_length=1_000)]

    @model_validator(mode="after")
    def _timestamps_are_ordered(self) -> Agent1BVerificationReportV11:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self

    @model_validator(mode="after")
    def _finding_ids_are_unique(self) -> Agent1BVerificationReportV11:
        counts = Counter(finding.finding_id for finding in self.findings)
        duplicates = sorted(finding_id for finding_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError("duplicate finding_id values: " + ", ".join(duplicates))
        return self

    @model_validator(mode="after")
    def _one_finding_per_field(self) -> Agent1BVerificationReportV11:
        counts = Counter(finding.field_path for finding in self.findings)
        duplicates = sorted(path for path, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(
                "a field must receive exactly one verdict per report; duplicated field_path: "
                + ", ".join(duplicates)
            )
        return self

    @model_validator(mode="after")
    def _coverage_review_is_declared(self) -> Agent1BVerificationReportV11:
        claimed = any(finding.coverage_verdict is not None for finding in self.findings)
        if claimed and not self.coverage_reviewed:
            raise ValueError(
                "a finding carries coverage_verdict but coverage_reviewed is false: "
                "declaring a coverage verdict means the trail was inspected"
            )
        return self

    @model_validator(mode="after")
    def _verdict_agrees_with_findings(self) -> Agent1BVerificationReportV11:
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
            disputed = [
                finding.finding_id
                for finding in self.findings
                if finding.coverage_verdict is CoverageVerdict.DISPUTED_COMPLETE
            ]
            if disputed:
                raise ValueError(
                    "scientific_review_status 'verified' conflicts with a disputed complete-"
                    "search claim: " + ", ".join(disputed)
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

    @property
    def worst_severity(self) -> FindingSeverity:
        """Highest severity among the findings (``info`` for an empty report)."""
        if not self.findings:
            return FindingSeverity.INFO
        return max(self.findings, key=lambda item: _SEVERITY_RANK[item.severity]).severity


def validate_findings_against_ledger(
    report: Agent1BVerificationReportV11, evidence_ids: Sequence[str]
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
