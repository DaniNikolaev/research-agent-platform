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

*Inventory corrected during the audit supplementation (2026-11-17); authoritative source:
`git diff --name-status 4d3332c ddc7c50` (“Iteration 2 technical closure” → “Iteration 3”),
43 paths changed: 30 added, 13 modified, 0 deleted, 0 renamed. The first version of this
report claimed 25/9 and listed `platform/observability/logging.py` as modified — it is not part
of the commit.*

**Created (30):** `src/research_platform/domain/extraction/__init__.py`,
`src/research_platform/domain/extraction/models.py`,
`src/research_platform/application/ports/context_budget.py`,
`src/research_platform/application/services/agent1a_extraction.py`,
`src/research_platform/application/services/extraction_errors.py`,
`src/research_platform/application/services/extraction_payloads.py`,
`src/research_platform/application/services/extraction_prompts.py`,
`src/research_platform/composition/agent1a_extraction.py`,
`src/research_platform/platform/models/fake_extraction.py`,
`src/research_platform/platform/models/token_counting.py`,
`src/research_platform/platform/checkpointing/sqlite.py`,
`src/research_platform/prompts/__init__.py`,
`src/research_platform/prompts/agent_1a/__init__.py`,
`src/research_platform/prompts/agent_1a/v1/__init__.py`,
`src/research_platform/prompts/agent_1a/v1/g1_bibliography.md`,
`src/research_platform/prompts/agent_1a/v1/g2_research_design.md`,
`src/research_platform/prompts/agent_1a/v1/g3_methods_data_variables.md`,
`src/research_platform/prompts/agent_1a/v1/g4_findings_implications.md`,
`src/research_platform/workflows/agent1a_extraction_state.py`,
`src/research_platform/workflows/agent1a_extraction_workflow.py`,
`tests/agent1a/__init__.py`, `tests/agent1a/conftest.py`,
`tests/agent1a/test_extraction_unit.py`, `tests/agent1a/test_agent1a_workflow.py`,
`tests/agent1a/test_agent1a_sqlite_resume.py`, `tests/agent1a/test_agent1a_cli.py`,
`tests/parsing/conftest.py`, `tests/parsing/test_agent1a_e2e_parsing.py`,
`docs/development/agent_1a_extraction.md`,
`docs/iteration_reports/iteration_3_report.md`.

**Modified (13):** `pyproject.toml` (checkpoint dependency), `uv.lock` (+37 lines: only
`langgraph-checkpoint-sqlite`, `aiosqlite`, `sqlite-vec`), `config/application.example.yaml`
(sqlite + `agent_1a` section), `config/models.example.yaml` (`fake-extraction` profile),
`src/research_platform/app.py` (SQLite provider, extraction pipeline in the context),
`src/research_platform/cli.py` (three commands, payload/exit mapping),
`src/research_platform/config.py` (`CheckpointerKind.SQLITE`, checkpoint path/timeout,
`Agent1ASettings`, profile validation),
`src/research_platform/application/ports/logging.py` (six event names),
`tests/unit/test_platform_foundation.py` (default checkpointer is sqlite),
`docs/development/testing.md`, `docs/development/limitations.md`,
`docs/architecture/architecture_overview.md`, `README.md`.

**Removed:** none. **Renamed:** none.

One file listed in the first version is in fact **not** modified:
`src/research_platform/platform/observability/logging.py` — the platform logger re-exports event
names, but the six new names were not added there (see §16.6, finding F6). Functionally the
events are valid because validation uses `EVENT_NAMES` from `application/ports/logging.py`.

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

---

## 16. Дополнение по результатам аудита отчёта Итерации 3

*Дата дополнения: 2026-11-17. Исходный код, конфигурация, тесты и контракты не изменялись.
Новые целевые запуски выполнены отдельно и датированы; результаты из §11 не переписаны.
Диагностические сценарии выполнялись скриптами вне репозитория
(`D:\MultiAgentPipeline\.tooling\it3_audit.py`, `it3_audit_logging.py`), которые используют
только публичный API платформы (`bootstrap_application`, composition, CLI).*

### 16.1. Проверка восстановления после перезапуска процесса

**Механизм.** Продолжение работы не читает process-local кэш исходного процесса: кэш живёт
только внутри одного вызова `Agent1AExtractionPipeline` и создаётся заново
(`_dependencies(ExtractionRuntimeCache())`). При восстановлении узел графа вызывает
`_hydrate_cache`, который по URI из состояния читает недостающие объекты из Artifact Store
через `_read_uri`; сам граф продолжается вызовом `invoke(None, config)`, то есть с последнего
сохранённого супер-шага, а не с `START`.

| Источник данных | Что именно берётся | Роль при cold resume |
|---|---|---|
| SQLite checkpoint (`SqliteSaver`, `thread_id = run_id`) | идентификаторы (`run_id`, `document_run_id`, `document_id`, `source_sha256`), `processing_status`, `group_statuses`, `completed_groups`, все URI, `last_completed_node`, `interrupt_reason` | определяет, **откуда** продолжать и **что** загрузить; содержит только компактное состояние |
| Artifact Store | `ParsedDocument` (JSON), канонический текст (`text/plain`), Quality Report, manifest исходного документа, extraction plan, результаты G1/G2 | единственный источник больших данных при восстановлении |
| `InMemoryRunRepository` | запись Run (статус, warnings, errors) | после рестарта пуст; запись **пере-материализуется** из checkpoint под тем же `run_id` (`_ensure_run`) — не является источником данных извлечения |
| `ExtractionRuntimeCache` | plan, parsed document, canonical text, quality report, результаты групп | в новом процессе **пуст** (проверено), заполняется из Artifact Store |

**Сценарий cold-process resume, фактические результаты** (документ: TXT, `source_sha256`
`8341f46b…3128`; проект `D:\MultiAgentPipeline\.tooling\it3-audit\resume`):

| Шаг | Процесс A (`resume-a`, pid 16600, exit 0) | Процесс B (`resume-b`, pid 19648, exit 0) |
|---|---|---|
| `document_run_id` | `run_20261117_195600_da5f04` | тот же (совпадает с A) |
| `run_id` | `run_20261117_195600_5c25bb` | тот же |
| `document_id` / `source_sha256` | `doc_4ccb24f1b1ff9f38965e81eb` / `8341f46b…3128` | не изменились |
| checkpoint `last_completed_node` | `validate_and_persist_g2_research_design` | `finalize_run` (после продолжения) |
| группы | G1/G2 `completed`, G3/G4 `pending` | все четыре `completed` |
| артефакты | 3: `art_extraction_plan…`, `art_g1_bibliography…`, `art_g2_research_design…` | 7: план, G1–G4, агрегат, manifest |
| manifest | отсутствует (`run_manifest_uri: null`) | `final_status=completed`, 4 group URI, `execution_mode=simulation`, `quality_status=passed` |
| Run status | `running` | `completed_with_warnings` |

Проверки, требуемые заданием:

* тот же `run_id` — да (`run_20261117_195600_5c25bb` в A, B и C);
* тот же `document_run_id` — да; `document_id` и `source_sha256` не изменились;
* восстановление состояния из SQLite — да: `checkpoint_last_node` до resume указывал на
  `validate_and_persist_g2_research_design`, продолжение началось с G3;
