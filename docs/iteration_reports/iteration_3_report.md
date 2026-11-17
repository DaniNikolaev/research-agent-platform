# Iteration 3 — Agent 1A LangGraph Extraction Workflow

**Project:** `research-agent-platform` · **Date:** 2026-11-17 · **Author:** DSH · **Scope:** iteration 3 ТЗ v1.0

---

## 1. Executive Summary

**Status: `PASS WITH CONDITIONS`.**

A working, tested extraction workflow exists: it takes a finished Document Workflow run,
validates it, routes it through the Quality Gate, executes four extraction groups sequentially
over the full canonical text through the provider-neutral Model Gateway (deterministic fake
adapter), validates every group against a typed schema, persists each result and the run
manifest in the Artifact Store, and keeps LangGraph state in SQLite so a run survives a process
restart and resumes without redoing finished groups.

All items of §4 (scope) are implemented; nothing from §5 (out of scope) was implemented.

The condition is not a defect but the honest nature of the iteration: **the model backend is a
fake adapter**, so the workflow is proven technically and not scientifically. There is no gold
dataset, no accuracy measurement, no real provider — by design.

Delivered numbers: **723 tests passed** (0 failed, 0 skipped; +67 vs 656), `ruff check`,
`ruff format --check`, `ty check`, `uv lock --check`, `export_schemas --check` — all exit 0;
CLI `agent1a extract|report|resume` verified on real runs, including a resume in a second
process.

---

## 2. Initial state

| Item | Value |
|---|---|
| Baseline | Iteration 2 technical closure (`PASS`), 656 tests |
| Environment | Windows 10 Pro 22H2 (10.0.19045) x64, CPython 3.13.18, uv 0.13.7 |
| Pydantic / LangGraph | 2.14.3 / 1.2.20 (`langgraph-checkpoint` 4.3.1) |
| Docling / PyMuPDF | 2.150.0 / 2.1 |
| Added dependency | `langgraph-checkpoint-sqlite==3.1.3` (+ transitive `aiosqlite 0.22.1`, `sqlite-vec 0.1.9`) |
| Lock impact | 3 packages added, no upgrades (`uv lock` diff: 37 insertions) |
| Baseline tests | 656 passed, 0 failed, 0 skipped |
| Frozen contracts | Agent 1A→1B 2.1, Agent 1B Input 1.1, Verification Report 1.1, legacy v1 — unchanged (hashes verified) |

Reused without modification: Model Gateway ports and errors, Fake chat adapter, Run Manager,
InMemoryRunRepository, LocalFileArtifactStore, ArtifactService, observability, configuration
loader, CLI framework, Document Workflow, Parsing and Quality Gate.

## 3. Implemented architecture

```text
cli.py  agent1a extract|report|resume
  └─ app.bootstrap_application → ApplicationContext.agent1a_extraction
       └─ composition/agent1a_extraction.py  (pipeline: gateway, counter, prompts, graph factory)
            ├─ application/services/agent1a_extraction.py   ExtractionService
            ├─ application/services/extraction_prompts.py   PromptTemplate loader
            ├─ application/services/extraction_payloads.py  GroupPayload validation
            ├─ application/services/extraction_errors.py    ErrorCategory hierarchy
            ├─ application/ports/context_budget.py          TokenCounterPort
            ├─ platform/models/fake_extraction.py           FakeExtractionGateway
            ├─ platform/models/token_counting.py            HeuristicTokenCounter
            ├─ platform/checkpointing/sqlite.py             SQLiteCheckpointProvider
            ├─ workflows/agent1a_extraction_state.py        Agent1AExtractionState
            └─ workflows/agent1a_extraction_workflow.py     graph + Agent1AExtractionRunner
```

New components: extraction domain models, extraction service, prompt system, fake structured
gateway, token counting, SQLite checkpointer, extraction graph/state/runner, composition root,
three CLI commands.
Reused: Document Workflow outputs, Artifact Store, Run Manager, Model Gateway contract, logging
(6 new event names added to the closed vocabulary), configuration, CLI helpers.

