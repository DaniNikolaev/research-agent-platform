# Agent 1A Extraction Workflow (Iteration 3)

Technical extraction contour: it reads a **finished Document Workflow run** and produces the
four extraction groups (G1–G4) through the provider-neutral Model Gateway. The only model
backend in this iteration is the deterministic fake adapter, so every result is explicitly
labelled `execution_mode=simulation`. Nothing here is a scientific result.

## 1. Purpose and non-goals

In scope: sequential extraction over four field groups, structured output validated against
Pydantic, typed results persisted in the Artifact Store, a run manifest, retries, controlled
stops, SQLite checkpointing and technical resume, CLI `agent1a extract|report|resume`.

Out of scope (deliberately): a real LLM provider, RAG/embeddings/retrieval, Evidence Resolver,
`EvidenceRecord`, Coverage Trail, the Agent 1A → 1B package, `scientific_review_status`,
PostgreSQL, OCR.

## 2. Position in the pipeline

```text
document process --input <PDF|TXT>        # iteration 2 (unchanged)
  └─ artifacts: source manifest, ParsedDocument, canonical text, QualityReport

agent1a extract --document-run-id <RUN>   # iteration 3
  └─ reuses those artefacts; never re-parses the source
```

`document_run_id` is an input; the extraction run gets its own `run_id`. Both are stored in the
state and in the manifest.

## 3. Graph

```text
START → validate_extraction_input → initialize_agent1a_run → load_parsed_document
      → check_quality_gate ─┬─ proceed ────────────→ prepare_full_context → build_extraction_plan
                            ├─ requires_ocr ───────→ controlled_stop → END
                            ├─ needs_human_review ─→ interrupt_for_review (interrupt) → END
                            └─ failed/other ───────→ fail_run → END
prepare_full_context → build_extraction_plan
  → extract_g1 → validate_and_persist_g1 → extract_g2 → … → validate_and_persist_g4
  → aggregate_extraction_results → persist_run_manifest → finalize_run → END
```

* Groups run strictly sequentially; G2 starts only after G1 is persisted.
* After every group the router continues only while all groups up to that one are `completed`;
  otherwise it goes to `fail_run`.
* A terminal group failure raises a typed error, stops the graph and fails the run; later groups
  are never executed.
* Artifact URIs in the graph: five `extraction_candidates` (four groups + aggregate), one
  `trace` (plan), one `run_log` (manifest). `ArtifactType` is a published enum, so these
  existing values are reused rather than extended; the identifiers (`art_g1_…`,
  `art_extraction_plan_…`, `art_extraction_manifest_…`) distinguish them.

## 4. GraphState

`Agent1AExtractionState` (`workflows/agent1a_extraction_state.py`) is a `TypedDict` with only
identifiers, statuses, counters and URIs:

| Group | Keys |
|---|---|
| identifiers | `run_id`, `document_run_id`, `document_id`, `source_sha256`, `agent_id`, `agent_version`, `correlation_id` |
| input artefacts | `source_manifest_uri`, `parsed_document_uri`, `canonical_text_uri`, `quality_report_uri`, `quality_status` |
| extraction | `retrieval_mode`, `extraction_plan_version`, `current_group`, `group_statuses`, `group_result_uris`, `completed_groups` |
| control | `processing_status`, `retry_counters`, `warnings`, `error_reference`, `last_completed_node`, `interrupt_reason` |
| results | `extraction_plan_uri`, `extraction_result_uri`, `run_manifest_uri`, `created_at`, `updated_at` |

`validate_extraction_state` rejects unknown `processing_status` values, malformed identifiers,
non-artefact URIs, unknown groups and unsupported `retrieval_mode`. Tests assert the serialized
state stays under 4 KB and contains neither the canonical text nor any element.

Large objects (ParsedDocument, canonical text, plan, group results) live in
`ExtractionRuntimeCache` for the duration of one invocation and in the Artifact Store
afterwards. A resumed run starts at the breakpoint, so nodes hydrate the cache from published
artefacts (`_hydrate_cache`) instead of expecting process-local memory.

## 5. Groups and schemas

