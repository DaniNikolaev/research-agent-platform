"""Builders for contract documents used across the test suite.

Each builder returns a plain ``dict`` (not a model instance) so that a test can express a
*contract violation* by mutating the dict, exactly as a broken producer would.

Two package generations are built on purpose:

* :func:`valid_package_v20` — the frozen 2.0 shape (no coverage trail, old evidence
  vocabulary);
* :func:`valid_package` — the current 2.1 shape (coverage trail, ``match_status`` +
  ``match_type``, parser provenance).
"""

from __future__ import annotations

import copy
from typing import Any, Final

SOURCE_SHA256: Final = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
PARSED_SHA256: Final = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809"
LEDGER_SHA256: Final = "2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a"
REPORT_SHA256: Final = "3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b"
CANDIDATES_SHA256: Final = "4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c"
PACKAGE_ID: Final = "pkg_20261111_120000"
RUN_ID: Final = "run_20261111_120000_ab12"
DOCUMENT_ID: Final = "doc_en_article_01"

ORIGINAL_URI: Final = (
    f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/original_document/art_original"
)
PARSED_URI: Final = f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/parsed_document/art_parsed"
LEDGER_URI: Final = f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/evidence_ledger/art_ledger"
REPORT_URI: Final = (
    f"artifact://documents/{DOCUMENT_ID}/runs/{RUN_ID}/validation_report/art_validation"
)

PARSER_NAME: Final = "docling"
PARSER_VERSION: Final = "unparsed"
RESOLVER_VERSION: Final = "0.1.0"
COVERAGE_POLICY_VERSION: Final = "1.0"
SEARCH_PLAN_ID: Final = "plan_0001"

CARD_FIELD_PATHS: Final[tuple[str, ...]] = (
    "bibliography.title",
    "bibliography.authors",
    "bibliography.year",
    "bibliography.journal",
    "bibliography.doi",
    "bibliography.keywords",
    "research_design.research_problem",
    "research_design.research_objective",
    "research_design.research_questions",
    "research_design.hypotheses",
    "research_design.theoretical_framework",
    "research_design.literature_context",
    "methodology",
    "data.sample",
    "data.period",
    "data.geographical_scope",
    "data.data_sources",
    "data.observations",
    "variables.dependent",
    "variables.independent",
    "variables.controls",
    "variables.other_variables",
    "findings.main_findings",
    "findings.authors_conclusions",
    "limitations",
    "future_research",
    "contribution",
)


# --------------------------------------------------------------------------- card


