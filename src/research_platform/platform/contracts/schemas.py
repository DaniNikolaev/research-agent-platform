"""JSON Schema export for the versioned inter-agent contracts.

Seven schemas are published:

======================  =============================================  ================
Contract                Path                                           Status
======================  =============================================  ================
Agent 1A package 2.0    ``contracts/agent_1a/v2/…``                    frozen history
Agent 1A package 2.1    ``contracts/agent_1a/v2.1/…``                  current
Evidence candidates 2.1 ``contracts/agent_1a/v2.1/evidence_candidates…`` current
Agent 1B input 1.0      ``contracts/agent_1b/v1/input_package…``        frozen history
Agent 1B input 1.1      ``contracts/agent_1b/v1.1/input_package…``      current
Agent 1B report 1.0     ``contracts/agent_1b/v1/verification_report…``  frozen history
Agent 1B report 1.1     ``contracts/agent_1b/v1.1/verification_report…`` current
======================  =============================================  ================

Frozen schemas are still generated from their frozen Pydantic snapshots, so the
byte-for-byte comparison in ``tests/contract/test_schema_export.py`` keeps proving that
the published history did not drift.  Their checksums are additionally pinned in
``*.sha256`` sidecars and verified by ``tests/contract/test_frozen_schemas.py``.
"""

from __future__ import annotations

import json
from typing import Any, Final

from research_platform.agents.agent_1a.contracts.package import (
    Agent1AExtractionPackageV2,
)
from research_platform.agents.agent_1a.contracts.package_v21 import (
    Agent1AExtractionPackageV21,
)
from research_platform.agents.agent_1b.contracts.input_package import (
    Agent1BInputPackageV1,
)
from research_platform.agents.agent_1b.contracts.input_package_v11 import (
    Agent1BInputPackageV11,
)
from research_platform.agents.agent_1b.contracts.verification_report import (
    Agent1BVerificationReportV1,
)
from research_platform.agents.agent_1b.contracts.verification_report_v11 import (
    Agent1BVerificationReportV11,
)
from research_platform.domain.documents.models import SourceDocumentManifest
from research_platform.domain.documents.parsed import ParsedDocument
from research_platform.domain.documents.quality import QualityReport
from research_platform.domain.evidence.models import EvidenceCandidateList

__all__ = [
    "AGENT_1A_CANDIDATES_SCHEMA_PATH",
    "AGENT_1A_PACKAGE_SCHEMA_PATH",
    "AGENT_1B_INPUT_SCHEMA_PATH",
    "AGENT_1B_REPORT_SCHEMA_PATH",
    "FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH",
    "FROZEN_AGENT_1B_INPUT_SCHEMA_PATH",
    "FROZEN_AGENT_1B_REPORT_SCHEMA_PATH",
    "PARSED_DOCUMENT_SCHEMA_PATH",
    "QUALITY_REPORT_SCHEMA_PATH",
    "SCHEMA_DIALECT",
    "SOURCE_MANIFEST_SCHEMA_PATH",
    "agent_1a_candidates_schema",
    "agent_1a_package_schema",
    "agent_1b_input_schema",
    "agent_1b_verification_report_schema",
    "frozen_schemas",
    "parsed_document_schema",
    "quality_report_schema",
    "serialize_schema",
    "source_manifest_schema",
]

SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
_SCHEMA_NAMESPACE: Final = "https://research-agent-platform.local/contracts"

FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH: Final = "contracts/agent_1a/v2/extraction_package.schema.json"
FROZEN_AGENT_1B_INPUT_SCHEMA_PATH: Final = "contracts/agent_1b/v1/input_package.schema.json"
FROZEN_AGENT_1B_REPORT_SCHEMA_PATH: Final = "contracts/agent_1b/v1/verification_report.schema.json"

AGENT_1A_PACKAGE_SCHEMA_PATH: Final = "contracts/agent_1a/v2.1/extraction_package.schema.json"
AGENT_1A_CANDIDATES_SCHEMA_PATH: Final = "contracts/agent_1a/v2.1/evidence_candidates.schema.json"
AGENT_1B_INPUT_SCHEMA_PATH: Final = "contracts/agent_1b/v1.1/input_package.schema.json"
AGENT_1B_REPORT_SCHEMA_PATH: Final = "contracts/agent_1b/v1.1/verification_report.schema.json"

SOURCE_MANIFEST_SCHEMA_PATH: Final = "contracts/documents/v1/source_manifest.schema.json"
PARSED_DOCUMENT_SCHEMA_PATH: Final = "contracts/documents/v1/parsed_document.schema.json"
QUALITY_REPORT_SCHEMA_PATH: Final = "contracts/documents/v1/quality_report.schema.json"


def _with_header(
    schema: dict[str, Any], *, schema_id: str, title: str, description: str
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "$schema": SCHEMA_DIALECT,
        "$id": f"{_SCHEMA_NAMESPACE}/{schema_id}",
        "title": title,
        "description": description,
    }
    document.update({key: value for key, value in schema.items() if key not in document})
    return document


def agent_1a_package_schema() -> dict[str, Any]:
    """JSON Schema of ``Agent1AExtractionPackageV21`` (current contract 2.1)."""
    return _with_header(
        Agent1AExtractionPackageV21.model_json_schema(mode="validation"),
        schema_id="agent_1a/v2.1/extraction_package.schema.json",
        title="Agent1AExtractionPackageV21",
        description=(
            "Immutable extraction package produced by Agent 1A, contract 2.1. Adds the "
            "Coverage Trail, hybrid retrieval mode and retrieval strategy to the 2.0 shape."
        ),
    )