Layers respected: domain has no infrastructure imports; application depends on ports only;
LangGraph and SQLite stay in `workflows/` and `platform/`; the fake adapter is one implementation
of `ModelGatewayPort`. DSH is not a runtime component.

## 4. GraphState

`Agent1AExtractionState` (version `1.0`) — 28 fields in five groups (identifiers, input
artefacts, extraction, control, results). Validation rejects unknown statuses, malformed ids,
non-artefact URIs, unknown groups and unsupported retrieval modes before a node acts.

Size proof: a completed run serializes to < 4 KB; tests assert that the canonical text and the
`elements` collection are absent, and that all large inputs are referenced by
`artifact://` URIs. Large objects are held in `ExtractionRuntimeCache` (process-local) and in
the Artifact Store.

Typed boundary: LangGraph returns untyped mappings from `invoke`/`get_state`, so a single
`_as_state` helper (one `cast`) converts them at the boundary; every node and service keeps
precise annotations.

## 5. Extraction Workflow

Sequence executed by the graph: `validate_extraction_input → initialize_agent1a_run →
load_parsed_document → check_quality_gate → prepare_full_context → build_extraction_plan →
extract_g1 → validate_and_persist_g1 → … → extract_g4 → validate_and_persist_g4 →
aggregate_extraction_results → persist_run_manifest → finalize_run`.

* Four groups, strictly sequential, each with its own `extract_*` and `validate_and_persist_*`
  node; the router advances only when every group up to that point is `completed`.
* Quality routing: `passed`/`passed_with_warnings` proceed; `requires_ocr` → `controlled_stop`;
  `needs_human_review` → `interrupt_for_review` (LangGraph `interrupt`); anything else →
  `fail_run`. In the three blocked cases the model gateway is **not called** (asserted by a
  spy in tests).
* Final statuses observed: `completed`, `completed_with_warnings`, `waiting_for_human`,
  `failed`; process statuses: `requires_ocr`, `needs_human_review`, `failed`, `completed`.
* Warnings from the Quality Gate and from simulation are carried into the run and the manifest.

## 6. Model Gateway and Fake Adapter

Integration: `ExtractionService` → `ModelGatewayPort.generate_structured(request, schema)` →
`FakeExtractionGateway` → `ModelResponse.structured_payload` → `GroupPayload` validation.

Scenarios implemented (all tested): `success`, `empty_result`, `invalid_schema_once`,
`invalid_schema_always`, `transient_error_once`, `transient_error_always`, `permanent_error`,
`interruption_scenario`.

Determinism: identical requests produce identical payloads (asserted). No external calls: the
test patches socket connect to fail and the adapter still works. Metadata: `provider=fake`,
`model_id=fake-structured-v1`, `is_fake=True`, and every persisted result carries
`execution_mode=simulation` plus a warning. Usage is `None` — no fabricated token metrics.

Limitations are documented in the module: the adapter proves plumbing, routing, validation,
persistence, retry and resume; it does not read the article.

## 7. Prompt Templates

| Prompt id | Group | Version | Fields |
|---|---|---|---|
| `agent1a.extract.g1_bibliography` | G1 | 1.0 | 6 |
| `agent1a.extract.g2_research_design` | G2 | 1.0 | 6 |
| `agent1a.extract.g3_methods_data_variables` | G3 | 1.0 | 10 |
| `agent1a.extract.g4_findings_implications` | G4 | 1.0 | 5 |

Location: `src/research_platform/prompts/agent_1a/v1/*.md`, loaded by a versioned loader
(version `1.0` → directory `v1`, content hash exposed). Content covers role, fields, JSON shape,
source-only rules, empty-value policy, no evidence resolution, structured-only output and the
untrusted-data rule. Prompt ids/versions land in the plan, each group result and the manifest;
unknown prompt-set versions are refused.

## 8. SQLite Checkpointing

Adapter: official `langgraph-checkpoint-sqlite 3.1.3` (`SqliteSaver`), used through
`SQLiteCheckpointProvider`; no manual table writes.