* исходные данные перечитаны из Artifact Store — да, в процессе B зафиксировано **8**
  чтений: `art_canonical_9f38965e81eb_da5f04`, `art_parsed_9f38965e81eb_da5f04`,
  `art_quality_9f38965e81eb_da5f04`, `art_manifest_9f38965e81eb_da5f04`,
  `art_extraction_plan_9f38965e81eb_5c25bb`, `art_g1_bibliography_9f38965e81eb_5c25bb`,
  `art_g2_research_design_9f38965e81eb_5c25bb`, `art_extraction_manifest_9f38965e81eb_5c25bb`;
* прежний process-local cache не использовался — да: `fresh_cache_is_empty_before_resume=true`
  (новый экземпляр `ExtractionRuntimeCache` пуст), а все данные пришли чтениями из Store;
* G1/G2 не выполнялись повторно — да: до и после resume ровно по одному артефакту
  `art_g1_bibliography…` и `art_g2_research_design…` (счётчики 1 → 1);
* результаты G1/G2 не перезаписывались — да: идентификаторы и SHA-256 прежние
  (`5aca34ce86f88533`, `df32adffd671061a`), Artifact Store отвергает повторную публикацию;
* G3/G4 выполнены — да, появились `art_g3_methods_data_variables…` и
  `art_g4_findings_implications…`;
* итоговый манифест доступен — да (`art_extraction_manifest_9f38965e81eb_5c25bb`,
  `final_status=completed`).

Ограничение доказательства: сценарий подтверждает независимость от process-local кэша
(другой PID, пустой кэш, 8 чтений из Store), но не доказывает долговременную персистентность
`RunRepository` — она по-прежнему in-memory (OI-006), и запись Run восстанавливается из
checkpoint, а не из репозитория.

### 16.2. Разделение сценариев восстановления Run

**Сценарий A — восстановление завершённого Run.** Процесс C (pid 14884, exit 0), новый
`bootstrap_application` на том же проекте:

* checkpoint: `processing_status=completed`, `last_completed_node=finalize_run`;
* `resume` вернул `paused=false` и **не выполнял** узлов: число артефактов 7 → 7,
  `artifacts_unchanged=true`;
* manifest читается: `final_status=completed`, 4 group URI;
* `run_status=null`: RunRecord не пере-материализуется, когда работы не осталось (запись Run
  может отсутствовать после рестарта; терминальный статус берётся из manifest/checkpoint).

**Сценарий B — продолжение незавершённого Run.** Процессы A → B (таблица §16.1): граф
продолжил с G3 и завершил G3/G4, не повторяя G1/G2.

| Параметр | Сценарий A: completed Run | Сценарий B: interrupted Run |
|---|---|---|
| Исходное состояние | `completed` | после G2 (`pending` G3/G4) |
| Исходный процесс завершён | Да (A завершён) | Да (A завершён) |
| Новый Application | Да (процесс C) | Да (процесс B) |
| Новый OS process | Да (pid 14884 ≠ 16600) | Да (pid 19648 ≠ 16600) |
| SQLite checkpoint | Читается | Восстанавливается |
| Artifact Store | Чтение манифеста и результатов | Чтение входных данных, плана и G1/G2 |
| G1/G2 | Не выполняются | Не выполняются повторно |
| G3/G4 | Не выполняются | Выполняются |
| Итоговый статус | Сохраняется (`completed`) | `completed` после продолжения |

### 16.3. Проверка Manifest и диагностики для незавершённых Run

Фактические наблюдения (диагностика `it3_audit.py evidence`, exit 0):

| Сценарий | Run status | Checkpoint | Артефакты | Причина доступна | Model Gateway вызван |
|---|---|---|---|---|---|
| Success | `completed_with_warnings` | конечное состояние, `finalize_run` | plan, G1–G4, aggregate, manifest | да (manifest, warnings) | да |
| `requires_ocr` | `waiting_for_human` | `processing_status=requires_ocr`, `last_completed_node=controlled_stop`, `interrupt_reason="requires_ocr: requires_ocr"` | **нет ни одного** | да, в checkpoint и RunRecord; лог — только при `observability.enabled=true` | нет |
| `needs_human_review` | `waiting_for_human` | состояние на `check_quality_gate`; причина только в interrupt-payload (`stop_reason`), `interrupt_reason` в values = `null` | **нет** | да, через interrupt payload; после рестарта — только при повторном interrupt | нет |
| Model/schema failure | `failed` | `planned`, `last_completed_node=build_extraction_plan`, группы `pending` | только plan | да, RunError + (при включённом логе) `stage_failed`/`run_failed` | да |
| Artifact Store failure | `failed` | `planned`, `last_completed_node=prepare_full_context` | нет (хранилище недоступно) | да, RunError `extraction_storage_error` + исключение | нет (сбой до первого вызова модели) |
| Checkpointer failure | Run **не создан** | отсутствует | нет | да, `FileExistsError` вызывающему; типизированного кода нет | нет |

**Выводы по сценариям.**

1. Успешный Run: manifest публикуется; план, G1–G4, агрегат, URI и SHA-256 присутствуют
   (`test_full_extraction_completes_and_publishes_artifacts`).
2. `requires_ocr`: причина и статус сохраняются в checkpoint и RunRecord; **отдельного
   manifest/диагностического артефакта не публикуется** — это несоответствие ожиданиям
   §16.2 ТЗ Итерации 3 и разделу 9 первоначального отчёта (там «manifest и diagnostics»
   упомянуты общо). Зафиксировано как дефект D1 (не исправлялся).
3. `needs_human_review`: причина живёт в interrupt-payload; в значениях чекпоинта
   `interrupt_reason=null`, поэтому после cold restart `report` не покажет причину, а
   `resume` повторно входит в узел и снова приостанавливается (безопасно, но причина не
   персистентна в state) — дефект D2.
4. Ошибка модели/схемы: категория сохраняется только текстом сообщения; код RunError —
   общий `extraction_error`, а не `auth_error`/`structured_output_invalid` (D3).
5. **Дублирование RunError**: каждая ошибка записывается дважды (`record_error` + `fail_run`
   оба добавляют запись) — в диагностике `run_errors_count=2` с одинаковыми сообщениями (D4).
6. Ошибка Artifact Store: последний доступный checkpoint сохраняется, публикация не
   притворяется успешной, ошибка типизирована (`extraction_storage_error`), но диагностика
   доступна только через RunError/исключение/лог (D1-смежное: манифеста нет, и это корректно,
   поскольку хранилище недоступно).
7. Ошибка Checkpointer: Run не создаётся, ложного успеха нет; но исключение — `OSError`
   (`FileExistsError`), а не типизированная ошибка категории `checkpoint` (D5).
8. **Независимый канал диагностики**: `observability.enabled=false` по умолчанию, поэтому
   JSONL-файлов нет; при `enabled: true` события эмитируются (`run_waiting_for_human` с
   `reason="requires_ocr: requires_ocr"`, `stage_failed`, `run_failed`), но
   `group_failed`/`retry_started` при постоянной ошибке модели не пишутся, а объявленное
   событие `checkpoint_saved` нигде не эмитируется (D6).

### 16.4. Матрица прослеживаемости требований ТЗ Итерации 3

Команды: `uv run --group parsing pytest <файл>::<тест> -q`; фактическая дата запусков —
2026-11-17 23:01 (T1–T4) и предыдущий полный прогон 22:26 (723 passed, exit 0).

