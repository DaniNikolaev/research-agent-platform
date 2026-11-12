"""``Agent1AExtractionPackageV2`` — the immutable Agent 1A → Agent 1B handoff contract.

Root layout (``contract_version`` is the package contract version, not the card
version)::

    {
        "contract_version": "2.0",
        "package_id": "...",
        "document": {...},
        "run": {...},
        "card": {...},
        "evidence_ledger": [...],
        "validation": {...},
        "artifacts": [...],
    }

The package is *self-describing and self-checking*: it can be validated offline, from
the JSON alone, without a database, an LLM or a file system.  The checks below are the
contract, not documentation conventions:

1. ``evidence_id`` values are unique inside the package;
2. every evidence record belongs to ``document.document_id`` **and** to its exact
   ``source_sha256`` — evidence from another version of the file cannot be attached;
3. every card field reference resolves to an existing ledger record;
4. denormalized ``quote``/``page``/``section_path`` copies match the ledger exactly;
5. pagination is honoured: a paginated source requires ``page`` on every record, an
   unpaginated source forbids it;
6. the minimum handoff artefact set is present, belongs to this document and run, and
   includes ``document.original_artifact_uri``;
7. ``run.schema_version`` equals ``contract_version``.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from research_platform.domain.base import ContractModel, Identifier
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
    parse_artifact_uri,
)
from research_platform.domain.runs.v2_0 import RunMetadata
from research_platform.domain.validation.models import ValidationReport

__all__ = [
    "REQUIRED_HANDOFF_ARTIFACT_TYPES",
    "SUPPORTED_CONTRACT_VERSIONS",
    "Agent1AExtractionPackageV2",
]

SUPPORTED_CONTRACT_VERSIONS: Final[tuple[str, ...]] = ("2.0",)

REQUIRED_HANDOFF_ARTIFACT_TYPES: Final[frozenset[ArtifactType]] = frozenset(
    {
        ArtifactType.ORIGINAL_DOCUMENT,
        ArtifactType.PARSED_DOCUMENT,
        ArtifactType.EVIDENCE_LEDGER,
        ArtifactType.VALIDATION_REPORT,
    }
)


class Agent1AExtractionPackageV2(ContractModel):
    """Versioned, immutable extraction package produced by Agent 1A."""

    contract_version: Literal["2.0"] = "2.0"
    package_id: Identifier
    document: DocumentManifest
    run: RunMetadata
    card: Agent1ACardV2
    evidence_ledger: Annotated[list[EvidenceRecord], Field(max_length=5_000)]
    validation: ValidationReport
    artifacts: Annotated[list[ArtifactReference], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> Agent1AExtractionPackageV2:
        validate_evidence_ids_are_unique(self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _run_is_the_declared_contract(self) -> Agent1AExtractionPackageV2:
        if self.run.schema_version != self.contract_version:
            raise ValueError(
                f"run.schema_version {self.run.schema_version!r} must equal "
                f"contract_version {self.contract_version!r}"
            )
        return self

    @model_validator(mode="after")
    def _ledger_belongs_to_this_document_version(self) -> Agent1AExtractionPackageV2:
        validate_ledger_belongs_to_document(self.document, self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _pagination_is_consistent(self) -> Agent1AExtractionPackageV2:
        validate_pagination_consistency(self.document, self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _card_references_resolve(self) -> Agent1AExtractionPackageV2:
        validate_card_references(self.card, self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _artifacts_are_complete_and_local(self) -> Agent1AExtractionPackageV2:
        counts = Counter(artifact.artifact_id for artifact in self.artifacts)
        duplicates = sorted(artifact_id for artifact_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError(
                "duplicate artifact_id values in the artifacts section: " + ", ".join(duplicates)
            )

        present_types = {artifact.artifact_type for artifact in self.artifacts}
        missing = REQUIRED_HANDOFF_ARTIFACT_TYPES - present_types
        if missing:
            raise ValueError(
                "handoff package is incomplete; missing artifact types: "
                + ", ".join(sorted(item.value for item in missing))
            )

        uris = {artifact.uri for artifact in self.artifacts}
        if self.document.original_artifact_uri not in uris:
            raise ValueError(
                "document.original_artifact_uri must appear in the artifacts section; an "
                "independent reviewer needs a reference to the immutable original"
            )

        for artifact in self.artifacts:
            parts = parse_artifact_uri(artifact.uri)
            if parts.document_id != self.document.document_id:
                raise ValueError(
                    f"artifact {artifact.artifact_id!r} belongs to document "
                    f"{parts.document_id!r}, not to {self.document.document_id!r}"
                )
            if parts.run_id != self.run.run_id:
                raise ValueError(
                    f"artifact {artifact.artifact_id!r} belongs to run "
                    f"{parts.run_id!r}, not to {self.run.run_id!r}"
                )
        return self