* `thread_id = run_id`; two runs have independent checkpoints (tested).
* Database: `data/checkpoints/agent1a.sqlite3` (configurable, gitignored, outside the Artifact
  Store); LangGraph tables `checkpoints` and `writes` verified on disk.
* Lifecycle: built at bootstrap, connections closed on `ApplicationContext.close()`.
* Restart: a new `bootstrap_application` on the same project restores the state by `run_id`
  (`state_for_run`, `processing_status=completed`, `last_completed_node=finalize_run`).
* Resume: `invoke(None, config)` continues from the breakpoint; finished groups are not
  re-executed (their artifact count stays 1; only G3/G4 run). Verified both in-process and in a
  **separate OS process** (start → new process → resume → report).
* Run record: the in-memory repository has no record after restart, so the runner
  re-materialises it from the checkpoint under the same `run_id` (`_ensure_run`) — documented
  as the reconstruction path for OI-006. Enumeration across restarts is not claimed.

## 9. Artifact Store and Run Manifest

Published per run: `trace` (plan), four `extraction_candidates` (groups) plus one aggregate
`extraction_candidates`, and `run_log` (manifest). URIs:
`artifact://documents/{document_id}/runs/{agent1a_run_id}/{type}/{artifact_id}`.

Every artefact carries SHA-256 and sidecar metadata; a second run of the same document produces
a disjoint identifier set (no overwrite) and earlier artefacts stay readable (tested). The
manifest is sufficient to reconstruct the run: ids, source hash, versions, group statuses and
URIs, quality status, retrieval mode, execution mode, provider/model, prompt set, retries,
warnings, timings, final status. It contains no article text.

## 10. CLI

| Command | Result |
|---|---|
| `agent1a extract --document-run-id <id>` | new run; prints ids, quality, provider/model, execution mode, per-group status and URIs |
| `agent1a report --run-id <id>` | manifest + checkpoint summary (works cross-process) |
| `agent1a resume --run-id <id>` | continues a checkpointed run; refuses `requires_ocr`/`needs_human_review` (`exit 4`) |

Exit codes: `0` completed, `1` input/integrity/contract, `2` configuration, `3` infrastructure,
`4` controlled stop. All three support `--json`.

## 11. Testing

| Layer | File | Tests | Result |
|---|---|---|---|
| Unit: state, payloads, prompts, groups | `tests/agent1a/test_extraction_unit.py` | 27 | passed |
| Fake gateway scenarios | same file | included above | passed |
| LangGraph integration | `tests/agent1a/test_agent1a_workflow.py` | 18 | passed |
| SQLite checkpointing and resume | `tests/agent1a/test_agent1a_sqlite_resume.py` | 10 | passed |
| CLI | `tests/agent1a/test_agent1a_cli.py` | 8 | passed |
| End-to-end PDF/TXT/scan | `tests/parsing/test_agent1a_e2e_parsing.py` | 4 | passed |
| Regression (iterations 0–2) | existing suite | 656 | passed |
| **Total** | | **723** | **passed** |

Static checks: `ruff check` exit 0; `ruff format --check` exit 0 (227 files);
`ty check` exit 0 (0 diagnostics; the 12 introduced during development and one redundant cast
were fixed, with one documented cast at the LangGraph boundary); `uv lock --check` exit 0;
`export_schemas --check` exit 0 (10 schemas unchanged).

End-to-end runs actually executed: TXT document → extraction (all four groups);
digital PDF fixture → extraction; scanned PDF → `requires_ocr` with zero model calls; separate
process → resume → report.

## 12. Changed files