| ID | Требование ТЗ | Test file / test name | Команда | Фактический результат | Статус |
|---|---|---|---|---|---|
| ITER3-01 | Архитектурные границы, слои | `tests/unit/test_import_boundaries.py` (5 тестов) | `pytest tests/unit/test_import_boundaries.py -q` | 5 passed (в полном прогоне 22:26) | PASS |
| ITER3-02 | Типизированный GraphState | `test_extraction_unit.py::test_initial_state_is_complete_and_valid`, `::test_unknown_processing_status_is_rejected[nonsense]`, `::test_state_rejects_a_broken_artifact_uri`, `::test_state_rejects_an_unknown_group` | `pytest tests/agent1a/test_extraction_unit.py -q` | 27 passed, exit 0 | PASS |
| ITER3-03 | Нет больших объектов в State | `::test_state_contains_no_document_payload`, `test_agent1a_workflow.py::test_state_stays_compact_and_holds_no_document_text` | то же | < 4 КБ, нет `elements`/`canonical_text` | PASS |
| ITER3-04 | Последовательные G1–G4 | `test_agent1a_workflow.py::test_full_extraction_completes_and_publishes_artifacts`, `::test_prompts_used_by_the_run_are_the_versioned_ones` | `pytest tests/agent1a/test_agent1a_workflow.py -q` | 18 passed, `completed_groups` в порядке G1→G4 | PASS |
| ITER3-05 | Quality Gate до Model Gateway | `::test_scan_requires_ocr_and_never_calls_the_model`, `::test_needs_human_review_interrupts_the_graph`, `::test_failed_quality_marks_the_run_failed`, `::test_passed_with_warnings_is_allowed_and_keeps_the_warnings` | то же | spy-проверки: `calls == []` для трёх блокирующих статусов | PASS |
| ITER3-06 | Structured output + Pydantic | `test_extraction_unit.py::test_group_result_rejects_a_field_of_another_group`, `::test_payload_rejects_unknown_field_and_duplicates`, `::test_empty_candidate_is_not_a_found_value`; `test_agent1a_workflow.py::test_invalid_structured_output_is_repaired` | оба файла | 27 + 18 passed | PASS |
| ITER3-07 | 8 сценариев Fake Adapter | `test_extraction_unit.py::test_fake_success_returns_every_field_and_candidate`, `::test_fake_empty_result_has_no_candidates`, `::test_fake_invalid_schema_once_then_valid`, `::test_fake_invalid_schema_always_stays_invalid`, `::test_fake_transient_error_once_then_success`, `::test_fake_transient_error_always`, `::test_fake_permanent_error_is_not_retryable`, `::test_every_scenario_name_is_supported` | `pytest tests/agent1a/test_extraction_unit.py -q` | 27 passed | PASS (`interruption_scenario` = success + LangGraph breakpoint, см. §16.6) |
| ITER3-08 | Context budget / overflow | `test_agent1a_workflow.py::test_context_overflow_is_controlled` | `pytest tests/agent1a/test_agent1a_workflow.py -q` | 18 passed; проверено превышение при малом окне | PARTIAL (проверка «текст не усечён» на реалистичном окне отдельным assertion не покрыта) |
| ITER3-09 | Retry transient | `::test_transient_error_once_is_retried`, `test_extraction_unit.py::test_fake_transient_error_always` | оба файла | счётчик попыток ≥ 1, 3 попытки при always | PASS |
| ITER3-10 | Targeted repair | `::test_invalid_structured_output_is_repaired`, `::test_always_invalid_output_never_reports_success` | `pytest tests/agent1a/test_agent1a_workflow.py -q` | `retry_counters=g1:1`; при always — `failed`, нет `completed` | PASS |
| ITER3-11 | Interrupt и controlled stop | `::test_needs_human_review_interrupts_the_graph`, `::test_scan_requires_ocr_and_never_calls_the_model`, `test_agent1a_cli.py::test_extract_stops_on_requires_ocr`, `::test_resume_does_not_confirm_a_human_review` | `pytest tests/agent1a -q` | 63 passed, exit 0 | PASS |
| ITER3-12 | SQLite checkpointing | `test_agent1a_sqlite_resume.py::test_provider_creates_a_database_and_binds_thread_to_run`, `::test_checkpoints_are_written_to_disk`, `::test_two_runs_keep_independent_checkpoints`, `::test_provider_closes_its_connections` | `pytest tests/agent1a/test_agent1a_sqlite_resume.py -q` | 10 passed, exit 0 (23:01) | PASS |
| ITER3-13 | Восстановление завершённого Run | `::test_state_survives_a_new_application_instance` + диагностика «процесс C» | `pytest ...::test_state_survives_a_new_application_instance -q` | passed; C: 7→7 артефактов, manifest `completed` | PASS |
| ITER3-14 | Продолжение прерванного Run (cold) | `::test_resume_works_in_a_separate_process`, `::test_resume_continues_without_repeating_finished_groups` + диагностика «A→B» | `pytest ...::test_resume_works_in_a_separate_process -q` | passed; B: G1/G2 1→1, G3/G4 выполнены | PASS |
| ITER3-15 | Публикация G1–G4 | `test_agent1a_workflow.py::test_full_extraction_completes_and_publishes_artifacts`, `::test_group_results_are_typed_and_marked_as_simulation` | `pytest tests/agent1a/test_agent1a_workflow.py -q` | 5 `extraction_candidates`, `execution_mode=simulation` | PASS |
| ITER3-16 | Manifest и диагностика | `::test_full_extraction_completes_and_publishes_artifacts` (manifest), `::test_passed_with_warnings_is_allowed_and_keeps_the_warnings` | то же | manifest для успешных Run; для controlled stop/failure манифеста нет | PARTIAL (D1/D2) |
| ITER3-17 | Идемпотентность, нет overwrite | `::test_second_run_does_not_overwrite_the_first`, `test_agent1a_sqlite_resume.py::test_resume_continues_without_repeating_finished_groups` | оба файла | disjoint ids; счётчики групп 1 | PASS |
| ITER3-18 | CLI extract/report/resume | 8 тестов `tests/agent1a/test_agent1a_cli.py` | `pytest tests/agent1a/test_agent1a_cli.py -q` | 8 passed, коды 0/1/2/4 | PASS |
| ITER3-19 | End-to-end PDF | `tests/parsing/test_agent1a_e2e_parsing.py::test_scenario_a_digital_pdf`, `::test_multipage_pdf_extraction_keeps_pagination_out_of_the_state` | `pytest tests/parsing/test_agent1a_e2e_parsing.py -q` | 4 passed, exit 0 (23:01), реальный Docling | PASS |
| ITER3-20 | End-to-end TXT | `::test_txt_document_run_is_extractable_end_to_end`, `test_agent1a_workflow.py::test_txt_document_keeps_no_invented_pagination` | оба | passed; `pages=[]`, `page_count=null` | PASS |
| ITER3-21 | End-to-end скан, модель не вызывается | `::test_scenario_c_scanned_pdf_is_never_extracted`, `test_agent1a_workflow.py::test_scan_requires_ocr_and_never_calls_the_model` | оба | `requires_ocr`, `completed_groups=[]`, нет `extraction_candidates` | PASS |
| ITER3-22 | Неизменность контрактов | `tests/contract/test_frozen_schemas.py` + `tools/export_schemas.py --check` | `pytest tests/contract/test_frozen_schemas.py -q`; `python tools/export_schemas.py --check` | 10 схем актуальны, exit 0; хэши `caa0b289…982c`, `2a6deb80…ec54` не изменились | PASS |
| ITER3-23 | Регрессия Итераций 0–2 | полный набор | `uv run --group parsing pytest -q` | 723 passed, 0 failed, 0 skipped (22:26) | PASS |

### 16.5. Исправленная инвентаризация файлов

Источник: `git diff --name-status 4d3332c ddc7c50` (коммиты «Iteration 2 technical closure» →
«Iteration 3»). Рабочее дерево на момент аудита чистое (`git status --porcelain -uall` — пусто),
поэтому инвентаризация взята из коммита.

