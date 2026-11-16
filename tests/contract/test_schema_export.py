"""Published JSON Schemas must be generated from the Pydantic models — including history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from tests.fixtures.builders import (
    evidence_candidate_list,
    valid_agent_1b_input,
    valid_agent_1b_input_v10,
    valid_package,
    valid_package_v20,
    valid_verification_report,
    valid_verification_report_v10,
)

from research_platform.platform.contracts.schemas import (
    AGENT_1A_CANDIDATES_SCHEMA_PATH,
    AGENT_1A_PACKAGE_SCHEMA_PATH,
    AGENT_1B_INPUT_SCHEMA_PATH,
    AGENT_1B_REPORT_SCHEMA_PATH,
    FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH,
    FROZEN_AGENT_1B_INPUT_SCHEMA_PATH,
    FROZEN_AGENT_1B_REPORT_SCHEMA_PATH,
    PARSED_DOCUMENT_SCHEMA_PATH,
    QUALITY_REPORT_SCHEMA_PATH,
    SCHEMA_DIALECT,
    SOURCE_MANIFEST_SCHEMA_PATH,
    exportable_schemas,
    serialize_schema,
)

SCHEMA_PATHS = (
    FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH,
    FROZEN_AGENT_1B_INPUT_SCHEMA_PATH,
    FROZEN_AGENT_1B_REPORT_SCHEMA_PATH,
    AGENT_1A_PACKAGE_SCHEMA_PATH,
    AGENT_1A_CANDIDATES_SCHEMA_PATH,
    AGENT_1B_INPUT_SCHEMA_PATH,
    AGENT_1B_REPORT_SCHEMA_PATH,
    SOURCE_MANIFEST_SCHEMA_PATH,
    PARSED_DOCUMENT_SCHEMA_PATH,
    QUALITY_REPORT_SCHEMA_PATH,
)

FIXTURES_BY_SCHEMA = {
    FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH: valid_package_v20,
    FROZEN_AGENT_1B_INPUT_SCHEMA_PATH: valid_agent_1b_input_v10,
    FROZEN_AGENT_1B_REPORT_SCHEMA_PATH: valid_verification_report_v10,
    AGENT_1A_PACKAGE_SCHEMA_PATH: valid_package,
    AGENT_1A_CANDIDATES_SCHEMA_PATH: evidence_candidate_list,
    AGENT_1B_INPUT_SCHEMA_PATH: valid_agent_1b_input,
    AGENT_1B_REPORT_SCHEMA_PATH: valid_verification_report,
}


def load(repo_root: Path, relative_path: str) -> dict[str, Any]:
    return json.loads((repo_root / relative_path).read_text(encoding="utf-8"))


@pytest.mark.parametrize("relative_path", SCHEMA_PATHS)
def test_committed_schema_matches_generated_schema(repo_root: Path, relative_path: str) -> None:
    committed = (repo_root / relative_path).read_text(encoding="utf-8")
    generated = serialize_schema(exportable_schemas()[relative_path])
    assert committed == generated, (
        f"{relative_path} is out of date; run: uv run python tools/export_schemas.py"
    )


@pytest.mark.parametrize("relative_path", SCHEMA_PATHS)
def test_schema_declares_draft_2020_12(repo_root: Path, relative_path: str) -> None:
    schema = load(repo_root, relative_path)
    assert schema["$schema"] == SCHEMA_DIALECT
    assert schema["$id"].startswith("https://research-agent-platform.local/contracts/")


@pytest.mark.parametrize("relative_path", SCHEMA_PATHS)
def test_schema_has_no_external_references(repo_root: Path, relative_path: str) -> None:
    schema = load(repo_root, relative_path)

    def walk(node: Any) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    if not value.startswith("#"):
                        found.append(value)
                else:
                    found.extend(walk(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(walk(item))
        return found

    assert walk(schema) == []


@pytest.mark.parametrize(("relative_path", "builder"), sorted(FIXTURES_BY_SCHEMA.items()))
def test_schema_accepts_its_fixture(repo_root: Path, relative_path: str, builder: Any) -> None:
    validator = Draft202012Validator(load(repo_root, relative_path))
    validator.validate(builder())


@pytest.mark.parametrize(
    "relative_path",
    [
        AGENT_1A_PACKAGE_SCHEMA_PATH,
        FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH,
        AGENT_1B_INPUT_SCHEMA_PATH,
        AGENT_1B_REPORT_SCHEMA_PATH,
    ],
)
def test_schema_rejects_an_unknown_root_field(repo_root: Path, relative_path: str) -> None:
    validator = Draft202012Validator(load(repo_root, relative_path))
    payload = FIXTURES_BY_SCHEMA[relative_path]()
    payload["unexpected"] = True
    assert list(validator.iter_errors(payload))


def test_agent_1a_schema_rejects_a_broken_evidence_object(repo_root: Path) -> None:
    """Schema-level guards work without the Pydantic layer (a foreign consumer)."""
    validator = Draft202012Validator(load(repo_root, AGENT_1A_PACKAGE_SCHEMA_PATH))
    payload = valid_package()
    payload["evidence_ledger"][0]["page"] = 0
    assert list(validator.iter_errors(payload))


def test_agent_1a_schema_requires_the_coverage_trail(repo_root: Path) -> None:
    validator = Draft202012Validator(load(repo_root, AGENT_1A_PACKAGE_SCHEMA_PATH))
    payload = valid_package()
    del payload["coverage_trail"]
    assert list(validator.iter_errors(payload))


def test_candidate_schema_forbids_coordinates(repo_root: Path) -> None:
    validator = Draft202012Validator(load(repo_root, AGENT_1A_CANDIDATES_SCHEMA_PATH))
    payload = evidence_candidate_list()
    payload["candidates"][0]["char_start"] = 10
    assert list(validator.iter_errors(payload))


def test_report_schema_rejects_unknown_finding_status(repo_root: Path) -> None:
    validator = Draft202012Validator(load(repo_root, AGENT_1B_REPORT_SCHEMA_PATH))
    payload = valid_verification_report()
    payload["findings"][0]["status"] = "probably_true"
    assert list(validator.iter_errors(payload))


def test_exportable_schemas_covers_all_published_contracts() -> None:
    assert set(exportable_schemas()) == set(SCHEMA_PATHS)


def test_schema_titles_match_model_names(repo_root: Path) -> None:
    expectations = {
        FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH: "Agent1AExtractionPackageV2",
        FROZEN_AGENT_1B_INPUT_SCHEMA_PATH: "Agent1BInputPackageV1",
        FROZEN_AGENT_1B_REPORT_SCHEMA_PATH: "Agent1BVerificationReportV1",
        AGENT_1A_PACKAGE_SCHEMA_PATH: "Agent1AExtractionPackageV21",
        AGENT_1A_CANDIDATES_SCHEMA_PATH: "EvidenceCandidateList",
        AGENT_1B_INPUT_SCHEMA_PATH: "Agent1BInputPackageV11",
        AGENT_1B_REPORT_SCHEMA_PATH: "Agent1BVerificationReportV11",
    }
    for relative_path, title in expectations.items():
        schema = load(repo_root, relative_path)
        assert schema["title"] == title