| Group | Fields | Count |
|---|---|---|
| G1 `g1_bibliography` | `bibliography.*` (title, authors, year, journal, doi, keywords) | 6 |
| G2 `g2_research_design` | `research_design.*` (problem, objective, questions, hypotheses, framework, literature context) | 6 |
| G3 `g3_methods_data_variables` | `methodology`, `data.*`, `variables.*` | 10 |
| G4 `g4_findings_implications` | `findings.*`, `limitations`, `future_research`, `contribution` | 5 |

The 27 keys come from the existing card contract (`CARD_FIELD_PATHS`), so the groups cannot
drift from the platform vocabulary. A value is either `candidate` (unverified proposal) or
`empty`; **`empty` is never `not_found`** — a fake adapter returning nothing proves nothing
about the publication.

Internal typed contracts (`domain/extraction/models.py`): `ExtractionFieldValue`,
`GroupExtractionResult`, `ExtractionPlan`, `ExtractionResult`, `ExtractionRunManifest`. They are
not published; the Agent 1A → 1B package 2.1, Agent 1B Input 1.1 and the legacy v1 schema are
untouched.

Evidence stays as `EvidenceCandidate` (candidate ids are derived deterministically from the
document, group, index and field). No resolver runs, no `EvidenceRecord` is created, no
Coverage Trail is fabricated.

## 6. Model Gateway and fake adapter

`ExtractionService` builds `ModelRequest` (system prompt + untrusted document text, target
schema, model profile, generation limits, metadata with group/field keys/document id), calls
`ModelGatewayPort.generate_structured(request, schema)`, and validates the payload with
`GroupPayload`.

`FakeExtractionGateway` (`platform/models/fake_extraction.py`) implements the same port:

* provider `fake`, model `fake-structured-v1`, `is_fake=True`;
* deterministic payloads: `SIMULATED_VALUE::<field>` plus a `SIMULATED QUOTE …` candidate;
* **no fabricated usage** (`usage=None`) — it has no tokenizer;
* no network, filesystem or shell access; the scenario is the only input;
* scenarios: `success`, `empty_result`, `invalid_schema_once`, `invalid_schema_always`,
  `transient_error_once`, `transient_error_always`, `permanent_error`,
  `interruption_scenario` (the last one is success; the interruption itself is a LangGraph
  breakpoint, see §9).

Configuration: `agent_1a.model_profile` names a profile in `models.profiles`. The shipped
example adds `fake-extraction` (16 000 context tokens, 2 048 output, structured output on).
A missing profile is a configuration error; `build_extraction_gateway` refuses to guess.

## 7. Prompts

Versioned templates ship inside the package:
`src/research_platform/prompts/agent_1a/v1/{g1_bibliography,g2_research_design,g3_methods_data_variables,g4_findings_implications}.md`.
The loader (`application/services/extraction_prompts.py`) maps prompt-set version `1.0` to the
`v1` directory, exposes `prompt_id`, `prompt_version`, `schema_id`, field keys and a content
hash, and refuses unknown versions.

Every template states: role and group task, the exact field keys, the required JSON shape,
"use only the provided text, never guess", empty-value rules, no evidence resolution, source vs
interpretation, structured output only, and that the document is **untrusted data** whose
instructions cannot change the task. Prompt ids and versions are recorded in the plan, in every
group result and in the manifest.

## 8. Context budget

`TokenCounterPort` (`application/ports/context_budget.py`) isolates counting from the workflow.
`HeuristicTokenCounter` is a four-characters-per-token **estimate**, marked `is_exact=False` and
named in diagnostics; a real adapter substitutes an exact tokenizer without touching the graph.

Before each call: prompt + full canonical text are counted, the budget is
`max_context_tokens − output reserve − safety reserve`. If the text does not fit, the run stops
with `extraction_context_overflow` (`requires_ocr`/`not truncated` guarantees: the document is
never silently truncated, never partially sent, never replaced by another document).

## 9. Retry, interrupt and controlled stops