| Категория | Фактически | В первой версии отчёта |
|---|---|---|
| Создано (A) | **30** | 25 (занижено) |
| Изменено (M) | **13** | 9 (занижено и содержало лишний файл) |
| Удалено (D) | **0** | 0 |
| Переименовано (R) | **0** | — |
| **Всего** | **43** | 34 |

Полный перечень — в исправленном §12. Расхождение объясняется тем, что первая версия
перечисляла файлы группами (например, «`prompts/…` + `v1/*.md` (7 файлов)»), из-за чего
визуальный список не совпадал с числом, а `platform/observability/logging.py` был указан как
изменённый, хотя в коммит не входит.

### 16.6. Выявленные ограничения и неподтверждённые требования

| ID | Требование | Факт | Влияние | Минимальная корректировка (отдельной задачей) |
|---|---|---|---|---|
| D1 | §16.2 ТЗ 3: диагностические артефакты для остановленных Run | для `requires_ocr`/`needs_human_review`/`failed` манифест и диагностический артефакт **не публикуются**; причина — только в checkpoint/RunRecord/логе | PARTIAL по критерию «Manifest/diagnostics»; после рестарта RunRecord теряется, остаются checkpoint и (при включённом логе) JSONL | публиковать `run_log`-артефакт с причиной остановки и статусом в `controlled_stop`/`interrupt_for_review`/`_record_failure` |
| D2 | Причина interrupt в состоянии | `needs_human_review`: `interrupt_reason` в values = `null`, причина в interrupt-payload | после cold restart `report` не показывает причину; resume повторно приостанавливается (безопасно) | писать `interrupt_reason` в состояние до вызова `interrupt()` |
| D3 | §14.1 ТЗ 3: категория ошибки | RunError.code для ошибок модели — общий `extraction_error`; категория только в тексте | диагностируемость ниже ожидаемой | маппинг `ErrorCategory` → отдельные коды RunError |
| D4 | Корректность регистрации ошибки | каждая ошибка записывается **дважды** (`record_error` + `fail_run`) | шум в отчётах, двойной счётчик ошибок | убрать один из вызовов либо сделать `record_error` идемпотентным |
| D5 | §14.1: типизация ошибки checkpoint | сбой Checkpointer — `FileExistsError`/`OSError`, не тип `checkpoint` | CLI возвращает 3 (инфраструктура), но без кода категории | обернуть создание saver в `ExtractionError(category=CHECKPOINT)` |
| D6 | §18 ТЗ 3: события checkpoint/resume и диагностика групп | `checkpoint_saved` объявлено, но не эмитируется; при постоянной ошибке модели нет `group_failed`/`retry_started`; `context_tokens` редактируется redactor-ом в `***REDACTED***` | неполная трассировка; включённая observability не даёт полной картины | эмитировать событие сохранения checkpoint; не считать `context_tokens` секретом |
| D7 | Полнота экспорта событий | `platform/observability/logging.py` не ре-экспортирует 6 новых имён (валидация идёт по `EVENT_NAMES` из ports) | не влияет на работу; неполный публичный ре-экспорт | добавить имена в ре-экспорт |

Ни один из пунктов D1–D7 не является блокирующим для технической приёмки: извлечение,
маршрутизация, публикация результатов, checkpoint и resume работают и проверены. D1 и D2
затрагивают полноту диагностики контролируемых остановок, D3–D7 — качество диагностики и
типизации. Все они требуют отдельного согласованного исправления; в рамках аудита код не
менялся.

**Явно неподтверждённые/частичные требования:**

* «Manifest и диагностические сведения сохраняются для завершённых, неуспешных и
  контролируемо остановленных Run» — верно только для успешных Run (PARTIAL, D1/D2).
* «Context overflow без усечения» — поведение реализовано (исключение до вызова модели),
  но тест проверяет срабатывание при заведомо малом окне, а не отсутствие усечения на
  реалистичном документе (PARTIAL).
* «События checkpoint/resume» — `workflow_resumed` эмитируется, `checkpoint_saved` — нет
  (PARTIAL, D6).
* Долговременная персистентность `RunRepository` — не реализована и не заявлялась (OI-006).

### 16.7. Итоговое заключение дополнения

1. Механизм cold-process resume подтверждён фактами: другой процесс, пустой process-local
   кэш, восстановление состояния из SQLite, 8 чтений из Artifact Store, G1/G2 без повторного
   выполнения и без перезаписи, G3/G4 выполнены, итоговый manifest `completed`.
2. Восстановление завершённого Run и продолжение незавершённого Run — два разных проверенных
   сценария (§16.2), оба после полного завершения исходного процесса.
3. Для успешных Run manifest и артефакты публикуются полностью. Для `requires_ocr`,
   `needs_human_review` и ошибок манифест не публикуется: диагностика ограничена checkpoint,
   RunRecord (in-memory) и JSONL-логом (выключен по умолчанию) — зафиксировано как D1/D2.
4. Дополнительно выявлены дефекты диагностики D3–D7 (коды ошибок, дублирование RunError,
   типизация ошибки checkpoint, неполные события, ре-экспорт).
5. Инвентаризация файлов исправлена: 30 создано, 13 изменено, 0 удалено, 0 переименовано.
6. **Итоговый статус Итерации 3 остаётся `PASS WITH CONDITIONS`.** Условия уточнены:
   C1 — реальный провайдер и оценка точности (OI-001…004, Evaluation); C2 — OCR (OI-008);
   C3 — качество таблиц на реальных публикациях; C4 — публикация диагностического артефакта
   для остановленных Run (D1) и персистентность причины interrupt (D2); C5 — типизация и
   однократность регистрации ошибок (D3–D6). Повышение статуса до `PASS` не обосновано.

---

## 17. Technical Hardening — closure of D1–D7 and A1–A2

*Дата выполнения: 2026-11-18; окружение: Windows 10 Pro 22H2, CPython 3.13.18, uv 0.13.7,
docling 2.150.0, pymupdf 2.1, pydantic 2.14.3, langgraph 1.2.20,
langgraph-checkpoint-sqlite 3.1.3. Архитектура, публичные контракты и замороженные схемы не
изменялись; новых зависимостей нет.*

### 17.1. Summary

| Пункт | Статус |
|---|---|
| Задание технического упрочнения (D1–D7, A1–A2) | **PASS** |
| D1 — diagnostic Run Manifest | закрыто |
| D2 — персистентный `interrupt_reason` | закрыто |
| D3 — конкретные коды `RunError` | закрыто |
| D4 — однократная регистрация ошибки | закрыто |
| D5 — типизированная ошибка Checkpointing | закрыто |
| D6 — события и redaction | закрыто (7 событий: 6 прежних + `run_manifest_publish_failed` для D1) |
| D7 — реэкспорт event names | закрыто |
| A1 — отсутствие silent truncation на реалистичном бюджете | закрыто тестами |
| A2 — хронология чтения `art_extraction_manifest` | закрыто, уточнено |
| Итоговый статус Итерации 3 | остаётся **PASS WITH CONDITIONS** |

Открытые ограничения: реальный LLM Provider и оценка точности (OI-001…004, Evaluation), OCR
(OI-008), качество таблиц на корпусе реальных публикаций, долговременный RunRepository
(OI-006), HITL-канал (OI-007). Дополнительно выявлен и **не исправлялся** дефект D8
(см. §17.3.4): опубликованный контракт `Message.content` ограничен 8192 символами, поэтому
«реалистичный» full_context фактически ограничен этим значением, а не token budget.