**Created (25):** `domain/extraction/{__init__,models}.py`;
`application/ports/context_budget.py`;
`application/services/{agent1a_extraction,extraction_errors,extraction_payloads,extraction_prompts}.py`;
`composition/agent1a_extraction.py`;
`platform/models/{fake_extraction,token_counting}.py`;
`platform/checkpointing/sqlite.py`;
`prompts/{__init__,__init__}` + `prompts/agent_1a/{__init__, v1/*.md}` (7 files);
`workflows/{agent1a_extraction_state,agent1a_extraction_workflow}.py`;
`tests/agent1a/{__init__,conftest,test_extraction_unit,test_agent1a_workflow,test_agent1a_sqlite_resume,test_agent1a_cli}.py`;
`tests/parsing/{conftest,test_agent1a_e2e_parsing}.py`;
`docs/development/agent_1a_extraction.md`; this report.

**Modified (9):** `app.py` (SQLite provider, extraction pipeline in the context);
`cli.py` (three commands, payload/exit mapping); `config.py` (`CheckpointerKind.SQLITE`,
checkpoint path/timeout, `Agent1ASettings`, profile validation);
`application/ports/logging.py` + `platform/observability/logging.py` (6 event names);
`pyproject.toml` (checkpoint dependency); `uv.lock`; `config/application.example.yaml`
(sqlite + `agent_1a`); `config/models.example.yaml` (`fake-extraction` profile);
`tests/unit/test_platform_foundation.py` (default checkpointer now sqlite);
docs (`testing.md`, `limitations.md`, `architecture_overview.md`, `README.md`).

**Removed:** none.

## 13. Known limitations

1. **No real LLM provider.** All extraction output is simulation (`execution_mode=simulation`).
2. **No proven scientific accuracy.** No gold dataset, no benchmark; accuracy is a later
   Evaluation stage.
3. **No RAG/retrieval.** Only `full_context`; an oversized document stops with
   `extraction_context_overflow` instead of being chunked.
4. **No Evidence Resolver / `EvidenceRecord` / Coverage Trail.** Only `EvidenceCandidate`;
   no candidate is marked verified.
5. **`empty` ≠ `not_found`.** Absence in a fake answer is never reported as absence in the
   publication.
6. **No Agent 1A → Agent 1B package** and no `scientific_review_status` (Agent 1B only).
7. **In-memory RunRepository (OI-006).** After a restart a run is reachable by `run_id`
   (checkpoint + artefacts), not by search or listing; the run record is re-materialised.
8. **HITL channel absent (OI-007).** `needs_human_review` stops the run; a technical resume
   deliberately refuses to confirm the review.
9. **Tables** keep the iteration 2 quality level; their warnings are carried into the run.
10. **Interrupt breakpoints** are configured via `agent_1a.interrupt_after` (empty in
    production; used to prove checkpoint/resume).

## 14. Open questions

| Id | Question | Owner decision needed |
|---|---|---|
| OI-001…004 | Provider, model, capability profile, limits, capability tests | yes, before a real adapter |
| OI-006 | Durable RunRepository / search across restarts | yes, before operational use |
| OI-007 | HITL channel and review acknowledgement | yes, before `needs_human_review` can proceed |
| OI-008 | OCR engine for scans | yes, blocks scanned documents |
| New: prompt governance | Who owns prompt changes and how are they re-versioned? | yes, before relying on prompt quality |
| New: extraction evaluation | Gold dataset, field-level metrics, acceptance thresholds | yes, iteration 6 (Evaluation) |

No OI is closed by this iteration; interfaces and configuration alone do not resolve them.

## 15. Readiness for Iteration 4

| Area | Verdict |
|---|---|
| LangGraph extraction workflow | ready: four sequential groups, routing, retries, persistence, resume verified |
| Fake adapter technical integration | ready as a test double; clearly not a science component |
| SQLite checkpointing | ready for the local runtime; durable RunRepository still open (OI-006) |
| Extraction candidates persistence | ready: typed, hashed, immutable, reconstructable |
| Move to RAG and Evidence Resolver | technically possible: context preparation is isolated behind the token-counter port, candidates are unverified by design, and the workflow has explicit seams for a retrieval mode and a resolver stage |

This iteration does **not** make scientific extraction production-ready, and must not be
presented as such: it delivers the technical contour on which iteration 4 (retrieval, chunking,
hybrid search) and later Evidence Resolution can be built without redesigning the graph.
