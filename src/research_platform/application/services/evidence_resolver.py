"""Deterministic Evidence Resolver.

The model proposes a quote inside a chunk; Python decides whether that quote really exists in
the ``ParsedDocument``, which element/table cell contains it, and what its canonical locator is.
The resolver never judges whether the quote *supports* the field, and it never invents page,
span or bbox: those come exclusively from the parsed provenance.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from research_platform.domain.documents.elements import DocumentElement
from research_platform.domain.documents.parsed import ParsedDocument
from research_platform.domain.retrieval.models import (
    EVIDENCE_RESOLVER_VERSION,
    DocumentChunk,
    EvidenceLedger,
    EvidenceObject,
    EvidenceProposal,
    EvidenceTableRef,
    MatchStatus,
    TableRef,
)

__all__ = ["EvidenceResolver", "ResolutionOutcome", "normalize_for_matching", "RESOLVER_VERSION"]

RESOLVER_VERSION = EVIDENCE_RESOLVER_VERSION
_FUZZY_THRESHOLD = 0.9
_WHITESPACE = re.compile(r"\s+")
_SOFT_HYPHEN = "\u00ad"


def normalize_for_matching(text: str) -> str:
    """NFC + whitespace/soft-hyphen normalisation that cannot change meaning.

    Digits, signs, decimal separators, inequality symbols, negation words and variable names
    are untouched: no lowercasing, no punctuation stripping, no number rewriting.
    """
    normalized = unicodedata.normalize("NFC", text).replace(_SOFT_HYPHEN, "")
    return _WHITESPACE.sub(" ", normalized).strip()


@dataclass(slots=True)
class ResolutionOutcome:
    """Result of resolving one proposal."""

    proposal: EvidenceProposal
    status: MatchStatus
    evidence: EvidenceObject | None = None
    detail: str | None = None
    alternatives: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        """Whether a canonical locator was established."""
        return self.status.is_confirmed and self.evidence is not None


class EvidenceResolver:
    """Resolve model-proposed quotes against the parsed document."""

    def __init__(self, *, version: str = RESOLVER_VERSION) -> None:
        self._version = version

    @property
    def version(self) -> str:
        """Resolver version recorded in every evidence object."""
        return self._version

    def resolve(
        self,
        *,
        proposals: list[EvidenceProposal],
        document: ParsedDocument,
        chunks: dict[str, DocumentChunk],
        run_id: str,
        created_at: str,
    ) -> tuple[EvidenceLedger, list[ResolutionOutcome]]:
        """Resolve every proposal and build the evidence ledger."""
        outcomes: list[ResolutionOutcome] = []
        evidence: list[EvidenceObject] = []
        seen_ids: set[str] = set()
        for proposal in proposals:
            outcome = self._resolve_one(
                proposal=proposal, document=document, chunks=chunks
            )
            outcomes.append(outcome)
            if outcome.evidence is not None and outcome.evidence.evidence_id not in seen_ids:
                seen_ids.add(outcome.evidence.evidence_id)
                evidence.append(outcome.evidence)
        unresolved = [
            {
                "candidate_id": item.proposal.candidate_id,
                "field_path": item.proposal.field_path,
                "chunk_id": item.proposal.chunk_id,
                "status": item.status.value,
                "reason": item.detail or "",
            }
            for item in outcomes
            if not item.confirmed
        ]
        warnings = [
            f"{len(unresolved)} of {len(proposals)} evidence candidate(s) are unresolved"
        ] if unresolved else []
        ledger = EvidenceLedger(
            document_id=document.document_id,
            source_sha256=document.source_sha256,
            run_id=run_id,
            resolver_version=self._version,
            evidence=evidence,
            unresolved=unresolved,
            warnings=warnings,
            created_at=created_at,
        )
        return ledger, outcomes

    # ----------------------------------------------------------------- internals

    def _resolve_one(
        self,
        *,
        proposal: EvidenceProposal,
        document: ParsedDocument,
        chunks: dict[str, DocumentChunk],
    ) -> ResolutionOutcome:
        chunk = chunks.get(proposal.chunk_id)
        if chunk is None:
            return ResolutionOutcome(
                proposal=proposal,
                status=MatchStatus.UNRESOLVED,
                detail="chunk_id is not part of this document's chunk set",
            )
        if chunk.document_id != document.document_id or (
            chunk.source_sha256 != document.source_sha256
        ):
            return ResolutionOutcome(
                proposal=proposal,
                status=MatchStatus.UNRESOLVED,
                detail="chunk belongs to a different document or source version",
            )
        quote = proposal.quote_candidate.strip()
        if chunk.chunk_kind.value in {"table", "table_cell"}:
            return self._resolve_table(
                proposal=proposal, quote=quote, chunk=chunk, document=document
            )
        matches = self._text_matches(document, chunk, quote)
        if len(matches) == 1:
            element = matches[0]
            return ResolutionOutcome(
                proposal=proposal,
                status=MatchStatus.EXACT,
                evidence=self._evidence(
                    proposal=proposal,
                    document=document,
                    element=element,
                    quote=element_text_match(element, quote) or quote,
                    status=MatchStatus.EXACT,
                ),
            )
        if len(matches) > 1:
            return ResolutionOutcome(
                proposal=proposal,
                status=MatchStatus.UNRESOLVED,
                detail="the quote occurs in several elements; the locator is ambiguous",
                alternatives=[element.block_id for element in matches[:8]],
            )
        normalized = self._normalized_matches(document, chunk, quote)
        if len(normalized) == 1:
            element = normalized[0]
            return ResolutionOutcome(
                proposal=proposal,
                status=MatchStatus.NORMALIZED_EXACT,
                evidence=self._evidence(
                    proposal=proposal,
                    document=document,
                    element=element,
                    quote=quote,
                    status=MatchStatus.NORMALIZED_EXACT,
                ),
            )
        if len(normalized) > 1:
            return ResolutionOutcome(
                proposal=proposal,
                status=MatchStatus.UNRESOLVED,
                detail="normalised quote occurs in several elements; locator is ambiguous",
                alternatives=[element.block_id for element in normalized[:8]],
            )
        fuzzy = self._fuzzy_match(chunk, quote)
        if fuzzy:
            return ResolutionOutcome(
                proposal=proposal,
                status=MatchStatus.FUZZY_CANDIDATE,
                detail="only a fuzzy match was found; it is not confirmed evidence",
            )
        return ResolutionOutcome(
            proposal=proposal,
            status=MatchStatus.UNRESOLVED,
            detail="quote was not found in the chunk's source elements",
        )

    def _text_matches(
        self, document: ParsedDocument, chunk: DocumentChunk, quote: str
    ) -> list[DocumentElement]:
        element_ids = set(chunk.parent_element_ids)
        return [
            element
            for element in document.elements
            if element.block_id in element_ids and quote in element.text
        ]

    def _normalized_matches(
        self, document: ParsedDocument, chunk: DocumentChunk, quote: str
    ) -> list[DocumentElement]:
        target = normalize_for_matching(quote)
        element_ids = set(chunk.parent_element_ids)
        return [
            element
            for element in document.elements
            if element.block_id in element_ids
            and target in normalize_for_matching(element.text)
        ]

    def _fuzzy_match(self, chunk: DocumentChunk, quote: str) -> bool:
        from difflib import SequenceMatcher

        target = normalize_for_matching(quote)
        source = normalize_for_matching(chunk.text)
        if not target or not source:
            return False
        window = max(len(target), 40)
        best = 0.0
        for start in range(0, max(1, len(source) - window + 1), max(1, window // 4)):
            candidate = source[start : start + window]
            best = max(best, SequenceMatcher(None, target, candidate).ratio())
        return best >= _FUZZY_THRESHOLD

    def _resolve_table(
        self,
        *,
        proposal: EvidenceProposal,
        quote: str,
        chunk: DocumentChunk,
        document: ParsedDocument,
    ) -> ResolutionOutcome:
        table_ids = {ref.table_id for ref in chunk.table_refs}
        for table in document.tables:
            if table.table_id not in table_ids:
                continue
            for row_index, row in enumerate(table.cells):
                for column_index, cell in enumerate(row):
                    if quote in cell:
                        ref = self._cell_ref(chunk, table.table_id, row_index, column_index)
                        header = list(table.cells[0]) if table.has_header_row and table.cells else []
                        element_id = chunk.parent_element_ids[0] if chunk.parent_element_ids else table.table_id
                        evidence_id = self._evidence_id(
                            document,
                            element_id=element_id,
                            quote=cell,
                            status=MatchStatus.EXACT,
                            table_id=table.table_id,
                            cell_id=ref.cell_id if ref else None,
                        )
                        return ResolutionOutcome(
                            proposal=proposal,
                            status=MatchStatus.EXACT,
                            evidence=EvidenceObject(
                                evidence_id=evidence_id,
                                document_id=document.document_id,
                                source_sha256=document.source_sha256,
                                chunk_id=chunk.chunk_id,
                                field_path=proposal.field_path,
                                page_number=table.page_number,
                                section_path=list(chunk.section_path),
                                element_id=element_id,
                                element_type="table_cell",
                                quote_original=cell,
                                bbox=None,
                                table_ref=EvidenceTableRef(
                                    table_id=table.table_id,
                                    cell_id=ref.cell_id if ref else None,
                                    row_index=row_index,
                                    column_index=column_index,
                                    header_values=[value for value in header if value],
                                ),
                                language=str(
                                    getattr(document.language, "value", document.language)
                                ),
                                extraction_method="table_extraction",
                                match_status=MatchStatus.EXACT,
                                resolver_version=self._version,
                            ),
                        )
        return ResolutionOutcome(
            proposal=proposal,
            status=MatchStatus.UNRESOLVED,
            detail="quote was not found in any cell of the chunk's table",
        )

    @staticmethod
    def _cell_ref(
        chunk: DocumentChunk, table_id: str, row_index: int, column_index: int
    ) -> TableRef | None:
        for ref in chunk.table_refs:
            if (
                ref.table_id == table_id
                and ref.row_index == row_index
                and ref.column_index == column_index
            ):
                return ref
        return None

    def _evidence(
        self,
        *,
        proposal: EvidenceProposal,
        document: ParsedDocument,
        element: DocumentElement,
        quote: str,
        status: MatchStatus,
    ) -> EvidenceObject:
        span = None
        if (
            status is MatchStatus.EXACT
            and element.char_start is not None
            and element.text
        ):
            offset = element.text.find(quote)
            if offset >= 0:
                span = {
                    "char_start": element.char_start + offset,
                    "char_end": element.char_start + offset + len(quote),
                }
        return EvidenceObject(
            evidence_id=self._evidence_id(
                document,
                element_id=element.block_id,
                quote=element.text if status is MatchStatus.EXACT else quote,
                status=status,
            ),
            document_id=document.document_id,
            source_sha256=document.source_sha256,
            chunk_id=proposal.chunk_id,
            field_path=proposal.field_path,
            page_number=element.page_number,
            section_path=[],
            element_id=element.block_id,
            element_type=element.element_type.value,
            quote_original=quote,
            source_span=span,
            bbox=element.bbox.model_dump(mode="json") if element.bbox else None,
            language=str(getattr(document.language, "value", document.language)),
            extraction_method="text_layer",
            match_status=status,
            resolver_version=self._version,
        )

    @staticmethod
    def _evidence_id(
        document: ParsedDocument,
        *,
        element_id: str,
        quote: str,
        status: MatchStatus,
        table_id: str | None = None,
        cell_id: str | None = None,
    ) -> str:
        payload = "|".join(
            [
                document.document_id,
                document.source_sha256,
                element_id,
                table_id or "",
                cell_id or "",
                hashlib.sha256(quote.encode("utf-8")).hexdigest()[:16],
                status.value,
            ]
        )
        return "ev_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def element_text_match(element: DocumentElement, quote: str) -> str | None:
    """Return the exact original substring matching ``quote`` inside the element."""
    index = element.text.find(quote)
    if index < 0:
        return None
    return element.text[index : index + len(quote)]