### 17.2. D1–D7 closure

| ID | Исходная проблема | Изменённые компоненты | Фактическое исправление | Тесты | Команда / результат |
|---|---|---|---|---|---|
| D1 | Для `requires_ocr`, `needs_human_review`, `failed` Run Manifest не публиковался; диагностика жила только в checkpoint, in-memory RunRecord и выключенном по умолчанию JSONL | `workflows/agent1a_extraction_workflow.py` (`_build_manifest`, `_publish_terminal_manifest`, `_controlled_stop`, `_prepare_human_review_stop`, `_fail_run`, `_record_failure`), `domain/extraction/models.py` (`interrupt_reason`), `cli.py` (явное предупреждение, stop reason в report) | Терминальные пути публикуют существующий `ExtractionRunManifest` (тип `run_log`, тот же URI-namespace) с `final_status`, `interrupt_reason`, `errors`, статусами и URI групп; публикация идемпотентна (повторный вход возвращает уже опубликованный URI; конфликт содержимого — контролируемая ошибка Store); при недоступном Store манифест не имитируется, а в состояние/исключение попадает причина, и CLI печатает «the run manifest was not published …» | `test_hardening.py::test_requires_ocr_publishes_diagnostic_manifest`, `::test_needs_human_review_publishes_manifest_and_persisted_reason`, `::test_terminal_failure_publishes_manifest_with_category_code`, `::test_failure_after_g1_keeps_completed_group_in_manifest`, `::test_terminal_handler_is_idempotent`, `::test_manifest_publication_failure_is_reported_not_faked`, `::test_report_cli_reads_manifest_after_restart` | `pytest tests/agent1a/test_hardening.py -q` → 33 passed, exit 0 |
| D2 | `interrupt_reason` не попадал в сохранённый GraphState при `needs_human_review` | `workflows/...` (новый узел `prepare_human_review_stop` перед `interrupt_for_review`) | Причина, `processing_status=needs_human_review` и манифест сохраняются **до** LangGraph `interrupt()`; узел interrupt только паркует граф. После cold restart `state_for_run` возвращает ту же причину, `resume` не подтверждает review | `::test_needs_human_review_publishes_manifest_and_persisted_reason` (checkpoint + новый `bootstrap_application`), `::test_resume_does_not_confirm_human_review` | то же, 33 passed |
| D3 | `RunError.code` был общим `extraction_error`, категория — только в тексте; для permanent model error терялась категория | `application/services/extraction_errors.py` (`RUN_ERROR_CODES` 1:1, `code` как свойство категории, классы `ModelTransientError`/`ModelPermanentError`/`ExtractionCheckpointError`/`ExtractionContractError`), `application/services/agent1a_extraction.py` (типизированный raise) | Каждая `ErrorCategory` отображается в один стабильный код по таблице ТЗ; `to_run_error()` берёт код из маппинга; controlled stop (`requires_ocr`, `needs_human_review`) RunError не создаёт | `::test_every_category_maps_to_a_specific_code` (12 параметров), `::test_terminal_failure_publishes_manifest_with_category_code` | то же, 33 passed |
| D4 | Ошибка регистрировалась дважды (`record_error` + `fail_run`) | `workflows/...` (`_record_failure`: только `fail_run`; защита от повторного перехода при уже `failed`) | Один логический сбой → ровно одна запись `RunError`; успешный retry не оставляет терминальной записи; повторный вход terminal handler не дублирует | `::test_permanent_error_registers_one_run_error`, `::test_invalid_schema_always_registers_one_run_error`, `::test_successful_retry_leaves_no_terminal_error` | то же, 33 passed |
| D5 | Сбой SQLite Checkpointer выходил как `OSError`/`FileExistsError` | `workflows/...` (`_checkpoint`), `composition/agent1a_extraction.py` (обёртка `create_saver`), `platform/checkpointing/sqlite.py` | Ожидаемые ошибки адаптера (`OSError`, `sqlite3.Error`) переводятся в `ExtractionCheckpointError` (категория `checkpoint`, код `checkpoint_error`) с сохранением `__cause__`; Run не создаётся, ложного успеха нет; CLI возвращает 3 | `::test_checkpoint_failure_is_typed_and_exits_three`, `::test_checkpoint_error_keeps_the_original_cause` | то же, 33 passed |
| D6 | `retry_started` без полей, `group_failed` только для structured output, `checkpoint_saved` не эмитировался, `context_tokens` редактировался | `application/services/agent1a_extraction.py` (события retry/group с `retry_type`, `max_attempts`, `error_category`, `error_code`, `attempt_count`), `platform/checkpointing/observed.py` (новый `ObservedSqliteSaver` — подкласс официального SqliteSaver, эмиссия **после** успешного `super().put`), `sqlite.py` + `app.py` (logger в провайдер), `application/services/redaction.py` (безопасные числовые ключи) | События отражают реальные попытки и терминальные отказы; `checkpoint_saved` означает подтверждённую запись адаптера, при ошибке не эмитируется; `context_tokens` сохраняется числом, секреты и текстовые поля по-прежнему редактируются | `::test_retry_and_group_events_are_emitted`, `::test_terminal_group_failure_emits_group_failed`, `::test_context_tokens_is_not_redacted`, `::test_redaction_still_masks_secrets` | то же, 33 passed |
| D7 | 6 event names не реэкспортировались через platform-модуль | `platform/observability/logging.py` | Все имена импортированы из единственного источника (`application/ports/logging.py`), добавлены в `__all__`; значения идентичны, `EVENT_NAMES` остаётся реестром | `::test_platform_reexports_the_new_event_names` | то же, 33 passed |

Публикация `run_manifest_publish_failed` (событие, добавленное для D1) также зарегистрирована в
реестре и реэкспортирована — это не «вторая система событий», а одно имя в существующем
реестре.

### 17.3. A1 — Context overflow и отсутствие silent truncation

**Профиль и счётчик.** `agent_1a.model_profile=fake-extraction`: `max_context_tokens=16000`,
`max_output_tokens=2048`; резервы `context_output_reserve_tokens=2048`,
`context_safety_reserve_tokens=512` → входной бюджет **13440** условных токенов.
Счётчик — `HeuristicTokenCounter` (`heuristic-chars-per-4`, `is_exact=False`), то есть
**оценка, а не точный tokenizer модели**.

| Сценарий | Текст | Оценка счётчика | Бюджет | Model Gateway | Проверки | Результат |
|---|---|---|---|---|---|---|
| A: внутри лимита | 6 000 символов детерминированного текста | 1 500 токенов + prompt ≈ 2 100 | 13 440 | 4 вызова (по группам) | полный текст присутствует в каждом запросе; SHA-256 и длина совпадают; префикс — только «Document text …» | PASS |
| B: выше лимита | ≈ 53,8 тыс. символов (бюджет − prompt + 50 токенов) | ≈ 13 490 | 13 440 | **0 вызовов** | `extraction_context_overflow`, сообщение содержит «not truncated» и бюджет; canonical-артефакт не изменён (SHA-256 совпадает); частичный текст не отправлен | PASS |
| C: граница | ровно `(budget − prompt) × 4` символов и +1 токен | ровно 13 440 / 13 441 | 13 440 | — | на границе `fits=True`, на +1 токене — исключение; `is_exact=False`; `max_context_tokens > budget` | PASS |

Тесты: `test_hardening.py::test_full_context_is_passed_untruncated`,
`::test_over_budget_context_is_controlled_and_never_sent`,
`::test_context_boundary_matches_the_configured_rule`; запуск
`uv run --group parsing pytest tests/agent1a/test_hardening.py -q` → 33 passed, exit 0.

