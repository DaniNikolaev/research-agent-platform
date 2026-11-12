"""``Agent1ACardV1ToV2Adapter`` — interface and mapping specification only.

Iteration 0 does not perform a migration.  What is defined here is the *contract* a
future adapter must satisfy, and the rule that makes it safe:

* the input is a legacy card v1.0 document and it is **read-only** — the adapter
  returns a new package and never mutates the legacy JSON;
* a v2 package can only be produced when the mandatory provenance exists
  (:data:`REQUIRED_V1_PROVENANCE`).  Legacy cards record neither the source hash, nor
  the model/run identity, nor block-level evidence provenance, so an adapter must
  report what is missing instead of inventing plausible values;
* "unknown" placeholders are acceptable only inside an explicit
  :class:`AdapterOutcome.MISSING_METADATA` result, never inside a produced package.

**Target version.**  The produced package type is the frozen
:class:`~research_platform.agents.agent_1a.contracts.package.Agent1AExtractionPackageV2`
(contract 2.0), not 2.1.  This is deliberate: contract 2.1 requires a Coverage Trail, and a
coverage trail describes a search that actually ran — it cannot be reconstructed for a card
that was produced years ago.  A historical card may therefore become a 2.0 package (which
Agent 1B receives with ``coverage.availability = not_recorded``), never a 2.1 package.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from pydantic import model_validator

from research_platform.agents.agent_1a.contracts.package import (
    Agent1AExtractionPackageV2,
)
from research_platform.domain.base import ContractModel, NonEmptyStr

__all__ = [
    "REQUIRED_V1_PROVENANCE",
    "AdapterOutcome",
    "AdapterResult",
    "Agent1ACardV1ToV2Adapter",
    "missing_required_provenance",
]


class AdapterOutcome(StrEnum):
    """Result of a v1 → v2 adaptation attempt."""

    CONVERTIBLE = "convertible"
    MISSING_METADATA = "missing_metadata"
    REJECTED = "rejected"


REQUIRED_V1_PROVENANCE: Final[tuple[str, ...]] = (
    "source_sha256",
    "run.run_id",
    "run.agent_version",
    "run.prompt_version",
    "run.schema_version",
    "run.model_provider",
    "run.model_id",
    "run.model_configuration_fingerprint",
    "run.parser_name",
    "run.parser_version",
    "run.started_at",
    "run.completed_at",
    "evidence_provenance.source_sha256",
    "evidence_provenance.block_id",
    "evidence_provenance.char_span",
    "evidence_provenance.match_status",
)
"""Dotted paths of the metadata a v2 package needs but a v1 card does not contain."""


def _lookup(provenance: Mapping[str, Any], dotted: str) -> Any:
    node: Any = provenance
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def missing_required_provenance(provenance: Mapping[str, Any]) -> list[str]:
    """Return the dotted paths of required provenance that are absent or empty.

    Presence is not enough: an empty string or ``None`` is treated as missing, so a
    caller cannot satisfy the contract with ``""`` placeholders.
    """
    missing: list[str] = []
    for dotted in REQUIRED_V1_PROVENANCE:
        value = _lookup(provenance, dotted)
        if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0):
            missing.append(dotted)
    return missing


class AdapterResult(ContractModel):
    """Outcome of one adaptation attempt.

    The three outcomes are mutually exclusive and the payload is checked against the
    outcome, so a "missing metadata" result can never smuggle in a half-invented
    package.
    """

    outcome: AdapterOutcome
    missing_required: tuple[NonEmptyStr, ...] = ()
    package: Agent1AExtractionPackageV2 | None = None
    notes: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _outcome_matches_payload(self) -> AdapterResult:
        if self.outcome is AdapterOutcome.CONVERTIBLE:
            if self.package is None:
                raise ValueError("outcome 'convertible' requires a v2 package")
            if self.missing_required:
                raise ValueError(
                    "outcome 'convertible' must not report missing required provenance"
                )
        elif self.package is not None:
            raise ValueError(
                f"outcome {self.outcome.value!r} must not carry a v2 package; the adapter "
                "does not invent provenance"
            )
        if self.outcome is AdapterOutcome.MISSING_METADATA and not self.missing_required:
            raise ValueError(
                "outcome 'missing_metadata' requires the list of missing provenance paths"
            )
        return self


@runtime_checkable
class Agent1ACardV1ToV2Adapter(Protocol):
    """Mapping specification between legacy ``card_schema.json`` v1.0 and v2."""

    def adapt(self, card_v1: Mapping[str, Any], *, provenance: Mapping[str, Any]) -> AdapterResult:
        """Map a legacy card to a v2 package when — and only when — provenance exists.

        Implementations must:

        1. treat ``card_v1`` as immutable input (no in-place edits);
        2. never derive ``source_sha256``, run identity, prompt/model identity or
           evidence coordinates from the card itself;
        3. return :class:`AdapterOutcome.MISSING_METADATA` with the concrete list of
           missing paths instead of emitting a package with fabricated values;
        4. report an unknown legacy field or status as
           :class:`AdapterOutcome.REJECTED` with a diagnostic note, never by silently
           dropping it.
        """
        ...
