"""``Agent1BInputPackageV1`` — what an independent verifier receives.

Agent 1B must be able to check the card **without trusting Agent 1A**.  The input
package therefore carries:

* the immutable Agent 1A package content (document manifest, run provenance, card,
  evidence ledger, technical validation report);
* an explicit read-only reference to the original document
  (``source_access``), because without it an independent check is impossible;
* a machine-readable mutation policy stating that the received card is read-only.

The two agent packages never import each other; both compose the shared ``domain``
contracts.  That is why this model re-declares its own composition instead of
importing ``Agent1AExtractionPackageV2``.

Version negotiation is explicit: ``agent_1a_contract_version`` must be one of
:data:`SUPPORTED_AGENT_1A_CONTRACT_VERSIONS`; anything else is rejected with a
diagnostic naming the supported versions.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from research_platform.domain.base import (
    ContractModel,
    Identifier,
    Sha256Hex,
    ShortStr,
)
from research_platform.domain.cards.models import Agent1ACardV2
from research_platform.domain.documents.models import DocumentManifest
from research_platform.domain.evidence.ledger import (
    validate_card_references,
    validate_evidence_ids_are_unique,
    validate_ledger_belongs_to_document,
    validate_pagination_consistency,
)
from research_platform.domain.evidence.v2_0 import EvidenceRecord
from research_platform.domain.runs.artifacts import (
    ArtifactReference,
    ArtifactType,
    ArtifactUri,
    parse_artifact_uri,
)
from research_platform.domain.runs.v2_0 import RunMetadata
from research_platform.domain.validation.models import ValidationReport

__all__ = [
    "SUPPORTED_AGENT_1A_CONTRACT_VERSIONS",
    "Agent1BInputPackageV1",
    "ImmutableSourceAccess",
    "MutationPolicy",
]

SUPPORTED_AGENT_1A_CONTRACT_VERSIONS: Final[tuple[str, ...]] = ("2.0",)
"""Agent 1A package contract versions this Agent 1B input contract can consume."""


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


class Agent1BInputPackageV1(ContractModel):
    """Input contract of the Agent 1B verification workflow."""

    contract_version: Literal["1.0"] = "1.0"
    package_id: Identifier
    agent_1a_contract_version: ShortStr
    agent_1a_package_id: Identifier
    document: DocumentManifest
    source_access: ImmutableSourceAccess
    run: RunMetadata
    card: Agent1ACardV2
    evidence_ledger: Annotated[list[EvidenceRecord], Field(max_length=5_000)]
    validation: ValidationReport
    artifacts: Annotated[list[ArtifactReference], Field(min_length=1, max_length=500)]
    mutation_policy: MutationPolicy = Field(default_factory=MutationPolicy)

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
    def _source_access_matches_the_document(self) -> Agent1BInputPackageV1:
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
    def _run_is_the_declared_agent_1a_version(self) -> Agent1BInputPackageV1:
        if self.run.schema_version != self.agent_1a_contract_version:
            raise ValueError(
                f"run.schema_version {self.run.schema_version!r} must equal "
                f"agent_1a_contract_version {self.agent_1a_contract_version!r}"
            )
        return self

    @model_validator(mode="after")
    def _evidence_is_consistent(self) -> Agent1BInputPackageV1:
        validate_evidence_ids_are_unique(self.evidence_ledger)
        validate_ledger_belongs_to_document(self.document, self.evidence_ledger)
        validate_pagination_consistency(self.document, self.evidence_ledger)
        validate_card_references(self.card, self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _artifacts_are_consistent(self) -> Agent1BInputPackageV1:
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