**17.3.4. Выявленный дефект D8 (не исправлялся).** Опубликованный контракт Model Gateway
ограничивает `Message.content` **8192 символами**. Следствия:

* full_context реально ограничен ~8 тыс. символов, а не бюджетом 13 440 токенов;
* документ длиннее 8192 символов, но в пределах бюджета (проверено на 20 000 символов)
  проходит `check_context`, а затем падает на валидации `ModelRequest` сырым
  `pydantic.ValidationError` без `ErrorCategory` (CLI → 3);
* это не silent truncation (текст не обрезается и не отправляется), но и не контролируемый
  `extraction_context_overflow`.

Причина — замороженный публичный контракт, менять который в этом задании запрещено.
Минимальное корректирующее решение (отдельной задачей): в `ExtractionService` считать
превышением контрактного лимита сообщения и поднимать `ExtractionContextOverflow` до вызова
gateway (без изменения контракта). Для полноценной работы с документами больше ~8 КБ нужен
retrieval/chunking Итерации 4.

### 17.4. A2 — Artifact Store chronology (cold-process resume)

Диагностика `D:\MultiAgentPipeline\.tooling\it3_a2.py` (вне репозитория, только публичный API),
два процесса, ISO-8601 с offset `+03:00`:

**Процесс A (pid 2164), run `run_20261118_191309_230bce`:**

| Время | PID | Фаза | Операция | Артефакт |
|---|---|---|---|---|
| 2026-11-18T22:13:09.543+03:00 | 2164 | A (load) | read | `art_manifest_…_14f27d` |
| 2026-11-18T22:13:09.549+03:00 | 2164 | A (load) | read | `art_parsed_…_14f27d` |
| 2026-11-18T22:13:09.558+03:00 | 2164 | A (load) | read | `art_canonical_…_14f27d` |
| 2026-11-18T22:13:09.559+03:00 | 2164 | A (load) | read | `art_quality_…_14f27d` |
| 2026-11-18T22:13:09.707+03:00 | 2164 | A (G1–G2) | publish | `art_extraction_plan_…_230bce` |
| 2026-11-18T22:13:09.854+03:00 | 2164 | A (G1–G2) | publish | `art_g1_bibliography_…_230bce` |
| 2026-11-18T22:13:09.979+03:00 | 2164 | A (G1–G2) | publish | `art_g2_research_design_…_230bce` |

Манифеста извлечения в процессе A нет (`run_manifest_uri=null`), checkpoint —
`validate_and_persist_g2_research_design`.

**Процесс B (pid 18376):**

| Время | PID | Фаза | Операция | Артефакт |
|---|---|---|---|---|
| 2026-11-18T22:13:12.133+03:00 | 18376 | B2 hydrate/resume | read | `art_manifest_…_14f27d` |
| 2026-11-18T22:13:12.136+03:00 | 18376 | B2 hydrate/resume | read | `art_parsed_…_14f27d` |
| 2026-11-18T22:13:12.138+03:00 | 18376 | B2 hydrate/resume | read | `art_canonical_…_14f27d` |
| 2026-11-18T22:13:12.140+03:00 | 18376 | B2 hydrate/resume | read | `art_quality_…_14f27d` |
| 2026-11-18T22:13:12.142+03:00 | 18376 | B2 hydrate/resume | read | `art_extraction_plan_…_230bce` |
| 2026-11-18T22:13:12.152+03:00 | 18376 | B2 hydrate/resume | read | `art_g1_bibliography_…_230bce` |
| 2026-11-18T22:13:12.161+03:00 | 18376 | B2 hydrate/resume | read | `art_g2_research_design_…_230bce` |
| 2026-11-18T22:13:12.331+03:00 | 18376 | B3 extract G3/G4 | publish | `art_g3_methods_data_variables_…_230bce` |
| 2026-11-18T22:13:12.466+03:00 | 18376 | B3 extract G3/G4 | publish | `art_g4_findings_implications_…_230bce` |
| 2026-11-18T22:13:12.573+03:00 | 18376 | B3 extract G3/G4 | publish | `art_extraction_result_…_230bce` |
| 2026-11-18T22:13:12.675+03:00 | 18376 | B4 publish manifest | publish | `art_extraction_manifest_…_230bce` |
| 2026-11-18T22:13:12.761+03:00 | 18376 | B5 report read | read | `art_extraction_manifest_…_230bce` |

**Однозначный ответ A2.** `art_extraction_manifest` опубликован **процессом B**
(pid 18376) в 22:13:12.675, впервые прочитан в 22:13:12.761 — **после** завершения resume
(фаза B5, проверка отчёта). К восстановлению относятся **7 чтений** фазы B2 (входные
артефакты документа, план и результаты G1/G2); чтение манифеста в число восстановительных не
входит. Прежняя формулировка §16.1 («восемь чтений, включая `art_extraction_manifest`»)
уточняется настоящим разделом: восьмое чтение — фаза B5. Время resume — 0,653 с.

### 17.5. Изменённые файлы

**Создано (2):** `src/research_platform/platform/checkpointing/observed.py`
(`ObservedSqliteSaver` для `checkpoint_saved`), `tests/agent1a/test_hardening.py` (33 теста).

**Изменено (13):** `src/research_platform/application/services/extraction_errors.py`
(маппинг кодов, типизированные ошибки, поля публикации), `.../agent1a_extraction.py`
(retry/group события, типизированный raise), `.../redaction.py` (безопасные числовые метрики),
`src/research_platform/workflows/agent1a_extraction_workflow.py` (D1/D2/D4/D5),
`src/research_platform/composition/agent1a_extraction.py` (обёртка checkpoint-ошибок),
`src/research_platform/platform/checkpointing/sqlite.py` (logger и observed saver),
`src/research_platform/platform/observability/logging.py` (реэкспорт D7),
`src/research_platform/platform/observability/redaction.py` (реэкспорт),
`src/research_platform/application/ports/logging.py` (`run_manifest_publish_failed`),
`src/research_platform/domain/extraction/models.py` (`interrupt_reason` в манифесте),
`src/research_platform/app.py` (logger в провайдер), `src/research_platform/cli.py`
(предупреждение о неопубликованном манифесте, stop reason в report),
`tests/agent1a/conftest.py` (observability-настройка тестового проекта).

**Документация:** `docs/iteration_reports/iteration_3_report.md` (разделы 16–17),
`docs/development/agent_1a_extraction.md`, `docs/development/testing.md`,
`docs/development/limitations.md`.

**Удалено:** нет. Диагностические скрипты A1/A2 лежат вне репозитория и в инвентаризацию не
входят.

### 17.6. Тестирование

| Проверка | Команда | Результат |
|---|---|---|
| Новые тесты упрочнения | `uv run --group parsing pytest tests/agent1a/test_hardening.py -q` | 33 passed, exit 0 |
| Весь набор Agent 1A | `uv run --group parsing pytest tests/agent1a -q` | 96 passed, exit 0 |
| Полный набор | `uv run --group parsing pytest -q` | **756 passed, 0 failed, 0 skipped**, exit 0 (2026-11-18T22:13:57+03:00, 166 с) |
| Регрессия Итераций 0–2 | тот же полный прогон | 723 прежних теста сохранены без ослаблений |
| `uv lock --check` | — | exit 0 (lock не изменялся) |
| `ruff check .` | — | exit 0 |
| `ruff format --check .` | — | exit 0 (231 файл) |
| `ty check` | — | exit 0, 0 диагностик |
| `export_schemas.py --check` | — | exit 0, 10 схем без изменений |
| `config validate` / `platform check` | — | exit 0 / exit 0 (PASS WITH WARNINGS) |
| `agent1a workflow-smoke` | — | exit 0 |
| CLI extract → report (новый процесс) | `research-platform --project-root <tmp> agent1a extract/report` | exit 0, `final status: completed`, `errors: 0`, один манифест |
| Cold-process resume | `tests/agent1a/test_agent1a_sqlite_resume.py::test_resume_works_in_a_separate_process` + диагностика A2 | exit 0; G1/G2 не повторяются, 7 восстановительных чтений |