def field(
    value: Any = None,
    status: str = "not_found",
    evidence_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A card field object in its JSON form."""
    return {"value": value, "status": status, "evidence_refs": evidence_refs or []}


def empty_card() -> dict[str, Any]:
    """All twenty-seven fields with ``status=not_found``."""
    return {
        "bibliography": {
            "title": field(),
            "authors": field(),
            "year": field(),
            "journal": field(),
            "doi": field(),
            "keywords": field(),
        },
        "research_design": {
            "research_problem": field(),
            "research_objective": field(),
            "research_questions": field(),
            "hypotheses": field(),
            "theoretical_framework": field(),
            "literature_context": field(),
        },
        "methodology": field(),
        "data": {
            "sample": field(),
            "period": field(),
            "geographical_scope": field(),
            "data_sources": field(),
            "observations": field(),
        },
        "variables": {
            "dependent": field(),
            "independent": field(),
            "controls": field(),
            "other_variables": field(),
        },
        "findings": {"main_findings": field(), "authors_conclusions": field()},
        "limitations": field(),
        "future_research": field(),
        "contribution": field(),
    }


def valid_card() -> dict[str, Any]:
    """A card with three ``found`` fields, one ``unclear`` and one ``not_applicable``."""
    card = empty_card()
    card["bibliography"]["title"] = field(
        "Board Gender Diversity and Firm Financial Performance",
        "found",
        [{"evidence_id": "ev_000001"}],
    )
    card["bibliography"]["authors"] = field(
        ["Anna Kowalska", "Michael Brenner", "Sofia Rossi"],
        "found",
        [{"evidence_id": "ev_000002"}],
    )
    card["bibliography"]["year"] = field(2021, "found", [{"evidence_id": "ev_000003"}])
    card["methodology"] = field(
        ["two-way fixed effects", "system GMM"], "found", [{"evidence_id": "ev_000002"}]
    )
    card["research_design"]["research_questions"] = field(
        None, "unclear", [{"evidence_id": "ev_000003"}]
    )
    card["limitations"] = field(None, "not_applicable", [])
    return card


def field_statuses(card: dict[str, Any]) -> dict[str, str]:
    """Flatten a card dict into ``{dotted_path: status}``."""
    statuses: dict[str, str] = {}
    for path in CARD_FIELD_PATHS:
        node: Any = card
        for part in path.split("."):
            node = node[part]
        statuses[path] = node["status"]
    return statuses


# ----------------------------------------------------------------------- evidence


def evidence_record(
    *,
    evidence_id: str = "ev_000001",
    quote: str = "Board Gender Diversity and Firm Financial Performance",
    page: int | None = 1,
    section_path: list[str] | None = None,
    document_id: str = DOCUMENT_ID,
    source_sha256: str = SOURCE_SHA256,
    element_type: str = "paragraph",
    match_status: str = "resolved",
    match_type: str = "exact",
    verification_method: str = "exact_string_match",
    occurrence_index: int | None = 0,
    char_start: int | None = 100,
    char_end: int | None = 154,
    block_id: str = "el_0001",
    bbox: dict[str, float] | None = None,
    table_ref: dict[str, object] | None = None,
    parser_name: str = PARSER_NAME,
    parser_version: str = PARSER_VERSION,
) -> dict[str, Any]:
    """A canonical evidence record in its contract-2.1 JSON form."""
    return {
        "evidence_id": evidence_id,
        "document_id": document_id,
        "source_sha256": source_sha256,
        "block_id": block_id,
        "page": page,
        "printed_page_label": None,
        "section_path": section_path if section_path is not None else ["Title"],
        "element_type": element_type,
        "quote": quote,
        "char_start": char_start,
        "char_end": char_end,
        "bbox": bbox,
        "table_ref": table_ref,
        "occurrence_index": occurrence_index,
        "extraction_method": "text_layer",
        "match_status": match_status,
        "match_type": match_type,
        "verification_method": verification_method,
        "parser_name": parser_name,
        "parser_version": parser_version,
        "resolver_version": RESOLVER_VERSION,
        "created_at": "2026-11-11T12:04:00Z",
    }


def evidence_record_v20(
    *,
    evidence_id: str = "ev_000001",
    quote: str = "Board Gender Diversity and Firm Financial Performance",
    page: int | None = 1,
    section_path: list[str] | None = None,
    document_id: str = DOCUMENT_ID,
    source_sha256: str = SOURCE_SHA256,
    element_type: str = "paragraph",
    match_status: str = "exact",
    verification_method: str = "exact_string_match",
    char_start: int | None = 100,
    char_end: int | None = 154,
    block_id: str = "el_0001",
    bbox: dict[str, float] | None = None,
    table_ref: dict[str, object] | None = None,
) -> dict[str, Any]:
    """A canonical evidence record in the frozen contract-2.0 JSON form."""
    return {
        "evidence_id": evidence_id,
        "document_id": document_id,
        "source_sha256": source_sha256,
        "block_id": block_id,
        "page": page,
        "printed_page_label": None,
        "section_path": section_path if section_path is not None else ["Title"],
        "element_type": element_type,
        "quote": quote,
        "char_start": char_start,
        "char_end": char_end,
        "bbox": bbox,
        "table_ref": table_ref,
        "extraction_method": "text_layer",
        "match_status": match_status,
        "verification_method": verification_method,
        "created_at": "2026-11-11T12:04:00Z",
    }


def valid_ledger() -> list[dict[str, Any]]:
    """Three evidence records (2.1) matching :func:`valid_card` references."""
    return [
        evidence_record(
            evidence_id="ev_000001",
            quote="Board Gender Diversity and Firm Financial Performance",
            char_start=100,
            char_end=154,
            block_id="el_0001",
        ),
        evidence_record(
            evidence_id="ev_000002",
            quote="Anna Kowalska, Michael Brenner, Sofia Rossi",
            char_start=155,
            char_end=196,
            block_id="el_0002",
            section_path=["Authors"],
        ),
        evidence_record(
            evidence_id="ev_000003",
            quote="Journal of Corporate Finance and Governance, Vol. 12, No. 3, 2021",
            char_start=197,
            char_end=259,
            block_id="el_0003",
            section_path=["Journal information"],
        ),
    ]


def valid_ledger_v20() -> list[dict[str, Any]]:
    """Three evidence records (2.0) matching :func:`valid_card` references."""
    return [
        evidence_record_v20(
            evidence_id="ev_000001",
            quote="Board Gender Diversity and Firm Financial Performance",
            char_start=100,
            char_end=154,
            block_id="el_0001",
        ),
        evidence_record_v20(
            evidence_id="ev_000002",
            quote="Anna Kowalska, Michael Brenner, Sofia Rossi",
            char_start=155,
            char_end=196,
            block_id="el_0002",
            section_path=["Authors"],
        ),
        evidence_record_v20(
            evidence_id="ev_000003",
            quote="Journal of Corporate Finance and Governance, Vol. 12, No. 3, 2021",
            char_start=197,
            char_end=259,
            block_id="el_0003",
            section_path=["Journal information"],
        ),
    ]


def evidence_candidate(**overrides: Any) -> dict[str, Any]:
    """An evidence candidate in its contract-2.1 JSON form."""
    payload: dict[str, Any] = {
        "candidate_id": "cand_0001",
        "document_id": DOCUMENT_ID,
        "field_path": "bibliography.title",
        "quote": "Board Gender Diversity and Firm Financial Performance",
        "page_hint": 1,
        "section_hint": "Title",
        "block_id_hint": "el_0001",
        "context_before": None,
        "context_after": None,
        "confidence": 0.9,
        "note": None,
    }
    payload.update(overrides)
    return payload


def evidence_candidate_list() -> dict[str, Any]:
    """A serialisable candidate envelope (the extraction call's structured output)."""
    return {
        "contract_version": "2.1",
        "document_id": DOCUMENT_ID,
        "field_group": "G1",
        "candidates": [evidence_candidate()],
    }


# ---------------------------------------------------------------------- coverage


def search_attempt(
    *,
    attempt_id: str = "att_0001",
    step_id: str = "full_context_pass",
    outcome: str = "succeeded",
    strategy: str | None = None,
    page_count: int = 7,
    blocks: list[str] | None = None,
    sections: list[str] | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """One recorded search attempt."""
    return {
        "attempt_id": attempt_id,
        "step_id": step_id,
        "retrieval_strategy": strategy,
        "searched_sections": sections if sections is not None else ["Body"],
        "searched_page_ranges": [{"start_page": 1, "end_page": page_count}],
        "retrieved_block_ids": blocks if blocks is not None else ["el_0001"],
        "outcome": outcome,
        "error_code": error_code,
    }


def coverage_record(
    field_path: str,
    card_status: str,
    *,
    retrieval_mode: str = "full_context",
    retrieval_strategy: str | None = None,
    page_count: int = 7,
    attempt_id: str = "att_0001",
    coverage_status: str | None = None,
    required_checks_completed: bool = True,
    not_found_eligible: bool | None = None,
    full_text_fallback_performed: bool | None = None,
) -> dict[str, Any]:
    """One coverage record consistent with a card field status."""
    search_mode = retrieval_mode in {"rag", "hybrid"}
    step_id = "rag_dense_lexical_1" if search_mode else "full_context_pass"

    if coverage_status is None:
        coverage_status = "not_applicable" if card_status == "not_applicable" else "complete"

    if coverage_status == "not_applicable":
        return {
            "field_path": field_path,
            "retrieval_mode": retrieval_mode,
            "retrieval_strategy": retrieval_strategy,
            "coverage_status": "not_applicable",
            "search_plan_id": SEARCH_PLAN_ID,
            "searched_sections": [],
            "searched_page_ranges": [],
            "retrieved_block_ids": [],
            "search_attempt_ids": [],
            "full_text_fallback_performed": False,
            "required_checks_completed": required_checks_completed,
            "not_found_eligible": False,
            "coverage_policy_version": COVERAGE_POLICY_VERSION,
            "attempts": [],
            "notes": [],
        }

    attempts = [
        search_attempt(
            attempt_id=attempt_id,
            step_id=step_id,
            strategy=retrieval_strategy,
            page_count=page_count,
        )
    ]
    if full_text_fallback_performed is None:
        full_text_fallback_performed = bool(card_status == "not_found" and search_mode)
    if full_text_fallback_performed:
        attempts.append(
            search_attempt(
                attempt_id=f"{attempt_id}_fb",
                step_id="fallback_full_text_scan",
                strategy=retrieval_strategy,
                page_count=page_count,
            )
        )
    if not_found_eligible is None:
        not_found_eligible = card_status == "not_found"

    return {
        "field_path": field_path,
        "retrieval_mode": retrieval_mode,
        "retrieval_strategy": retrieval_strategy,
        "coverage_status": coverage_status,
        "search_plan_id": SEARCH_PLAN_ID,
        "searched_sections": ["Body"],
        "searched_page_ranges": [{"start_page": 1, "end_page": page_count}],
        "retrieved_block_ids": ["el_0001"],
        "search_attempt_ids": [item["attempt_id"] for item in attempts],
        "full_text_fallback_performed": full_text_fallback_performed,
        "required_checks_completed": required_checks_completed,
        "not_found_eligible": not_found_eligible,
        "coverage_policy_version": COVERAGE_POLICY_VERSION,
        "attempts": attempts,
        "notes": [],
    }


def coverage_trail(
    card: dict[str, Any],
    *,
    retrieval_mode: str = "full_context",
    retrieval_strategy: str | None = None,
    page_count: int = 7,
) -> list[dict[str, Any]]:
    """A complete 27-record coverage trail consistent with the given card."""
    statuses = field_statuses(card)
    return [
        coverage_record(
            path,
            statuses[path],
            retrieval_mode=retrieval_mode,
            retrieval_strategy=retrieval_strategy,
            page_count=page_count,
            attempt_id=f"att_{index:04d}",
        )
        for index, path in enumerate(CARD_FIELD_PATHS, start=1)
    ]


# ------------------------------------------------------------- document / run


def valid_document() -> dict[str, Any]:
    """A paginated PDF manifest."""
    return {
        "document_id": DOCUMENT_ID,
        "source_file_name": "en_article.pdf",
        "source_media_type": "application/pdf",
        "language": "en",
        "page_count": 7,
        "source_sha256": SOURCE_SHA256,
        "original_artifact_uri": ORIGINAL_URI,
    }


def valid_run(
    *,
    retrieval_mode: str = "full_context",
    retrieval_strategy: str | None = None,
    contract_version: str = "2.1",
) -> dict[str, Any]:
    """A run with complete reproducibility metadata for the given retrieval mode."""
    search_mode = retrieval_mode in {"rag", "hybrid"}
    payload: dict[str, Any] = {
        "run_id": RUN_ID,
        "agent_id": "1A",
        "agent_version": "0.1.0",
        "prompt_version": "1a-prompt-2.1.0",
        "schema_version": contract_version,
        "model_provider": "unresolved_provider",
        "model_id": "unresolved_model",
        "model_configuration_fingerprint": "sha256:9f86d081884c7d659a2feaa0c55ad015",
        "parser_name": PARSER_NAME,
        "parser_version": PARSER_VERSION,
        "started_at": "2026-11-11T12:00:00Z",
        "completed_at": "2026-11-11T12:05:00Z",
        "retrieval_mode": retrieval_mode,
        "embedding_model_id": "bge-m3" if search_mode else None,
        "index_version": "idx_0001" if search_mode else None,
    }
    if contract_version == "2.1":
        payload["retrieval_strategy"] = retrieval_strategy if search_mode else None
    return payload


def run_rag() -> dict[str, Any]:
    """A 2.1 run in RAG mode with dense+lexical strategy."""
    return valid_run(retrieval_mode="rag", retrieval_strategy="dense_lexical")


def run_hybrid() -> dict[str, Any]:
    """A 2.1 run in hybrid mode with lexical strategy."""
    return valid_run(retrieval_mode="hybrid", retrieval_strategy="lexical")


# ------------------------------------------------------------------- artifacts


def artifact(
    artifact_type: str,
    artifact_id: str,
    uri: str,
    *,
    media_type: str = "application/json",
    sha256: str = PARSED_SHA256,
    size_bytes: int = 1024,
) -> dict[str, Any]:
    """One artifact reference in its JSON form."""
    return {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "uri": uri,
        "media_type": media_type,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "version": "1",
        "created_at": "2026-11-11T12:04:30Z",
    }


def valid_artifacts() -> list[dict[str, Any]]:
    """The minimum handoff artefact set."""
    return [
        artifact(
            "original_document",
            "art_original",
            ORIGINAL_URI,
            media_type="application/pdf",
            sha256=SOURCE_SHA256,
            size_bytes=2048,
        ),
        artifact("parsed_document", "art_parsed", PARSED_URI, sha256=PARSED_SHA256),
        artifact("evidence_ledger", "art_ledger", LEDGER_URI, sha256=LEDGER_SHA256),
        artifact("validation_report", "art_validation", REPORT_URI, sha256=REPORT_SHA256),
    ]


def valid_validation() -> dict[str, Any]:
    """A technically valid, semantically unverified validation report."""
    return {
        "technical_status": "valid",
        "technical": {
            "schema_valid": True,
            "validator_name": "research-platform.contract-validator",
            "validator_version": "0.1.0",
            "performed_at": "2026-11-11T12:04:30Z",
            "checks": [
                {
                    "check_id": "schema",
                    "description": "JSON Schema validation",
                    "outcome": "passed",
                    "detail": None,
                }
            ],
            "errors": [],
            "warnings": [],
        },
        "semantic": {
            "status": "pending_agent_1b",
            "independent_verification_required": True,
            "notes": [],
        },
    }


# --------------------------------------------------------------------- packages


def valid_package() -> dict[str, Any]:
    """A complete, contract-satisfying ``Agent1AExtractionPackageV21`` document."""
    card = valid_card()
    return {
        "contract_version": "2.1",
        "package_id": PACKAGE_ID,
        "document": valid_document(),
        "run": valid_run(),
        "card": card,
        "evidence_ledger": valid_ledger(),
        "coverage_trail": coverage_trail(card),
        "validation": valid_validation(),
        "artifacts": valid_artifacts(),
    }


def valid_package_rag() -> dict[str, Any]:
    """A contract-2.1 package produced in RAG mode (fallback evidenced)."""
    card = valid_card()
    return {
        "contract_version": "2.1",
        "package_id": PACKAGE_ID,
        "document": valid_document(),
        "run": run_rag(),
        "card": card,
        "evidence_ledger": valid_ledger(),
        "coverage_trail": coverage_trail(
            card, retrieval_mode="rag", retrieval_strategy="dense_lexical"
        ),
        "validation": valid_validation(),
        "artifacts": valid_artifacts(),
    }


def valid_package_v20() -> dict[str, Any]:
    """A complete, contract-satisfying ``Agent1AExtractionPackageV2`` (frozen 2.0)."""
    return {
        "contract_version": "2.0",
        "package_id": PACKAGE_ID,
        "document": valid_document(),
        "run": valid_run(contract_version="2.0"),
        "card": valid_card(),
        "evidence_ledger": valid_ledger_v20(),
        "validation": valid_validation(),
        "artifacts": valid_artifacts(),
    }


# ------------------------------------------------------------------- Agent 1B


def valid_agent_1b_input() -> dict[str, Any]:
    """A complete, contract-satisfying ``Agent1BInputPackageV11`` document."""
    package = valid_package()
    return {
        "contract_version": "1.1",
        "package_id": "pkg_1b_input_0001",
        "agent_1a_contract_version": package["contract_version"],
        "agent_1a_package_id": package["package_id"],
        "document": package["document"],
        "source_access": {
            "original_artifact_uri": ORIGINAL_URI,
            "source_sha256": SOURCE_SHA256,
            "read_only": True,
        },
        "run": package["run"],
        "card": package["card"],
        "evidence_ledger": package["evidence_ledger"],
        "coverage": {
            "availability": "recorded",
            "source_contract_version": "2.1",
            "policy_version": COVERAGE_POLICY_VERSION,
            "records": package["coverage_trail"],
        },
        "validation": package["validation"],
        "artifacts": package["artifacts"],
        "mutation_policy": {
            "card_mutation_allowed": False,
            "corrections_require_new_package": True,
        },
    }


def valid_agent_1b_input_v10() -> dict[str, Any]:
    """A contract-1.0 Agent 1B input built from a frozen 2.0 package."""
    package = valid_package_v20()
    return {
        "contract_version": "1.0",
        "package_id": "pkg_1b_input_0001",
        "agent_1a_contract_version": package["contract_version"],
        "agent_1a_package_id": package["package_id"],
        "document": package["document"],
        "source_access": {
            "original_artifact_uri": ORIGINAL_URI,
            "source_sha256": SOURCE_SHA256,
            "read_only": True,
        },
        "run": package["run"],
        "card": package["card"],
        "evidence_ledger": package["evidence_ledger"],
        "validation": package["validation"],
        "artifacts": package["artifacts"],
        "mutation_policy": {
            "card_mutation_allowed": False,
            "corrections_require_new_package": True,
        },
    }


def verification_report(**overrides: Any) -> dict[str, Any]:
    """A contract-1.1 report that verifies one field and flags nothing."""
    payload: dict[str, Any] = {
        "contract_version": "1.1",
        "report_id": "rep_1b_0001",
        "agent_1a_package_id": PACKAGE_ID,
        "agent_1a_contract_version": "2.1",
        "agent_id": "1B",
        "agent_version": "0.1.0",
        "prompt_version": "1b-prompt-1.1.0",
        "model_provider": "unresolved_provider",
        "model_id": "unresolved_model",
        "model_configuration_fingerprint": "sha256:fingerprint",
        "started_at": "2026-11-11T13:00:00Z",
        "completed_at": "2026-11-11T13:10:00Z",
        "scientific_review_status": "verified",
        "coverage_reviewed": True,
        "summary": "Card checked against the original article.",
        "findings": [
            {
                "finding_id": "find_0001",
                "field_path": "bibliography.title",
                "status": "verified",
                "severity": "info",
                "comment": "Title matches page 1 of the original.",
                "agent_1a_evidence_ids": ["ev_000001"],
                "agent_1b_evidence": [
                    evidence_record(evidence_id="ev_1b_0001", quote="Title on page one")
                ],
                "coverage_verdict": "confirmed_complete",
            }
        ],
    }
    payload.update(overrides)
    return payload


def valid_verification_report() -> dict[str, Any]:
    """Convenience alias for :func:`verification_report` with no overrides."""
    return verification_report()


def valid_verification_report_v10() -> dict[str, Any]:
    """A contract-1.0 report (frozen shape: no ``omitted``, no coverage verdict)."""
    return {
        "contract_version": "1.0",
        "report_id": "rep_1b_0001",
        "agent_1a_package_id": PACKAGE_ID,
        "agent_1a_contract_version": "2.0",
        "agent_id": "1B",
        "agent_version": "0.1.0",
        "prompt_version": "1b-prompt-1.0.0",
        "model_provider": "unresolved_provider",
        "model_id": "unresolved_model",
        "model_configuration_fingerprint": "sha256:fingerprint",
        "started_at": "2026-11-11T13:00:00Z",
        "completed_at": "2026-11-11T13:10:00Z",
        "scientific_review_status": "verified",
        "summary": "Card checked against the original article.",
        "findings": [
            {
                "finding_id": "find_0001",
                "field_path": "bibliography.title",
                "status": "verified",
                "severity": "info",
                "comment": "Title matches page 1 of the original.",
                "agent_1a_evidence_ids": ["ev_000001"],
                "agent_1b_evidence": [
                    evidence_record_v20(evidence_id="ev_1b_0001", quote="Title on page one")
                ],
            }
        ],
    }


def deep_copy(value: Any) -> Any:
    """Deep copy helper used by mutation tests."""
    return copy.deepcopy(value)
