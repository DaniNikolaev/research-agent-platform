"""Consistency rules shared by every contract that carries a card and an evidence ledger.

Both the Agent 1A handoff packages (contract 2.0 and 2.1) and the Agent 1B input must
enforce exactly the same evidence invariants.  They live here, in the domain layer,
because the two agents may not import each other — but both may rely on the domain.

The helpers are written against a structural :class:`EvidenceRecordLike` protocol so the
frozen 2.0 record and the current 2.1 record share one implementation instead of drifting
apart.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from research_platform.domain.cards.models import Agent1ACardV2, iter_card_fields
from research_platform.domain.documents.models import DocumentManifest

__all__ = [
    "EvidenceRecordLike",
    "validate_card_references",
    "validate_evidence_ids_are_unique",
    "validate_ledger_belongs_to_document",
    "validate_pagination_consistency",
]


@runtime_checkable
class EvidenceRecordLike(Protocol):
    """The attributes every evidence record version must expose."""

    @property
    def evidence_id(self) -> str: ...

    @property
    def document_id(self) -> str: ...

    @property
    def source_sha256(self) -> str: ...

    @property
    def page(self) -> int | None: ...

    @property
    def quote(self) -> str: ...

    @property
    def section_path(self) -> list[str] | None: ...


def validate_evidence_ids_are_unique(ledger: Sequence[EvidenceRecordLike]) -> None:
    """Reject a ledger with duplicated ``evidence_id`` values.

    Raises:
        ValueError: two records share an id, which would make a card reference
            ambiguous.
    """
    counts = Counter(record.evidence_id for record in ledger)
    duplicates = sorted(evidence_id for evidence_id, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(
            "duplicate evidence_id values in the evidence ledger: " + ", ".join(duplicates)
        )


def validate_ledger_belongs_to_document(
    document: DocumentManifest, ledger: Sequence[EvidenceRecordLike]
) -> None:
    """Require every record to belong to this document *version*.

    Raises:
        ValueError: a record refers to another document or to another ``source_sha256``.
            Evidence may never be carried across file versions.
    """
    for record in ledger:
        if record.document_id != document.document_id:
            raise ValueError(
                f"evidence {record.evidence_id!r} belongs to document "
                f"{record.document_id!r}, not to {document.document_id!r}"
            )
        if record.source_sha256 != document.source_sha256:
            raise ValueError(
                f"evidence {record.evidence_id!r} was resolved against a different source "
                "version (source_sha256 mismatch); evidence may not be carried over "
                "between file versions"
            )


def validate_pagination_consistency(
    document: DocumentManifest, ledger: Sequence[EvidenceRecordLike]
) -> None:
    """Enforce the physical-pagination rule for every evidence record.

    Raises:
        ValueError: a paginated document has a record without ``page``, an unpaginated
            document has a record with ``page``, or a page is out of bounds.
    """
    paginated = document.page_count is not None
    for record in ledger:
        if paginated and record.page is None:
            raise ValueError(
                f"evidence {record.evidence_id!r} has no page, but document "
                f"{document.document_id!r} is paginated ({document.page_count} pages)"
            )
        if not paginated and record.page is not None:
            raise ValueError(
                f"evidence {record.evidence_id!r} declares page {record.page}, but the "
                "document has no physical pagination"
            )
        if record.page is not None and record.page > (document.page_count or 0):
            raise ValueError(
                f"evidence {record.evidence_id!r} points at page {record.page}, beyond the "
                f"document page count {document.page_count}"
            )


def validate_card_references(card: Agent1ACardV2, ledger: Sequence[EvidenceRecordLike]) -> None:
    """Require every card reference to resolve, and denormalized copies to agree.

    Raises:
        ValueError: a reference points at an unknown ``evidence_id``, or a
            denormalized ``quote``/``page``/``section_path`` disagrees with the ledger.
    """
    index = {record.evidence_id: record for record in ledger}
    for path, field in iter_card_fields(card):
        for reference in field.evidence_refs:
            record = index.get(reference.evidence_id)
            if record is None:
                raise ValueError(
                    f"card field {path!r} references unknown evidence_id {reference.evidence_id!r}"
                )
            if reference.quote is not None and reference.quote != record.quote:
                raise ValueError(
                    f"card field {path!r} has a denormalized quote that differs from "
                    f"evidence {record.evidence_id!r}; the ledger is the source of truth"
                )
            if reference.page is not None and reference.page != record.page:
                raise ValueError(
                    f"card field {path!r} has a denormalized page that differs from "
                    f"evidence {record.evidence_id!r}"
                )
            record_sections = list(record.section_path or [])
            if (
                reference.section_path is not None
                and list(reference.section_path) != record_sections
            ):
                raise ValueError(
                    f"card field {path!r} has a denormalized section_path that differs "
                    f"from evidence {record.evidence_id!r}"
                )