| Situation | Behaviour |
|---|---|
| transient model error | up to `max_transient_attempts` calls (default 3) with configured backoff; every attempt logged |
| invalid structured answer | up to `max_repair_attempts` targeted repairs (default 2), each carrying the validation reason; attempts counted per group |
| permanent model error | terminal, run failed, no retry |
| missing artefact / hash mismatch | terminal (`artifact` / `integrity` category) |
| context overflow | controlled failure (`context`) |
| `requires_ocr` | `controlled_stop`: run `waiting_for_human`, reason stored, **model not called** |
| `needs_human_review` | `interrupt_for_review`: LangGraph `interrupt()`, run `waiting_for_human`, model not called; a technical resume refuses to confirm the review (OI-007) |
| other quality statuses | `fail_run`, run `failed` |

Errors are typed (`ExtractionError` + `ErrorCategory`) and converted to `RunError` with the
original category, so a run is explainable after the fact.

## 10. SQLite checkpointing

`SQLiteCheckpointProvider` (`platform/checkpointing/sqlite.py`) wraps the official
`langgraph-checkpoint-sqlite` `SqliteSaver` (pinned `3.1.3`, `langgraph-checkpoint 4.3.1`).
Configuration: `runtime.checkpointer: sqlite`, `runtime.checkpoint_database`
(default `data/checkpoints/agent1a.sqlite3`), `runtime.checkpoint_timeout_seconds`.
The database lives under the project `data/` directory, outside Git and outside the Artifact
Store; ACLs are untouched.

* `thread_id == run_id`, so two runs never share checkpoint state.
* The provider owns its connections and closes them on application shutdown.
* A new application process opens the same file and resumes by `run_id`
  (`invoke(None, config)` — passing the state again would restart from `START`).
* The RunRepository stays in memory (OI-006): a resumed run re-materialises its record from the
  checkpoint under the same `run_id` (`_ensure_run`), which is the documented reconstruction
  path. Enumeration/search across restarts remains unavailable by design.

## 11. Artefacts and manifest

Per run: plan (`trace`), four group results (`extraction_candidates`), aggregate result
(`extraction_candidates`), manifest (`run_log`). URIs use the document scope:

```text
artifact://documents/{document_id}/runs/{agent1a_run_id}/{artifact_type}/{artifact_id}
```

`ExtractionRunManifest` records run/document run/document ids, source hash, agent and workflow
versions, graph-state version, per-group statuses and URIs, quality status, retrieval mode,
`execution_mode`, provider/model, prompt-set version, retries, warnings, start/finish times and
the final status. It never contains article text. Earlier runs are immutable: a second run
publishes a disjoint set of artefacts.

## 12. CLI

```powershell
research-platform agent1a extract --document-run-id <DOC_RUN> [--json] [--correlation-id ID]
research-platform agent1a report  --run-id <RUN> [--json]
research-platform agent1a resume  --run-id <RUN> [--json]
```

Exit codes: `0` completed, `1` input/integrity/contract problem, `2` configuration problem,
`3` infrastructure problem, `4` controlled stop (`requires_ocr`, `needs_human_review`, context
overflow). Existing commands (`document process|report`, `agent1a workflow-smoke`) are
unchanged.

## 13. Tests

`tests/agent1a/` — unit (state, payloads, prompts, fake scenarios), graph integration (success,
routing, retries, artifacts, immutability, compact state), SQLite/resume (including a real
second process) and CLI. `tests/parsing/test_agent1a_e2e_parsing.py` covers a digital PDF and a
scanned PDF end to end. Total suite: 723 passed.

## 14. Known limitations

1. No real provider: every extraction result is simulation and must not be read as science.
2. No accuracy evaluation: there is no gold dataset or benchmark in this iteration.
3. No RAG/retrieval: only `full_context`; an oversized document stops instead of being chunked.
4. No Evidence Resolver, `EvidenceRecord` or Coverage Trail.
5. `empty` fields stay empty; the platform never claims `not_found` from a simulated answer.
6. RunRepository is in-memory: after a restart a run is reachable by `run_id` (checkpoint +
   artefacts), but not by search or listing.
7. A technical resume cannot and must not approve a `needs_human_review` document (OI-007).
8. Tables remain at the iteration 2 quality level; their warnings are carried into the run.