### 17.7. Открытые вопросы

Настоящее задание **не закрывает**: OI-001…OI-004 (реальный провайдер и его capability-тесты),
OI-006 (долговременный RunRepository и перечисление запусков), OI-007 (HITL-канал и
подтверждение ревью), OI-008 (OCR), а также качество таблиц на реальных публикациях и
научную точность извлечения (gold dataset, Evaluation). Fake Model Adapter не подтверждает и
не может подтвердить научную достоверность — его результаты остаются
`execution_mode=simulation`.

Дополнительно открыт D8 (§17.3.4): контрактный лимит `Message.content` (8192 символа) делает
full_context недоступным для документов больше ~8 КБ, хотя token budget это допускает.

---

## 18. D8 closure — Message.content contract guard + D5/D6 evidence

*Дата: 2026-11-18. Изменён только Application-слой (`ExtractionService`) и тесты; публичные
контракты, Model Gateway, схемы, Checkpointer, конфигурация и последовательность G1–G4 не
менялись.*

### 18.1. D8 — реализация

**Проверенный контракт.** `Message.content: NonEmptyStr` =
`Annotated[str, StringConstraints(min_length=1, max_length=8192)]`; ограничение применяется
Pydantic к **каждому отдельному** сообщению, граница 8192 включительна. Лимит читается из
самого контракта (`_message_content_limit()` → `Message.model_fields["content"].metadata`),
без дублирующей константы и без нового конфигурационного параметра.

**Точка проверки.** `ExtractionService._call_with_transient_retries`: сначала формируются
окончательные строки всех исходящих сообщений (`_message_contents` — system/prompt и
user/document), затем `_guard_outbound_messages` проверяет длину каждой строки, и только
после этого создаются `Message`/`ModelRequest` и вызывается
`ModelGatewayPort.generate_structured`. Проверка одна, в общей точке отправки; по узлам
workflow не дублируется.

**Классификация.** Используется существующая `ExtractionContextOverflow`
(`ErrorCategory.CONTEXT`, `RUN_ERROR_CODES → extraction_context_overflow`), `to_run_error()`
и штатный терминальный путь: один `RunError`, диагностический Run Manifest, без retry и без
`retry_started`. Сообщение ошибки:
`outbound user message exceeds Message.content limit: actual_chars=…, max_chars=8192; Model
Gateway request was not sent` (роль, фактические символы, лимит, факт неотправки; текст
документа/prompt в лог не попадает). CLI использует существующую классификацию: терминальный Run `failed` с `RunError` →
exit **1** (execution failure), без нового кода; `4` остаётся только для controlled stop
(`requires_ocr` / `needs_human_review`). Уточнение и тесты — §18.9.

**Поведение.** Текст не обрезается, не разбивается и не отправляется частично; следующая
группа не запускается; token-budget проверка продолжает работать независимо.

### 18.2. D8 — тестирование

Файл `tests/agent1a/test_d8_and_evidence.py`; команда
`uv run --group parsing pytest tests/agent1a/test_d8_and_evidence.py -q` → **7 passed**, exit 0.

| Тест | Вход | Ожидание | Факт |
|---|---|---|---|
| `test_message_at_contract_limit_is_sent_untruncated` | user-сообщение ровно 8192 символа | проходит; 4 вызова gateway; содержимое не изменено | PASS: длина 8192, `content.endswith(text)`, SHA-256 стабилен |
| `test_message_above_contract_limit_is_refused_before_contract` | 8193 символа | отказ до контракта, 0 вызовов, typed context | PASS: `actual_chars=8193`, `max_chars=8192`, «was not sent», canonical SHA не изменён |
| `test_d8_regression_20000_characters_is_a_controlled_stop` | 20 000 символов | token budget проходит, контракт отклоняет | PASS: 0 вызовов, один `RunError` с `extraction_context_overflow`, manifest `failed` |
| `test_each_message_is_checked_separately` | user 8192 + system (сумма > 8192) | лимит по каждому сообщению | PASS: запрос отправлен, 4 вызова |

Сырой `pydantic.ValidationError` не возникает ни в одном сценарии; частичная отправка
отсутствует. CLI на реальном проекте (документ 20 000 символов): `agent1a extract` →
**exit 1** (терминальная ошибка `failed` с `RunError`; до исправления §18.9 ошибочно
возвращался 4), сообщение содержит `actual_chars=20073, max_chars=8192; Model Gateway request
was not sent`; `agent1a report` в новом процессе → `final status: failed`, `errors: 1`, все группы
`pending`, checkpoint `build_extraction_plan`.

### 18.3. D5 — подтверждающий тест

`test_checkpoint_read_failure_is_typed_and_preserves_cause` (параметризован: `OSError`,
`sqlite3.OperationalError`). Сценарий: реальный `SqliteSaver.get_tuple` (именно чтение,
которое вызывает `graph.get_state` при `resume`) подменяется отказом; Run создан и остановлен
на breakpoint после G2, восстановление выполняется новым `bootstrap_application`. Результат:
`ExtractionCheckpointError`, категория `checkpoint`, код `checkpoint_error`,
`__cause__` — исходный `OSError`/`sqlite3.OperationalError`, успешного восстановления нет;
ошибка не маскируется под контекстную. Успешный cold-resume по-прежнему покрыт
`test_resume_works_in_a_separate_process` и диагностикой A2 (без изменений).

### 18.4. D6 — подтверждающий тест

`test_checkpoint_saved_is_not_emitted_when_checkpoint_write_fails`. `ObservedSqliteSaver`
создаётся с записывающим logger; базовый `SqliteSaver.put` подменяется отказом
(`OSError`); `saver.put(...)` пробрасывает ошибку, ни одного события `checkpoint_saved` не
зарегистрировано. Затем та же запись выполняется штатно: ровно **одно** событие
`checkpoint_saved` с `thread_id` и `checkpoint_id`. Классификация ошибок в `ObservedSqliteSaver`
не дублируется — это делает существующая граница Application-слоя.

### 18.5. Файловый инвентарь

**Создано (1):** `tests/agent1a/test_d8_and_evidence.py` (7 тестов).

**Изменено (5):** `src/research_platform/application/services/agent1a_extraction.py`
(preflight-проверка сообщений, `_message_contents`, `_message_content_limit`),
`docs/iteration_reports/iteration_3_report.md` (раздел 18),
`docs/development/agent_1a_extraction.md`,
`docs/development/limitations.md`, `docs/development/testing.md`.

**Удалено:** нет. Контракты, схемы, `pyproject.toml`, `uv.lock`, конфигурация не изменялись.

### 18.6. Регрессионные проверки