def agent_1a_candidates_schema() -> dict[str, Any]:
    """JSON Schema of ``EvidenceCandidateList`` — the extraction call's output contract."""
    return _with_header(
        EvidenceCandidateList.model_json_schema(mode="validation"),
        schema_id="agent_1a/v2.1/evidence_candidates.schema.json",
        title="EvidenceCandidateList",
        description=(
            "Structured output of an extraction call: quotes plus untrusted locator hints. "
            "Contains no coordinates, no hashes and no resolution status."
        ),
    )


def agent_1b_input_schema() -> dict[str, Any]:
    """JSON Schema of ``Agent1BInputPackageV11`` (current contract 1.1)."""
    return _with_header(
        Agent1BInputPackageV11.model_json_schema(mode="validation"),
        schema_id="agent_1b/v1.1/input_package.schema.json",
        title="Agent1BInputPackageV11",
        description=(
            "Input of the Agent 1B verification workflow, contract 1.1: the immutable Agent "
            "1A package content, read-only access to the original, and the received Coverage "
            "Trail with explicit availability."
        ),
    )


def agent_1b_verification_report_schema() -> dict[str, Any]:
    """JSON Schema of ``Agent1BVerificationReportV11`` (current contract 1.1)."""
    return _with_header(
        Agent1BVerificationReportV11.model_json_schema(mode="validation"),
        schema_id="agent_1b/v1.1/verification_report.schema.json",
        title="Agent1BVerificationReportV11",
        description=(
            "Independent verification report produced by Agent 1B, contract 1.1. Adds the "
            "'omitted' finding status and coverage verdicts; never rewrites the Agent 1A card."
        ),
    )


def _frozen_schemas() -> dict[str, dict[str, Any]]:
    return {
        FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH: _with_header(
            Agent1AExtractionPackageV2.model_json_schema(mode="validation"),
            schema_id="agent_1a/v2/extraction_package.schema.json",
            title="Agent1AExtractionPackageV2",
            description=(
                "Immutable extraction package produced by Agent 1A and consumed by Agent 1B. "
                "Contract version 2.0; inherits the 27 legacy content fields of card v1.0."
            ),
        ),
        FROZEN_AGENT_1B_INPUT_SCHEMA_PATH: _with_header(
            Agent1BInputPackageV1.model_json_schema(mode="validation"),
            schema_id="agent_1b/v1/input_package.schema.json",
            title="Agent1BInputPackageV1",
            description=(
                "Input contract of the Agent 1B verification workflow: the immutable Agent 1A "
                "package content plus read-only access to the original document."
            ),
        ),
        FROZEN_AGENT_1B_REPORT_SCHEMA_PATH: _with_header(
            Agent1BVerificationReportV1.model_json_schema(mode="validation"),
            schema_id="agent_1b/v1/verification_report.schema.json",
            title="Agent1BVerificationReportV1",
            description=(
                "Independent verification report produced by Agent 1B. It never rewrites the "
                "Agent 1A card; corrections require a new package version."
            ),
        ),
    }


def source_manifest_schema() -> dict[str, Any]:
    """JSON Schema of ``SourceDocumentManifest`` (ingestion contract 1.0)."""
    return _with_header(
        SourceDocumentManifest.model_json_schema(mode="validation"),
        schema_id="documents/v1/source_manifest.schema.json",
        title="SourceDocumentManifest",
        description=(
            "Ingestion manifest of one received document version, contract 1.0: identity, "
            "original bytes, detected encoding, language and processing status."
        ),
    )


def parsed_document_schema() -> dict[str, Any]:
    """JSON Schema of ``ParsedDocument`` (parsing contract 1.0)."""
    return _with_header(
        ParsedDocument.model_json_schema(mode="validation"),
        schema_id="documents/v1/parsed_document.schema.json",
        title="ParsedDocument",
        description=(
            "Parser-independent representation of a parsed source, contract 1.0: physical "
            "pages, sections, elements, tables, figures and character-level provenance."
        ),
    )


def quality_report_schema() -> dict[str, Any]:
    """JSON Schema of ``QualityReport`` (quality gate contract 1.0)."""
    return _with_header(
        QualityReport.model_json_schema(mode="validation"),
        schema_id="documents/v1/quality_report.schema.json",
        title="QualityReport",
        description=(
            "Result of the deterministic Quality Gate, contract 1.0: named checks with "
            "observed values and thresholds, per-page verdicts and the overall status."
        ),
    )


def _current_schemas() -> dict[str, dict[str, Any]]:
    return {
        AGENT_1A_PACKAGE_SCHEMA_PATH: agent_1a_package_schema(),
        AGENT_1A_CANDIDATES_SCHEMA_PATH: agent_1a_candidates_schema(),
        AGENT_1B_INPUT_SCHEMA_PATH: agent_1b_input_schema(),
        AGENT_1B_REPORT_SCHEMA_PATH: agent_1b_verification_report_schema(),
        SOURCE_MANIFEST_SCHEMA_PATH: source_manifest_schema(),
        PARSED_DOCUMENT_SCHEMA_PATH: parsed_document_schema(),
        QUALITY_REPORT_SCHEMA_PATH: quality_report_schema(),
    }


def exportable_schemas() -> dict[str, dict[str, Any]]:
    """Every published schema keyed by its repository-relative path."""
    return {**_frozen_schemas(), **_current_schemas()}


def frozen_schemas() -> dict[str, dict[str, Any]]:
    """Only the frozen historical schemas."""
    return _frozen_schemas()


def serialize_schema(schema: dict[str, Any]) -> str:
    """Deterministic UTF-8 JSON text for a schema file (stable key order, LF)."""
    return json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
