"""``Agent1BInputPackageV11`` — input contract 1.1 for the independent verifier.

What changed relative to 1.0:

* the input can carry a **Coverage Trail** received from Agent 1A;
* the received package contract may be ``2.0`` (no coverage trail exists) or ``2.1``;
* coverage availability is an explicit, validated statement — a historical 2.0 package is
  reported as ``not_recorded`` and never receives invented coverage.

Why this matters for the verifier: without the coverage trail it cannot distinguish
"the article really does not contain this" from "the search was incomplete or failed".
With it, it can — and it can also see that a field *was* found but never made it into the
card.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from research_platform.domain.base import ContractModel, Identifier, Sha256Hex, ShortStr
from research_platform.domain.cards.models import Agent1ACardV2
from research_platform.domain.coverage.models import (
    CoverageAvailability,
    FieldCoverageRecord,
)
from research_platform.domain.coverage.rules import validate_coverage_is_not_claimed
from research_platform.domain.documents.models import DocumentManifest
from research_platform.domain.evidence.ledger import (
    validate_card_references,
    validate_evidence_ids_are_unique,
    validate_ledger_belongs_to_document,
    validate_pagination_consistency,
)
from research_platform.domain.evidence.models import EvidenceRecord
from research_platform.domain.evidence.v2_0 import EvidenceRecord as EvidenceRecordV20
from research_platform.domain.runs.artifacts import (
    ArtifactReference,
    ArtifactType,
    ArtifactUri,
    parse_artifact_uri,
)
from research_platform.domain.runs.models import RunMetadata
from research_platform.domain.runs.v2_0 import RunMetadata as RunMetadataV20
from research_platform.domain.validation.models import ValidationReport

__all__ = [
    "COVERAGE_RECORDED_CONTRACT_VERSIONS",
    "SUPPORTED_AGENT_1A_CONTRACT_VERSIONS",
    "Agent1BInputPackageV11",
    "CoverageInput",
    "ImmutableSourceAccess",
    "MutationPolicy",
]

SUPPORTED_AGENT_1A_CONTRACT_VERSIONS: Final[tuple[str, ...]] = ("2.0", "2.1")
"""Agent 1A package contract versions this input contract can consume."""

COVERAGE_RECORDED_CONTRACT_VERSIONS: Final[tuple[str, ...]] = ("2.1",)
"""Versions that always carry a coverage trail."""


class ImmutableSourceAccess(ContractModel):
    """Read-only access to the exact original bytes the card was built from."""

    original_artifact_uri: ArtifactUri
    source_sha256: Sha256Hex
    read_only: Literal[True] = True

    @model_validator(mode="after")
    def _reference_is_the_original_document(self) -> ImmutableSourceAccess:
        parts = parse_artifact_uri(self.original_artifact_uri)
        if parts.artifact_type is not ArtifactType.ORIGINAL_DOCUMENT:
            raise ValueError(
                "source_access.original_artifact_uri must reference an artifact of type "
                f"'original_document', got {parts.artifact_type.value!r}"
            )
        return self


class MutationPolicy(ContractModel):
    """Structural statement that Agent 1B does not edit the received card."""

    card_mutation_allowed: Literal[False] = False
    corrections_require_new_package: Literal[True] = True


class CoverageInput(ContractModel):
    """Coverage trail as received from Agent 1A, with explicit availability."""

    availability: CoverageAvailability
    source_contract_version: ShortStr
    policy_version: Annotated[str | None, Field(default=None, pattern=r"^\d+\.\d+$")] = None
    records: Annotated[list[FieldCoverageRecord], Field(default_factory=list, max_length=64)] = (
        Field(default_factory=list)
    )

    @model_validator(mode="after")
    def _availability_matches_payload(self) -> CoverageInput:
        validate_coverage_is_not_claimed(
            availability=self.availability,
            records=self.records,
            source_contract_version=self.source_contract_version,
            recorded_contract_versions=COVERAGE_RECORDED_CONTRACT_VERSIONS,
        )
        if self.availability is CoverageAvailability.RECORDED and self.policy_version is None:
            raise ValueError(
                "a recorded coverage trail requires policy_version: the rules under which "
                "coverage was claimed must be reproducible"
            )
        if (
            self.availability is CoverageAvailability.NOT_RECORDED
            and self.policy_version is not None
        ):
            raise ValueError(
                "coverage availability 'not_recorded' must not declare a policy version"
            )
        return self


class Agent1BInputPackageV11(ContractModel):
    """Input contract 1.1 of the Agent 1B verification workflow."""

    contract_version: Literal["1.1"] = "1.1"
    package_id: Identifier
    agent_1a_contract_version: ShortStr
    agent_1a_package_id: Identifier
    document: DocumentManifest
    source_access: ImmutableSourceAccess
    run: RunMetadata | RunMetadataV20
    card: Agent1ACardV2
    evidence_ledger: Annotated[list[EvidenceRecord | EvidenceRecordV20], Field(max_length=5_000)]
    coverage: CoverageInput
    validation: ValidationReport
    artifacts: Annotated[list[ArtifactReference], Field(min_length=1, max_length=500)]
    mutation_policy: MutationPolicy = Field(default_factory=MutationPolicy)

    @model_validator(mode="before")
    @classmethod
    def _coerce_versioned_sections(cls, data: object) -> object:
        """Pick the versioned shape *before* validation, from the declared version.

        The 2.0 run metadata is a structural subset of 2.1 (it simply has no
        ``retrieval_strategy``), so a plain union would let a 2.0 payload validate against
        the 2.1 model and the provenance would silently look newer than it is.  The
        version declared by the received package decides the shape explicitly.
        """
        if not isinstance(data, dict):
            return data
        coverage = data.get("coverage")
        if not isinstance(coverage, dict):
            return data
        declared = coverage.get("source_contract_version")
        if not isinstance(declared, str):
            return data

        if declared == "2.0":
            run_model: type[ContractModel] = RunMetadataV20
            record_model: type[ContractModel] = EvidenceRecordV20
        else:
            run_model = RunMetadata
            record_model = EvidenceRecord

        coerced = dict(data)
        run = coerced.get("run")
        if isinstance(run, dict):
            coerced["run"] = run_model.model_validate(run)
        ledger = coerced.get("evidence_ledger")
        if isinstance(ledger, list):
            coerced["evidence_ledger"] = [
                record_model.model_validate(item) if isinstance(item, dict) else item
                for item in ledger
            ]
        return coerced

    @model_validator(mode="before")
    @classmethod
    def _reject_unsupported_agent_1a_contract(cls, data: object) -> object:
        if isinstance(data, dict):
            declared = data.get("agent_1a_contract_version")
            if isinstance(declared, str) and declared not in SUPPORTED_AGENT_1A_CONTRACT_VERSIONS:
                supported = ", ".join(repr(item) for item in SUPPORTED_AGENT_1A_CONTRACT_VERSIONS)
                raise ValueError(
                    f"unsupported Agent 1A contract version {declared!r}; this Agent 1B "
                    f"input contract accepts only: {supported}"
                )
        return data

    @model_validator(mode="after")
    def _source_access_matches_the_document(self) -> Agent1BInputPackageV11:
        if self.source_access.original_artifact_uri != self.document.original_artifact_uri:
            raise ValueError(
                "source_access.original_artifact_uri must equal "
                "document.original_artifact_uri: the verifier must read the same immutable "
                "original the extractor used"
            )
        if self.source_access.source_sha256 != self.document.source_sha256:
            raise ValueError("source_access.source_sha256 must equal document.source_sha256")
        return self

    @model_validator(mode="after")
    def _run_is_the_declared_agent_1a_version(self) -> Agent1BInputPackageV11:
        if self.run.schema_version != self.agent_1a_contract_version:
            raise ValueError(
                f"run.schema_version {self.run.schema_version!r} must equal "
                f"agent_1a_contract_version {self.agent_1a_contract_version!r}"
            )
        return self

    @model_validator(mode="after")
    def _coverage_matches_the_received_version(self) -> Agent1BInputPackageV11:
        if self.coverage.source_contract_version != self.agent_1a_contract_version:
            raise ValueError(
                f"coverage.source_contract_version "
                f"{self.coverage.source_contract_version!r} must equal "
                f"agent_1a_contract_version {self.agent_1a_contract_version!r}"
            )
        return self

    @model_validator(mode="after")
    def _ledger_shape_matches_the_source_version(self) -> Agent1BInputPackageV11:
        """A historical package keeps its historical evidence shape.

        The 2.0 record carries no parser/resolver provenance, so it cannot be silently
        upgraded to the 2.1 shape — and a 2.1 record must not be presented as if it were a
        2.0 artefact.
        """
        if self.coverage.source_contract_version == "2.0":
            if not isinstance(self.run, RunMetadataV20):
                raise ValueError(
                    "the received package is contract 2.0, so its run metadata must use the "
                    "2.0 shape (no retrieval_strategy, no hybrid mode)"
                )
            wrong = [
                record.evidence_id
                for record in self.evidence_ledger
                if not isinstance(record, EvidenceRecordV20)
            ]
            if wrong:
                raise ValueError(
                    "the received package is contract 2.0, so its evidence records must use "
                    "the 2.0 shape; unexpected records: " + ", ".join(wrong)
                )
        else:
            if not isinstance(self.run, RunMetadata):
                raise ValueError(
                    f"the received package is contract "
                    f"{self.coverage.source_contract_version}, so its run metadata must use "
                    "the current shape"
                )
            wrong = [
                record.evidence_id
                for record in self.evidence_ledger
                if not isinstance(record, EvidenceRecord)
            ]
            if wrong:
                raise ValueError(
                    f"the received package is contract "
                    f"{self.coverage.source_contract_version}, so its evidence records must "
                    "use the 2.1 shape; unexpected records: " + ", ".join(wrong)
                )
        return self

    @model_validator(mode="after")
    def _evidence_is_consistent(self) -> Agent1BInputPackageV11:
        validate_evidence_ids_are_unique(self.evidence_ledger)
        validate_ledger_belongs_to_document(self.document, self.evidence_ledger)
        validate_pagination_consistency(self.document, self.evidence_ledger)
        validate_card_references(self.card, self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _artifacts_are_consistent(self) -> Agent1BInputPackageV11:
        counts = Counter(artifact.artifact_id for artifact in self.artifacts)
        duplicates = sorted(artifact_id for artifact_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(
                "duplicate artifact_id values in the artifacts section: " + ", ".join(duplicates)
            )
        present_types = {artifact.artifact_type for artifact in self.artifacts}
        for required in (
            ArtifactType.ORIGINAL_DOCUMENT,
            ArtifactType.EVIDENCE_LEDGER,
            ArtifactType.VALIDATION_REPORT,
        ):
            if required not in present_types:
                raise ValueError(
                    f"Agent 1B input is incomplete; missing artifact type: {required.value!r}"
                )
        uris = {artifact.uri for artifact in self.artifacts}
        if self.source_access.original_artifact_uri not in uris:
            raise ValueError(
                "the immutable original referenced by source_access must be listed in artifacts"
            )
        for artifact in self.artifacts:
            parts = parse_artifact_uri(artifact.uri)
            if parts.document_id != self.document.document_id:
                raise ValueError(
                    f"artifact {artifact.artifact_id!r} belongs to document "
                    f"{parts.document_id!r}, not to {self.document.document_id!r}"
                )
        return self