| Команда | Результат |
|---|---|
| `uv run --group parsing pytest tests/agent1a/test_d8_and_evidence.py -q` | 7 passed, exit 0 |
| `uv run --group parsing pytest -q` | **763 passed, 0 failed, 0 skipped**, exit 0 |
| `uv run --group parsing ruff check .` | exit 0 |
| `uv run --group parsing ruff format --check .` | exit 0 (232 файла) |
| `uv run --group parsing ty check` | exit 0, 0 диагностик |
| `uv lock --check` | exit 0 (без изменений) |
| `export_schemas.py --check` | exit 0, 10 схем без изменений |
| `config validate` / `platform check` / `agent1a workflow-smoke` | exit 0 / exit 0 / exit 0 |
| CLI: `document process` (20 000 симв.) → `agent1a extract` → `agent1a report` | 0 / **1** (terminal failure) / 0, manifest `failed` |

### 18.7. Контракты

`Message.content` по-прежнему `min_length=1, max_length=8192`; `ModelRequest` и Model Gateway
contract не изменены; экспортируемые JSON-схемы (10) побайтово прежние; URI namespaces,
Artifact Store и SQLite Checkpointer не затронуты; `InMemoryRunRepository` не изменён;
последовательность G1–G4 прежняя.

### 18.8. Остаточные ограничения

Документы, сообщение которых длиннее 8192 символов, контролируемо отклоняются с exit 1 и
диагностическим манифестом — это не обработка крупных документов. Полноценная работа с
такими документами требует retrieval/chunking в Итерации 4; token budget и лимит сообщения
остаются двумя независимыми условиями. Fake Model Adapter по-прежнему не подтверждает
научную точность извлечения; OI-001…004, OI-006, OI-007, OI-008 открыты.

---

### 18.9. Exit-code classification correction (controlled failure vs controlled stop)

*Дата: 2026-11-18. Точечная корректировка CLI-классификации; `ErrorCategory.CONTEXT`,
`extraction_context_overflow`, Run status, workflow G1–G4, Model Gateway, SQLite Checkpointing
и публичные контракты не изменялись.*

#### 18.9.1. Исходное расхождение

До исправления `_extraction_exit_code` отображал `ErrorCategory.CONTEXT` в
`EXIT_CONTROLLED_STOP` (4), то есть D8-ошибка контекста (терминальный Run `failed` с
`RunError`) получала тот же код, что `requires_ocr`/`needs_human_review` (Run
`waiting_for_human`, без `RunError`). Это смешивало два разных исхода.

Документированный контракт CLI (`cli.py`, заголовок модуля) различает их:

| Код | Смысл |
|---|---|
| 0 | операция успешна |
| 1 | сбой исполнения или валидации (нарушение контракта, плохой вход, терминальная ошибка извлечения) |
| 2 | ошибка конфигурации |
| 3 | инфраструктура недоступна/не инициализирована |
| 4 | controlled stop / ожидание внешнего действия |

По ТЗ Итерации 3 (§7.3, §14.1) контекстное превышение — `Context — Controlled failure`
(терминальный Run `failed`), а `controlled stop` — только `requires_ocr` и
`needs_human_review`.

#### 18.9.2. Исправление

| Компонент | Изменение |
|---|---|
| `cli.py::_extraction_exit_code` | `CONTEXT` → `EXIT_CONTRACT_VIOLATION` (1); маппинг остальных категорий не изменился (`INPUT/ARTIFACT/INTEGRITY/CONTRACT` → 1; `QUALITY` → 4; `STORAGE/CHECKPOINT/MODEL_*/STRUCTURED_OUTPUT/UNEXPECTED` → 3) |
| `cli.py::_extraction_outcome_exit_code` (новый helper) | завершённый outcome без исключения: `requires_ocr`/`needs_human_review` → 4; `failed` → 1; иначе 0. Используется в `extract` и `resume` (раньше любой `interrupt_reason`, включая `quality failed`, давал 4) |
| `cli.py` (docstring) | явно зафиксировано: 1 — terminal extraction failure (в т.ч. context overflow); 4 — controlled stop без `RunError` |

Новые числовые коды не вводились; архитектура и workflow не менялись.

#### 18.9.3. Фактическое соответствие кодов

| Сценарий | Run status | RunError | processing_status в state | Exit code |
|---|---|---|---|---|
| Успешное извлечение | `completed` / `completed_with_warnings` | нет | `completed` | **0** |
| `requires_ocr` | `waiting_for_human` | нет | `requires_ocr` | **4** |
| `needs_human_review` | `waiting_for_human` | нет (interrupt stop reason) | `needs_human_review` | **4** |
| Quality Gate `failed` | `failed` | 1 × `quality_gate_failed` | `failed` | **1** |
| Context overflow (D8, 20 000 симв.) | `failed` | 1 × `extraction_context_overflow` | `failed` | **1** |
| Model permanent/transient после retry | `failed` | 1 × `model_permanent_error`/`model_transient_error` | `failed` | **3** (инфраструктура) |
| Structured output после repair | `failed` | 1 × `structured_output_invalid` | `failed` | **3** |
| Artifact Store / Checkpointer | `failed` / Run не создан | `extraction_storage_error` / `checkpoint_error` | `failed` | **3** |
| Input/artifact/integrity | — | соответствующая категория | — | **1** |

Ключевое различие зафиксировано и статусом, и наличием/отсутствием `RunError`: controlled stop
не создаёт `RunError`, его manifest содержит `errors: []`; терминальный сбой (включая D8)
содержит ровно одну запись и manifest `final_status=failed`.

#### 18.9.4. Тесты

| Тест | Проверка | Результат |
|---|---|---|
| `test_d8_and_evidence.py::test_d8_cli_exits_one_as_a_terminal_failure` | CLI 20 000 символов → 1; stderr содержит `extraction_context_overflow` и `max_chars=8192`; опубликован один manifest с `final_status=failed` и кодом | PASS |
| `test_agent1a_cli.py::test_context_overflow_is_terminal_failure_with_exit_one` | exit 1 ≠ 4; manifest `failed` с `extraction_context_overflow` | PASS |
| `test_agent1a_cli.py::test_quality_failed_is_terminal_failure_not_controlled_stop` | quality `failed` → 1; manifest `failed`, `quality_gate_failed` | PASS |
| `test_agent1a_cli.py::test_controlled_stops_keep_exit_four[requires_ocr]` | `requires_ocr` → 4, `run_status=waiting_for_human`, `errors == []` | PASS |
| `test_agent1a_cli.py::test_controlled_stops_keep_exit_four[needs_human_review]` | `needs_human_review` → 4, `run_status=waiting_for_human`, `errors == []` | PASS |
| Существующие `test_extract_stops_on_requires_ocr`, `test_resume_does_not_confirm_a_human_review` | по-прежнему 4 | PASS |

Ручная проверка CLI на реальном проекте (документ 20 000 символов, canonical 20 000):
`research-platform agent1a extract --document-run-id …` → **exit 1**, stderr:
`error: extraction_context_overflow (context): outbound user message exceeds
Message.content limit: actual_chars=20073, max_chars=8192; Model Gateway request was not sent`.

Полный набор: **768 passed, 0 failed, 0 skipped**; `ruff check`, `ruff format --check`,
`ty check`, `uv lock --check`, `export_schemas --check` — exit 0.

#### 18.9.5. Изменённые файлы

`src/research_platform/cli.py` (маппинг и helper, docstring), `tests/agent1a/test_agent1a_cli.py`
(+3 сценария, 4 теста с параметризацией, helper доступа к manifest),
`tests/agent1a/test_d8_and_evidence.py` (+1 CLI-тест), `docs/iteration_reports/iteration_3_report.md`
(§18 и §18.9), `docs/development/limitations.md`, `docs/development/testing.md`.
Публичные контракты, схемы, конфигурация, зависимостι и lock-файл не изменялись.
