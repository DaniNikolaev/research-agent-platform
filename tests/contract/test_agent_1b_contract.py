"""Agent 1B contracts: input 1.1 (with coverage) and report 1.1, plus frozen 1.0."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.builders import (
    ORIGINAL_URI,
    SOURCE_SHA256,
    evidence_record,
    valid_agent_1b_input,
    valid_agent_1b_input_v10,
    valid_package_v20,
    valid_verification_report,
    valid_verification_report_v10,
)

from research_platform.agents.agent_1b.contracts.input_package import (
    Agent1BInputPackageV1,
)
from research_platform.agents.agent_1b.contracts.input_package_v11 import (
    COVERAGE_RECORDED_CONTRACT_VERSIONS,
    SUPPORTED_AGENT_1A_CONTRACT_VERSIONS,
    Agent1BInputPackageV11,
)
from research_platform.agents.agent_1b.contracts.verification_report import (
    Agent1BVerificationReportV1,
)
from research_platform.agents.agent_1b.contracts.verification_report_v11 import (
    Agent1BVerificationReportV11,
    CoverageVerdict,
    FindingSeverity,
    FindingStatus,
    ScientificReviewStatus,
    VerificationFinding,
    validate_findings_against_ledger,
)


def agent_1b_input(**overrides: Any) -> dict[str, Any]:
    payload = valid_agent_1b_input()
    payload.update(overrides)
    return payload


def input_from_v20_package() -> dict[str, Any]:
    """A 1.1 input that received a historical 2.0 package (no coverage trail)."""
    package = valid_package_v20()
    payload = valid_agent_1b_input()
    payload["agent_1a_contract_version"] = "2.0"
    payload["document"] = package["document"]
    payload["run"] = package["run"]
    payload["card"] = package["card"]
    payload["evidence_ledger"] = package["evidence_ledger"]
    payload["coverage"] = {
        "availability": "not_recorded",
        "source_contract_version": "2.0",
        "policy_version": None,
        "records": [],
    }
    return payload


# ------------------------------------------------------------------- 1.1 input


def test_valid_agent_1a_package_is_accepted_as_agent_1b_input() -> None:
    model = Agent1BInputPackageV11.model_validate(valid_agent_1b_input())
    assert model.agent_1a_contract_version == "2.1"
    assert model.coverage.availability.value == "recorded"
    assert len(model.coverage.records) == 27
    assert model.mutation_policy.card_mutation_allowed is False
    assert model.source_access.read_only is True


def test_supported_versions_are_declared() -> None:
    assert SUPPORTED_AGENT_1A_CONTRACT_VERSIONS == ("2.0", "2.1")
    assert COVERAGE_RECORDED_CONTRACT_VERSIONS == ("2.1",)


def test_unsupported_agent_1a_contract_version_is_rejected_with_diagnostic() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(agent_1b_input(agent_1a_contract_version="2.5"))
    message = str(excinfo.value)
    assert "unsupported Agent 1A contract version" in message
    assert "'2.1'" in message


def test_missing_immutable_source_reference_blocks_verification() -> None:
    payload = valid_agent_1b_input()
    del payload["source_access"]
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "source_access" in str(excinfo.value)


def test_source_reference_must_match_the_document() -> None:
    payload = valid_agent_1b_input()
    payload["source_access"]["source_sha256"] = "b" * 64
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "source_sha256" in str(excinfo.value)


def test_source_reference_must_be_the_original_document() -> None:
    payload = valid_agent_1b_input()
    parsed_uri = ORIGINAL_URI.replace(
        "original_document/art_original", "parsed_document/art_parsed"
    )
    payload["source_access"]["original_artifact_uri"] = parsed_uri
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "original_document" in str(excinfo.value)


def test_agent_1b_input_forbids_card_mutation_policy() -> None:
    payload = valid_agent_1b_input()
    payload["mutation_policy"]["card_mutation_allowed"] = True
    with pytest.raises(ValidationError):
        Agent1BInputPackageV11.model_validate(payload)


def test_input_validation_does_not_mutate_the_source_package() -> None:
    payload = valid_agent_1b_input()
    original = copy.deepcopy(payload)
    Agent1BInputPackageV11.model_validate(payload)
    assert payload == original


def test_received_card_is_immutable_in_memory() -> None:
    model = Agent1BInputPackageV11.model_validate(valid_agent_1b_input())
    with pytest.raises(ValidationError):
        model.card.bibliography.title.value = "changed"  # type: ignore[misc]


def test_missing_validation_artifact_is_rejected() -> None:
    payload = valid_agent_1b_input()
    payload["artifacts"] = [
        item for item in payload["artifacts"] if item["artifact_type"] != "validation_report"
    ]
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "validation_report" in str(excinfo.value)


# ---------------------------------------------------------- coverage in the input


def test_historical_2_0_package_is_reported_as_not_recorded() -> None:
    model = Agent1BInputPackageV11.model_validate(input_from_v20_package())
    assert model.coverage.availability.value == "not_recorded"
    assert model.coverage.records == []


def test_2_1_package_cannot_be_reported_without_coverage() -> None:
    payload = valid_agent_1b_input()
    payload["coverage"] = {
        "availability": "not_recorded",
        "source_contract_version": "2.1",
        "policy_version": None,
        "records": [],
    }
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "coverage" in str(excinfo.value).lower()


def test_historical_package_cannot_grow_coverage() -> None:
    payload = input_from_v20_package()
    payload["coverage"] = {
        "availability": "recorded",
        "source_contract_version": "2.0",
        "policy_version": "1.0",
        "records": valid_agent_1b_input()["coverage"]["records"],
    }
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "predates the coverage trail" in str(excinfo.value)


def test_not_recorded_coverage_must_not_carry_records() -> None:
    payload = input_from_v20_package()
    payload["coverage"]["records"] = valid_agent_1b_input()["coverage"]["records"][:1]
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "none may be invented" in str(excinfo.value)


def test_coverage_source_version_must_match_the_received_contract() -> None:
    """The coverage section must describe the package version that was actually received."""
    payload = input_from_v20_package()
    payload["agent_1a_contract_version"] = "2.1"
    payload["run"]["schema_version"] = "2.1"
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "source_contract_version" in str(excinfo.value)


def test_recorded_coverage_requires_a_policy_version() -> None:
    payload = valid_agent_1b_input()
    payload["coverage"]["policy_version"] = None
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV11.model_validate(payload)
    assert "policy_version" in str(excinfo.value)


# ---------------------------------------------------------------- frozen 1.0 input


def test_frozen_1_0_input_still_validates() -> None:
    model = Agent1BInputPackageV1.model_validate(valid_agent_1b_input_v10())
    assert model.contract_version == "1.0"
    assert model.agent_1a_contract_version == "2.0"


def test_frozen_1_0_input_cannot_carry_coverage() -> None:
    payload = valid_agent_1b_input_v10()
    payload["coverage"] = {"availability": "not_recorded", "source_contract_version": "2.0"}
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV1.model_validate(payload)
    assert "coverage" in str(excinfo.value)


def test_frozen_1_0_input_rejects_unknown_contract_versions() -> None:
    payload = valid_agent_1b_input_v10()
    payload["agent_1a_contract_version"] = "2.1"
    with pytest.raises(ValidationError) as excinfo:
        Agent1BInputPackageV1.model_validate(payload)
    assert "unsupported Agent 1A contract version" in str(excinfo.value)


# ------------------------------------------------------------------ 1.1 report


def report(**overrides: Any) -> dict[str, Any]:
    return valid_verification_report() | overrides


def finding(
    *,
    status: str,
    severity: str = "major",
    with_evidence: bool = True,
    finding_id: str = "find_0001",
    field_path: str = "bibliography.title",
    coverage_verdict: str | None = None,
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "field_path": field_path,
        "status": status,
        "severity": severity,
        "comment": "checked against the original",
        "agent_1a_evidence_ids": ["ev_000001"],
        "agent_1b_evidence": ([evidence_record(evidence_id="ev_1b_0001")] if with_evidence else []),
        "coverage_verdict": coverage_verdict,
    }


@pytest.mark.parametrize(
    ("status", "severity", "with_evidence", "review"),
    [
        ("verified", "info", True, "verified"),
        ("partially_supported", "minor", False, "verified_with_issues"),
        ("unsupported", "major", False, "verified_with_issues"),
        ("contradicted", "critical", False, "human_review_required"),
        ("omitted", "major", True, "verified_with_issues"),
        ("not_verifiable", "major", False, "verified_with_issues"),
        ("not_checked", "info", False, "verified_with_issues"),
    ],
)
def test_report_accepts_every_finding_status(
    status: str, severity: str, with_evidence: bool, review: str
) -> None:
    model = Agent1BVerificationReportV11.model_validate(
        report(
            scientific_review_status=review,
            findings=[finding(status=status, severity=severity, with_evidence=with_evidence)],
        )
    )
    assert model.findings[0].status.value == status


def test_all_seven_finding_statuses_are_defined() -> None:
    assert {item.value for item in FindingStatus} == {
        "verified",
        "partially_supported",
        "unsupported",
        "contradicted",
        "omitted",
        "not_verifiable",
        "not_checked",
    }


def test_omitted_requires_evidence_and_serious_severity() -> None:
    with pytest.raises(ValidationError) as excinfo:
        VerificationFinding.model_validate(finding(status="omitted", with_evidence=False))
    assert "own evidence" in str(excinfo.value)
    with pytest.raises(ValidationError):
        VerificationFinding.model_validate(
            finding(status="omitted", severity="minor", with_evidence=True)
        )


def test_disputed_complete_requires_evidence() -> None:
    with pytest.raises(ValidationError) as excinfo:
        VerificationFinding.model_validate(
            finding(
                status="partially_supported",
                severity="minor",
                with_evidence=False,
                coverage_verdict="disputed_complete",
            )
        )
    assert "disputed_complete" in str(excinfo.value)


def test_coverage_verdict_requires_a_declared_review() -> None:
    payload = report(
        scientific_review_status="verified_with_issues",
        findings=[
            finding(
                status="partially_supported",
                severity="minor",
                with_evidence=False,
                coverage_verdict="confirmed_partial",
            )
        ],
    )
    payload["coverage_reviewed"] = False
    with pytest.raises(ValidationError) as excinfo:
        Agent1BVerificationReportV11.model_validate(payload)
    assert "coverage_reviewed" in str(excinfo.value)


def test_verified_report_cannot_dispute_coverage() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Agent1BVerificationReportV11.model_validate(
            report(
                scientific_review_status="verified",
                findings=[
                    finding(
                        status="verified",
                        severity="info",
                        with_evidence=True,
                        coverage_verdict="disputed_complete",
                    )
                ],
            )
        )
    assert "disputed" in str(excinfo.value)


def test_verified_finding_requires_the_verifier_s_own_evidence() -> None:
    with pytest.raises(ValidationError) as excinfo:
        VerificationFinding.model_validate(finding(status="verified", with_evidence=False))
    assert "own evidence" in str(excinfo.value)


def test_serious_finding_requires_major_or_critical_severity() -> None:
    with pytest.raises(ValidationError) as excinfo:
        VerificationFinding.model_validate(finding(status="unsupported", severity="minor"))
    assert "major" in str(excinfo.value)


def test_not_checked_cannot_carry_evidence() -> None:
    with pytest.raises(ValidationError):
        VerificationFinding.model_validate(
            finding(status="not_checked", severity="info", with_evidence=True)
        )


def test_report_cannot_be_verified_with_a_bad_finding() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Agent1BVerificationReportV11.model_validate(
            report(
                scientific_review_status="verified",
                findings=[finding(status="contradicted", severity="critical", with_evidence=False)],
            )
        )
    assert "verified" in str(excinfo.value)


def test_verified_report_requires_at_least_one_finding() -> None:
    with pytest.raises(ValidationError):
        Agent1BVerificationReportV11.model_validate(report(findings=[]))


def test_verified_with_issues_requires_an_issue() -> None:
    with pytest.raises(ValidationError):
        Agent1BVerificationReportV11.model_validate(
            report(
                scientific_review_status="verified_with_issues",
                findings=[finding(status="verified", severity="info")],
            )
        )


def test_human_review_requires_a_critical_finding() -> None:
    with pytest.raises(ValidationError):
        Agent1BVerificationReportV11.model_validate(
            report(
                scientific_review_status="human_review_required",
                findings=[
                    finding(status="partially_supported", severity="minor", with_evidence=False)
                ],
            )
        )


def test_one_verdict_per_field() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Agent1BVerificationReportV11.model_validate(
            report(
                scientific_review_status="verified_with_issues",
                findings=[
                    finding(status="verified", severity="info", finding_id="find_0001"),
                    finding(
                        status="unsupported",
                        severity="major",
                        with_evidence=False,
                        finding_id="find_0002",
                    ),
                ],
            )
        )
    assert "exactly one verdict" in str(excinfo.value)


def test_findings_must_reference_existing_evidence() -> None:
    model = Agent1BVerificationReportV11.model_validate(valid_verification_report())
    validate_findings_against_ledger(model, ["ev_000001"])
    with pytest.raises(ValueError) as excinfo:
        validate_findings_against_ledger(model, ["ev_000002"])
    assert "absent from the reviewed package" in str(excinfo.value)


def test_review_status_vocabulary_includes_all_required_values() -> None:
    assert {item.value for item in ScientificReviewStatus} == {
        "pending",
        "in_review",
        "verified_with_issues",
        "verified",
        "human_review_required",
    }
    assert {item.value for item in FindingSeverity} == {"info", "minor", "major", "critical"}
    assert {item.value for item in CoverageVerdict} == {
        "confirmed_complete",
        "confirmed_partial",
        "disputed_complete",
        "coverage_not_recorded",
    }


def test_blocking_findings_and_worst_severity_helpers() -> None:
    model = Agent1BVerificationReportV11.model_validate(
        report(
            scientific_review_status="human_review_required",
            findings=[finding(status="contradicted", severity="critical", with_evidence=False)],
        )
    )
    assert model.has_blocking_findings
    assert model.worst_severity is FindingSeverity.CRITICAL


def test_report_requires_utc_timestamps() -> None:
    payload = valid_verification_report()
    payload["completed_at"] = "2026-11-11T16:10:00+03:00"
    with pytest.raises(ValidationError):
        Agent1BVerificationReportV11.model_validate(payload)


def test_report_keeps_scientific_review_status_exclusive() -> None:
    """The scientific verdict exists only in the verifier's report, never in 1A."""
    schema = Agent1BVerificationReportV11.model_json_schema()
    assert "scientific_review_status" in str(schema)


# ---------------------------------------------------------------- frozen 1.0 report


def test_frozen_1_0_report_still_validates() -> None:
    model = Agent1BVerificationReportV1.model_validate(valid_verification_report_v10())
    assert model.contract_version == "1.0"


def test_frozen_1_0_report_does_not_know_omitted() -> None:
    payload = valid_verification_report_v10()
    payload["findings"][0]["status"] = "omitted"
    with pytest.raises(ValidationError):
        Agent1BVerificationReportV1.model_validate(payload)


def test_frozen_1_0_report_cannot_carry_a_coverage_verdict() -> None:
    payload = valid_verification_report_v10()
    payload["findings"][0]["coverage_verdict"] = "confirmed_complete"
    with pytest.raises(ValidationError):
        Agent1BVerificationReportV1.model_validate(payload)


def test_source_sha_matches_between_input_and_document() -> None:
    model = Agent1BInputPackageV11.model_validate(valid_agent_1b_input())
    assert model.document.source_sha256 == SOURCE_SHA256
