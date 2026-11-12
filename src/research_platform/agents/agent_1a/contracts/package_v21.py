"""``Agent1AExtractionPackageV21`` — contract **2.1** handoff (adds the Coverage Trail).

Differences from the frozen 2.0 package:

1. ``run.retrieval_mode`` may be ``hybrid`` and ``run`` carries ``retrieval_strategy``;
2. a new required section ``coverage_trail`` documents, for every one of the twenty-seven
   card fields, which search actually ran;
3. ``not_found`` is legal only when that search is ``complete``, ``not_found_eligible``
   and — for ``rag``/``hybrid`` — the mandatory full-text fallback really executed;
4. evidence records carry the 2.1 shape (``match_status`` + ``match_type`` + parser and
   resolver provenance).

Everything else (document manifest, card, validation, artefacts) is unchanged, and the
2.0 package remains valid under its own contract.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from research_platform.domain.base import ContractModel, Identifier
from research_platform.domain.cards.models import Agent1ACardV2
from research_platform.domain.coverage.models import FieldCoverageRecord
from research_platform.domain.coverage.rules import validate_coverage_trail
from research_platform.domain.documents.models import DocumentManifest
from research_platform.domain.evidence.ledger import (
    validate_card_references,
    validate_evidence_ids_are_unique,
    validate_ledger_belongs_to_document,
    validate_pagination_consistency,
)
from research_platform.domain.evidence.models import EvidenceRecord
from research_platform.domain.runs.artifacts import (
    ArtifactReference,
    ArtifactType,
    parse_artifact_uri,
)
from research_platform.domain.runs.models import RunMetadata
from research_platform.domain.validation.models import ValidationReport

__all__ = [
    "REQUIRED_HANDOFF_ARTIFACT_TYPES",
    "SUPPORTED_CONTRACT_VERSIONS",
    "Agent1AExtractionPackageV21",
]

SUPPORTED_CONTRACT_VERSIONS: Final[tuple[str, ...]] = ("2.0", "2.1")

REQUIRED_HANDOFF_ARTIFACT_TYPES: Final[frozenset[ArtifactType]] = frozenset(
    {
        ArtifactType.ORIGINAL_DOCUMENT,
        ArtifactType.PARSED_DOCUMENT,
        ArtifactType.EVIDENCE_LEDGER,
        ArtifactType.VALIDATION_REPORT,
    }
)


class Agent1AExtractionPackageV21(ContractModel):
    """Versioned, immutable extraction package produced by Agent 1A (contract 2.1)."""

    contract_version: Literal["2.1"] = "2.1"
    package_id: Identifier
    document: DocumentManifest
    run: RunMetadata
    card: Agent1ACardV2
    evidence_ledger: Annotated[list[EvidenceRecord], Field(max_length=5_000)]
    coverage_trail: Annotated[list[FieldCoverageRecord], Field(min_length=1, max_length=64)]
    validation: ValidationReport
    artifacts: Annotated[list[ArtifactReference], Field(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> Agent1AExtractionPackageV21:
        validate_evidence_ids_are_unique(self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _run_is_the_declared_contract(self) -> Agent1AExtractionPackageV21:
        if self.run.schema_version != self.contract_version:
            raise ValueError(
                f"run.schema_version {self.run.schema_version!r} must equal "
                f"contract_version {self.contract_version!r}"
            )
        return self

    @model_validator(mode="after")
    def _ledger_belongs_to_this_document_version(self) -> Agent1AExtractionPackageV21:
        validate_ledger_belongs_to_document(self.document, self.evidence_ledger)
        validate_pagination_consistency(self.document, self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _evidence_provenance_matches_the_run(self) -> Agent1AExtractionPackageV21:
        for record in self.evidence_ledger:
            if record.parser_name != self.run.parser_name:
                raise ValueError(
                    f"evidence {record.evidence_id!r} was produced by parser "
                    f"{record.parser_name!r}, but the run reports "
                    f"{self.run.parser_name!r}"
                )
            if record.parser_version != self.run.parser_version:
                raise ValueError(
                    f"evidence {record.evidence_id!r} was produced by parser version "
                    f"{record.parser_version!r}, but the run reports "
                    f"{self.run.parser_version!r}"
                )
        return self

    @model_validator(mode="after")
    def _card_references_resolve(self) -> Agent1AExtractionPackageV21:
        validate_card_references(self.card, self.evidence_ledger)
        return self

    @model_validator(mode="after")
    def _coverage_is_consistent_with_the_card(self) -> Agent1AExtractionPackageV21:
        validate_coverage_trail(
            document=self.document,
            card=self.card,
            coverage=self.coverage_trail,
            retrieval_mode=self.run.retrieval_mode,
            retrieval_strategy=self.run.retrieval_strategy,
        )
        return self

    @model_validator(mode="after")
    def _artifacts_are_complete_and_local(self) -> Agent1AExtractionPackageV21:
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
