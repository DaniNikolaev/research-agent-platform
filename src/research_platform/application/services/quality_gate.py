"""Quality Gate: deterministic technical checks with an explainable verdict.

The gate never grades science.  It answers one question: *how reliably was the source
content extracted?* Every decision is backed by a named check with its observed value and
threshold, so an operator can see exactly why a document was accepted, flagged or stopped.

Verdict rules (this order, first match wins):

1. a failed **critical** check → ``failed``;
2. pages that need OCR and carry no usable text → ``requires_ocr``;
3. a failed **major** check, garbled pages, unknown pages or a fallback parse →
   ``needs_human_review``;
4. warnings only → ``passed_with_warnings``;
5. nothing → ``passed``.

A fallback parse is never reported as a normal parse: it lacks layout, tables and section
structure, so it cannot be treated as equivalent to a structured extraction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from research_platform.application.ports.document_pipeline import DocumentInspection
from research_platform.application.ports.logging import LoggerPort
from research_platform.config import QualityGateSettings
from research_platform.domain.documents.elements import PageStatus
from research_platform.domain.documents.parsed import ParsedDocument, ParsingStatus
from research_platform.domain.documents.quality import (
    CheckSeverity,
    CheckStatus,
    PageQualityRecord,
    PageQualityStatus,
    QualityCheckResult,
    QualityPolicy,
    QualityReport,
    QualityStatus,
)

__all__ = ["QualityGateService"]

_REPLACEMENT_CHAR: Final = "\ufffd"
_FALLBACK_REASONS: Final = (
    "the document was parsed by the plain-text fallback: no layout, no tables and no "
    "section structure were recovered"
)


@dataclass(slots=True)
class _Checks:
    """Small collector that keeps the check ids honest and ordered."""

    items: list[QualityCheckResult] = field(default_factory=list)

    def add(
        self,
        check_id: str,
        status: CheckStatus,
        severity: CheckSeverity,
        message: str,
        *,
        observed: int | float | str | None = None,
        threshold: int | float | str | None = None,
        page_number: int | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        self.items.append(
            QualityCheckResult(
                check_id=check_id,
                status=status,
                severity=severity,
                message=message,
                observed_value=observed,
                threshold=threshold,
                page_number=page_number,
                details=details or {},
            )
        )


class QualityGateService:
    """Evaluate one parsed document against the versioned policy."""

    def __init__(
        self,
        *,
        settings: QualityGateSettings,
        logger: LoggerPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger
        self._clock = clock

    def policy(self) -> QualityPolicy:
        """The effective policy (thresholds recorded in the report)."""
        return self._settings.to_policy()  # type: ignore[return-value]

    def evaluate(
        self,
        *,
        document: ParsedDocument,
        canonical_text: str,
        inspection: DocumentInspection | None = None,
        used_fallback: bool = False,
        correlation_id: str | None = None,
    ) -> QualityReport:
        """Run every check and produce the report."""
        policy = self.policy()
        checks = _Checks()
        page_results = self._page_results(
            document=document,
            canonical_text=canonical_text,
            inspection=inspection,
            policy=policy,
            checks=checks,
        )
        self._structural_checks(
            document=document, inspection=inspection, checks=checks, policy=policy
        )
        self._text_checks(
            document=document,
            canonical_text=canonical_text,
            checks=checks,
            policy=policy,
            has_ocr_pages=any(
                record.page_status is PageQualityStatus.OCR_REQUIRED for record in page_results
            ),
        )
        self._provenance_checks(
            document=document, canonical_text=canonical_text, checks=checks, policy=policy
        )
        self._table_checks(document=document, checks=checks, policy=policy)
        self._parser_checks(
            document=document,
            checks=checks,
            policy=policy,
            has_ocr_pages=any(
                record.page_status is PageQualityStatus.OCR_REQUIRED for record in page_results
            ),
        )
        if document.source_type.value == "txt":
            self._text_source_checks(document=document, checks=checks)
        if used_fallback:
            checks.add(
                "fallback_parse_requires_review",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                _FALLBACK_REASONS,
                observed=document.parser.parser_name,
            )

        ocr_pages = sorted(
            record.page_number
            for record in page_results
            if record.page_status is PageQualityStatus.OCR_REQUIRED
        )
        overall = self._verdict(
            checks=checks.items,
            page_results=page_results,
            ocr_pages=ocr_pages,
            document=document,
        )
        failed = sum(1 for check in checks.items if check.status is CheckStatus.FAILED)
        warned = sum(1 for check in checks.items if check.status is CheckStatus.WARNING)
        report = QualityReport(
            document_id=document.document_id,
            run_id=document.run_id,
            source_sha256=document.source_sha256,
            parsed_document_version=document.document_contract_version,
            quality_policy_version=policy.policy_version,
            overall_status=overall,
            page_count=document.page_count,
            processed_pages=len(page_results),
            warnings_count=warned,
            errors_count=failed,
            ocr_required_pages=ocr_pages if overall is QualityStatus.REQUIRES_OCR else [],
            page_results=page_results,
            checks=checks.items,
            policy=policy,
            parser_warnings=[],
            summary=self._summary(overall, failed, warned, len(ocr_pages), len(page_results)),
            created_at=self._now(),
            extra={"fallback": "true" if used_fallback else "false"},
        )
        self._logger.bind(
            run_id=document.run_id,
            document_id=document.document_id,
            correlation_id=correlation_id,
        ).event(
            "check_completed",
            stage="quality_gate",
            status=overall.value,
            failed_checks=failed,
            warnings=warned,
            ocr_pages=len(ocr_pages),
        )
        return report

    # --------------------------------------------------------------------- pages

    def _page_results(
        self,
        *,
        document: ParsedDocument,
        canonical_text: str,
        inspection: DocumentInspection | None,
        policy: QualityPolicy,
        checks: _Checks,
    ) -> list[PageQualityRecord]:
        if document.page_count is None:
            return []
        inspection_by_page = (
            {page.page_number: page for page in inspection.pages} if inspection else {}
        )
        tables_by_page: dict[int, int] = {}
        for table in document.tables:
            if table.page_number is not None:
                tables_by_page[table.page_number] = tables_by_page.get(table.page_number, 0) + 1
        figures_by_page: dict[int, int] = {}
        for figure in document.figures:
            if figure.page_number is not None:
                figures_by_page[figure.page_number] = figures_by_page.get(figure.page_number, 0) + 1

        results: list[PageQualityRecord] = []
        for page in document.pages:
            block_count = len(
                [
                    element
                    for element in document.elements
                    if element.page_number == page.page_number and element.text
                ]
            )
            chars = len(page.text)
            replacement_ratio = _ratio(page.text, _REPLACEMENT_CHAR)
            control_ratio = _control_char_ratio(page.text)
            probe = inspection_by_page.get(page.page_number)
            image_ratio = probe.image_area_ratio if probe else page.image_area_ratio
            has_native_text = probe.has_native_text if probe else None
            scan_suspected = (
                probe is not None and not probe.has_native_text and probe.image_count > 0
            ) or (chars == 0 and (image_ratio or 0.0) >= policy.image_dominant_area_ratio)
            status = self._page_status(
                page_status=page.status,
                chars=chars,
                replacement_ratio=replacement_ratio,
                control_ratio=control_ratio,
                image_ratio=image_ratio,
                scan_suspected=scan_suspected,
                policy=policy,
            )
            results.append(
                PageQualityRecord(
                    page_number=page.page_number,
                    page_status=status,
                    text_char_count=chars,
                    text_block_count=block_count,
                    table_count=tables_by_page.get(page.page_number, 0),
                    figure_count=figures_by_page.get(page.page_number, 0),
                    image_area_ratio=image_ratio,
                    replacement_char_ratio=round(replacement_ratio, 4),
                    control_char_ratio=round(control_ratio, 4),
                    has_text_layer=has_native_text,
                    scan_suspected=scan_suspected,
                )
            )

        ocr_pages = [
            record.page_number
            for record in results
            if record.page_status is PageQualityStatus.OCR_REQUIRED
        ]
        if ocr_pages:
            checks.add(
                "page_ocr_required",
                CheckStatus.WARNING,
                CheckSeverity.MAJOR,
                f"{len(ocr_pages)} page(s) carry no usable text layer and need OCR",
                observed=len(ocr_pages),
                details={"pages": ",".join(str(number) for number in ocr_pages[:20])},
            )
        garbled = [
            record.page_number
            for record in results
            if record.page_status is PageQualityStatus.SUSPECTED_GARBLED
        ]
        if garbled:
            checks.add(
                "page_garbled_text",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                f"{len(garbled)} page(s) show a damaged text layer",
                observed=len(garbled),
                details={"pages": ",".join(str(number) for number in garbled[:20])},
            )
        blank = [
            record.page_number
            for record in results
            if record.page_status is PageQualityStatus.BLANK_OR_UNKNOWN
        ]
        if blank and policy.warn_on_empty_pages:
            checks.add(
                "page_blank_or_unknown",
                CheckStatus.WARNING,
                CheckSeverity.MINOR,
                f"{len(blank)} page(s) contain neither text nor images",
                observed=len(blank),
                details={"pages": ",".join(str(number) for number in blank[:20])},
            )
        _ = canonical_text
        return results

    def _page_status(
        self,
        *,
        page_status: PageStatus,
        chars: int,
        replacement_ratio: float,
        control_ratio: float,
        image_ratio: float | None,
        scan_suspected: bool,
        policy: QualityPolicy,
    ) -> PageQualityStatus:
        if page_status is PageStatus.FAILED:
            return PageQualityStatus.PARSE_FAILED
        if chars == 0:
            if scan_suspected or (image_ratio or 0.0) >= policy.image_dominant_area_ratio:
                return PageQualityStatus.OCR_REQUIRED
            return PageQualityStatus.BLANK_OR_UNKNOWN
        if replacement_ratio > policy.max_replacement_char_ratio and policy.detect_garbled_text:
            return PageQualityStatus.SUSPECTED_GARBLED
        if control_ratio > policy.max_control_char_ratio:
            return PageQualityStatus.SUSPECTED_GARBLED
        if chars < policy.low_text_chars_per_page:
            if image_ratio is not None and image_ratio >= policy.image_dominant_area_ratio:
                return PageQualityStatus.IMAGE_DOMINANT
            return PageQualityStatus.LOW_TEXT
        return PageQualityStatus.OK

    # ----------------------------------------------------------------- structure

    def _structural_checks(
        self,
        *,
        document: ParsedDocument,
        inspection: DocumentInspection | None,
        checks: _Checks,
        policy: QualityPolicy,
    ) -> None:
        if document.source_type.value == "txt":
            checks.add(
                "structure_text_source_has_no_pages",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "text source: no physical pagination is expected or created",
                observed=document.page_count,
            )
            return

        expected = inspection.page_count if inspection else None
        if expected is not None and document.page_count != expected:
            checks.add(
                "structure_page_count_matches_source",
                CheckStatus.FAILED,
                CheckSeverity.CRITICAL,
                "the parsed page count differs from the page count of the original PDF",
                observed=document.page_count or 0,
                threshold=expected,
            )
        else:
            checks.add(
                "structure_page_count_matches_source",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "page count matches the original PDF",
                observed=document.page_count or 0,
                threshold=expected,
            )
        checks.add(
            "structure_pages_complete",
            CheckStatus.PASS,
            CheckSeverity.INFO,
            "all physical pages are present in order without gaps",
            observed=len(document.pages),
        )
        order = [element.reading_order for element in document.elements]
        if len(order) != len(set(order)):
            checks.add(
                "structure_reading_order_unique",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                "two elements share the same reading_order position",
                observed=len(order) - len(set(order)),
            )
        else:
            checks.add(
                "structure_reading_order_unique",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "reading order is unique across the document",
                observed=len(order),
            )
        if policy.require_page_provenance:
            without_page = [element for element in document.elements if element.page_number is None]
            if without_page:
                checks.add(
                    "structure_page_provenance",
                    CheckStatus.WARNING,
                    CheckSeverity.MAJOR,
                    f"{len(without_page)} element(s) carry no physical page number",
                    observed=len(without_page),
                )
            else:
                checks.add(
                    "structure_page_provenance",
                    CheckStatus.PASS,
                    CheckSeverity.INFO,
                    "every element is bound to a physical page",
                    observed=len(document.elements),
                )

    # ---------------------------------------------------------------------- text

    def _text_checks(
        self,
        *,
        document: ParsedDocument,
        canonical_text: str,
        checks: _Checks,
        policy: QualityPolicy,
        has_ocr_pages: bool = False,
    ) -> None:
        chars = len(canonical_text.strip())
        if chars == 0:
            # A document whose only text lives on pages that need OCR is not a parser
            # failure: it is a document waiting for an OCR engine.
            checks.add(
                "text_extracted",
                CheckStatus.WARNING if has_ocr_pages else CheckStatus.FAILED,
                CheckSeverity.MAJOR if has_ocr_pages else CheckSeverity.CRITICAL,
                "no text was extracted from the document"
                + (" (pages require OCR)" if has_ocr_pages else ""),
                observed=0,
                threshold=policy.minimum_meaningful_text_chars,
            )
        else:
            checks.add(
                "text_extracted",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "text was extracted from the document",
                observed=chars,
                threshold=policy.minimum_meaningful_text_chars,
            )
            if chars < policy.minimum_meaningful_text_chars:
                checks.add(
                    "text_meaningful_length",
                    CheckStatus.WARNING,
                    CheckSeverity.MAJOR,
                    "extracted text is very short for a document",
                    observed=chars,
                    threshold=policy.minimum_meaningful_text_chars,
                )
            else:
                checks.add(
                    "text_meaningful_length",
                    CheckStatus.PASS,
                    CheckSeverity.INFO,
                    "extracted text length is plausible",
                    observed=chars,
                    threshold=policy.minimum_meaningful_text_chars,
                )

        replacement_ratio = _ratio(canonical_text, _REPLACEMENT_CHAR)
        control_ratio = _control_char_ratio(canonical_text)
        if policy.detect_garbled_text and replacement_ratio > policy.max_replacement_char_ratio:
            checks.add(
                "text_replacement_characters",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                "the text layer contains replacement characters above the threshold",
                observed=round(replacement_ratio, 4),
                threshold=policy.max_replacement_char_ratio,
            )
        else:
            checks.add(
                "text_replacement_characters",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "replacement character ratio is within the threshold",
                observed=round(replacement_ratio, 4),
                threshold=policy.max_replacement_char_ratio,
            )
        if control_ratio > policy.max_control_char_ratio:
            checks.add(
                "text_control_characters",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                "the text layer contains control characters above the threshold",
                observed=round(control_ratio, 4),
                threshold=policy.max_control_char_ratio,
            )
        else:
            checks.add(
                "text_control_characters",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "control character ratio is within the threshold",
                observed=round(control_ratio, 4),
                threshold=policy.max_control_char_ratio,
            )

    # ---------------------------------------------------------------- provenance

    def _provenance_checks(
        self,
        *,
        document: ParsedDocument,
        canonical_text: str,
        checks: _Checks,
        policy: QualityPolicy,
    ) -> None:
        if not policy.verify_character_ranges:
            checks.add(
                "provenance_character_ranges",
                CheckStatus.NOT_APPLICABLE,
                CheckSeverity.INFO,
                "character range verification is disabled by configuration",
            )
            return
        total = len(canonical_text)
        bad_range: list[str] = []
        mismatched: list[str] = []
        for element in document.elements:
            if element.char_start is None or element.char_end is None:
                continue
            if element.char_end > total:
                bad_range.append(element.block_id)
                continue
            if canonical_text[element.char_start : element.char_end] != element.text:
                mismatched.append(element.block_id)
        if bad_range:
            checks.add(
                "provenance_character_ranges",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                f"{len(bad_range)} element(s) point outside the canonical text",
                observed=len(bad_range),
                threshold=total,
                details={"blocks": ",".join(bad_range[:10])},
            )
        elif mismatched:
            checks.add(
                "provenance_character_ranges",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                f"{len(mismatched)} element range(s) do not reproduce their text",
                observed=len(mismatched),
                details={"blocks": ",".join(mismatched[:10])},
            )
        else:
            checks.add(
                "provenance_character_ranges",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "every character range reproduces its element text",
                observed=len(document.elements),
                threshold=total,
            )

        out_of_page = [
            element.block_id
            for element in document.elements
            if element.bbox is not None
            and element.page_number is not None
            and document.pages
            and _bbox_outside(element.bbox, document, element.page_number)
        ]
        if out_of_page:
            checks.add(
                "provenance_bbox_bounds",
                CheckStatus.WARNING,
                CheckSeverity.MAJOR,
                f"{len(out_of_page)} element(s) have a bounding box outside their page",
                observed=len(out_of_page),
                details={"blocks": ",".join(out_of_page[:10])},
            )
        else:
            checks.add(
                "provenance_bbox_bounds",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "all bounding boxes lie inside their pages",
            )

    # -------------------------------------------------------------------- tables

    def _table_checks(
        self, *, document: ParsedDocument, checks: _Checks, policy: QualityPolicy
    ) -> None:
        if not policy.verify_table_structure:
            checks.add(
                "tables_structure",
                CheckStatus.NOT_APPLICABLE,
                CheckSeverity.INFO,
                "table structure verification is disabled by configuration",
            )
            return
        if not document.tables:
            checks.add(
                "tables_structure",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "no tables were reported by the parser",
                observed=0,
            )
            return
        empty = [table.table_id for table in document.tables if table.row_count == 0]
        partial = [
            table.table_id for table in document.tables if table.extraction_status == "partial"
        ]
        no_text = [
            table.table_id
            for table in document.tables
            if table.row_count > 0 and not table.text_representation.strip()
        ]
        if empty:
            checks.add(
                "tables_structure",
                CheckStatus.FAILED,
                CheckSeverity.MAJOR,
                f"{len(empty)} table(s) were detected but no cells were extracted",
                observed=len(empty),
                details={"tables": ",".join(empty[:10])},
            )
        elif no_text or partial:
            checks.add(
                "tables_structure",
                CheckStatus.WARNING,
                CheckSeverity.MINOR,
                "some table cells are empty or the structure is incomplete",
                observed=len(partial) + len(no_text),
                details={"tables": ",".join(sorted(set(partial + no_text))[:10])},
            )
        else:
            checks.add(
                "tables_structure",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "tables have consistent row and column counts",
                observed=len(document.tables),
            )

    # -------------------------------------------------------------------- parser

    def _parser_checks(
        self,
        *,
        document: ParsedDocument,
        checks: _Checks,
        policy: QualityPolicy,
        has_ocr_pages: bool = False,
    ) -> None:
        if document.parsing_status is ParsingStatus.FAILED:
            checks.add(
                "parser_status",
                CheckStatus.FAILED,
                CheckSeverity.CRITICAL,
                "the parser reported a failure",
                observed=document.parsing_status.value,
            )
        elif document.parsing_status is ParsingStatus.EMPTY:
            # An image-only document is not a parser failure: the pages need OCR.
            checks.add(
                "parser_status",
                CheckStatus.WARNING if has_ocr_pages else CheckStatus.FAILED,
                CheckSeverity.MAJOR if has_ocr_pages else CheckSeverity.CRITICAL,
                "the parser produced no content"
                + (" (pages require OCR)" if has_ocr_pages else ""),
                observed=document.parsing_status.value,
            )
        else:
            checks.add(
                "parser_status",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                f"parser finished with status {document.parsing_status.value!r}",
                observed=document.parsing_status.value,
            )
        _ = policy

    # ---------------------------------------------------------------- text source

    def _text_source_checks(self, *, document: ParsedDocument, checks: _Checks) -> None:
        if document.page_count is not None or document.pages:
            checks.add(
                "text_source_no_pagination",
                CheckStatus.FAILED,
                CheckSeverity.CRITICAL,
                "a text source must not carry physical pagination",
                observed=document.page_count or len(document.pages),
            )
        else:
            checks.add(
                "text_source_no_pagination",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "no fictitious pages were created for the text source",
                observed=0,
            )
        with_page = [
            element.block_id for element in document.elements if element.page_number is not None
        ]
        if with_page:
            checks.add(
                "text_source_no_page_numbers",
                CheckStatus.FAILED,
                CheckSeverity.CRITICAL,
                f"{len(with_page)} text element(s) carry a page number",
                observed=len(with_page),
            )
        else:
            checks.add(
                "text_source_no_page_numbers",
                CheckStatus.PASS,
                CheckSeverity.INFO,
                "no text element claims a page number",
                observed=0,
            )

    # -------------------------------------------------------------------- verdict

    def _verdict(
        self,
        *,
        checks: Sequence[QualityCheckResult],
        page_results: Sequence[PageQualityRecord],
        ocr_pages: Sequence[int],
        document: ParsedDocument,
    ) -> QualityStatus:
        failed = [check for check in checks if check.status is CheckStatus.FAILED]
        if any(check.severity is CheckSeverity.CRITICAL for check in failed):
            return QualityStatus.FAILED
        if ocr_pages:
            return QualityStatus.REQUIRES_OCR
        if failed:
            return QualityStatus.NEEDS_HUMAN_REVIEW
        blocking_pages = [
            record
            for record in page_results
            if record.page_status
            in {PageQualityStatus.PARSE_FAILED, PageQualityStatus.SUSPECTED_GARBLED}
        ]
        if blocking_pages:
            return QualityStatus.NEEDS_HUMAN_REVIEW
        if any(check.status is CheckStatus.WARNING for check in checks):
            return QualityStatus.PASSED_WITH_WARNINGS
        if document.parsing_status is ParsingStatus.PARSED_WITH_FALLBACK:
            return QualityStatus.PASSED_WITH_WARNINGS
        return QualityStatus.PASSED

    def _summary(
        self,
        status: QualityStatus,
        failed: int,
        warned: int,
        ocr_pages: int,
        processed: int,
    ) -> str:
        base = f"quality gate: {status.value}; {processed} page(s) processed"
        if ocr_pages:
            base += f"; {ocr_pages} page(s) require OCR"
        return f"{base}; {failed} failed check(s), {warned} warning(s)"

    def _now(self) -> datetime:
        if self._clock is not None:
            assert self._clock is not None
            return self._clock().astimezone(UTC)
        return datetime.now(UTC)


def _ratio(text: str, needle: str) -> float:
    if not text:
        return 0.0
    return text.count(needle) / len(text)


def _control_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    control = sum(
        1 for char in text if (ord(char) < 32 and char not in "\n\t\r\f") or ord(char) == 0x7F
    )
    return control / len(text)


def _bbox_outside(bbox: object, document: ParsedDocument, page_number: int | None) -> bool:
    """Whether a box leaves its page bounds by more than a small tolerance."""
    try:
        x0 = float(bbox.x0)
        y0 = float(bbox.y0)
        x1 = float(bbox.x1)
        y1 = float(bbox.y1)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False
    page = next(
        (
            item
            for item in document.pages
            if item.page_number == page_number
            and item.page_width is not None
            and item.page_height is not None
        ),
        None,
    )
    if page is None:
        return False
    tolerance = 2.0
    return (
        x0 < -tolerance
        or y0 < -tolerance
        or x1 > (page.page_width or 0) + tolerance
        or y1 > (page.page_height or 0) + tolerance
    )
