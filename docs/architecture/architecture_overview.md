# Архитектура платформы `research-agent-platform` (состояние после Итерации 2)

**Версия:** 1.3 · **Дата:** 2026-11-14 · **Основание:** `ADD_MultiAgent_Research_Platform_v1.0`, ТЗ Итерации 2, `docs/iteration_reports/iteration_1_report.md`, `docs/iteration_reports/iteration_2_report.md` · **Статус:** каркас исполняется; добавлен конвейер обработки документов (приём, разбор цифровых PDF и TXT, Quality Gate); OCR, retrieval и извлечение полей — следующие итерации.

Документ фиксирует **карту модулей и направления зависимостей** в том виде, в котором они
реализованы. Архитектурные решения и их обоснование — в
[`docs/decisions/`](../decisions/README.md). ADD не переписывается.

---

## 1. Стиль и слои

Платформа — **модульный монолит** (ADR-002): один Python-пакет, внутри — изолированные
слои и два независимых workflow (Agent 1A, Agent 1B), связанных только
версионированными контрактами и логическими ссылками на артефакты.

```text
                 ┌──────────────────────────────────────────────────┐
 CLI (cli.py)    │ --version · config validate · platform check ·    │
                 │ contracts validate · agent1a workflow-smoke       │
                 └───────────────────┬──────────────────────────────┘
                                     │
                 ┌───────────────────▼──────────────────────────────┐
 Composition     │ app.py: ApplicationBootstrap → ApplicationContext │
                 │ composition/runtime_factory.py: сборка графа      │
                 └───────┬───────────────────────────────┬──────────┘
                         │ создаёт                       │ передаёт
    ┌────────────────────▼──────────────┐   ┌────────────▼─────────────────────────┐
    │ application/ports                 │   │ platform/: artifact_store (local),   │
    │  run_repository, artifact_store,  │◄──┤ persistence (in_memory),             │
    │  checkpoint, logging, model_gateway│   │ checkpointing (in_memory), models    │
    │ application/services              │   │ (fake), observability (jsonl),        │
    │  run_manager, artifact_service,   │   │ contracts (7 JSON Schema)            │
    │  configuration_service,           │   └──────────────────────────────────────┘
    │  health_check, redaction          │
    └───────┬───────────────────────────┘
            │ используют
    ┌───────▼───────────────────────────────────────────────────────────┐
    │ domain/: base, documents, cards, evidence, coverage, runs,         │
    │ validation  (чистые контракты; ни ФС, ни сети, ни LLM)             │
    └───────▲───────────────────────────────────────────────────────────┘
            │ используют
    ┌───────┴───────────────────────────────────────────────────────────┐
    │ agents/agent_1a/graph: state, nodes, workflow, factory (LangGraph)│
    │ agents/agent_1a/contracts: package 2.1 + 2.0 (frozen), adapter     │
    │ agents/agent_1b/contracts: input/report 1.1 + 1.0 (frozen)         │
    └───────────────────────────────────────────────────────────────────┘
```

**Правило, добавленное в Итерации 1:** сборка зависимостей вынесена в
`composition/` (композиционный корень). Это не архитектурный слой, а место, которому
разрешено знать все слои. Без него `application` импортировал бы конкретного агента, что
инвертирует зависимости. Поэтому `application/runtime/` из эскиза ТЗ заменён на
`composition/runtime_factory.py`.

**DSH не входит в runtime.** Ни один модуль пакета не обращается к DSH и не требует его
сессии (ADR-001, ADR-010).

---

## 2. Карта модулей

| Путь | Назначение | Состояние |
|---|---|---|
| `cli.py` | единственная точка входа: `--version`, `--help`, `contracts validate`, `config validate`, `platform check`, `document process`, `document report`, `agent1a workflow-smoke`; коды 0–4 | готово |
| `app.py` | `ApplicationBootstrap` → `ApplicationContext`, закрытие ресурсов | готово |
| `config.py` | типизированные настройки: 12 секций (8 платформенных + 4 документных), YAML + env, валидация | готово |
| `composition/runtime_factory.py` | сборка LangGraph-рантайма из сервисов | готово |
| `composition/document_pipeline.py` | сборка конвейера документов (адаптеры + сервисы) | готово |
| `domain/base.py` | контрактный базовый тип (`extra="forbid"`, `frozen`, UTC) | готово |
| `domain/documents/` | `DocumentManifest` (2.1), `SourceDocumentManifest` 1.0, `ParsedDocument` 1.0, `QualityReport` 1.0, элементы, провенанс, идентичность | готово |
| `domain/cards/` | карточка: 9 разделов, 27 полей | готово (контракт) |
| `domain/evidence/` | evidence 2.1 + frozen 2.0 + правила ledger | готово (контракт) |
| `domain/coverage/` | Coverage Trail и правила `not_found` | готово (контракт) |
| `domain/runs/` | `RunRecord`, `RunStatus`, `ALLOWED_TRANSITIONS`, `RunMetadata`, артефакты, генераторы идентификаторов | готово |
| `domain/validation/` | техническая валидация + плейсхолдер семантики | готово |
| `application/ports/` | 7 модулей портов: run repository, artifact store, checkpoint, logging, model gateway, document repository, document pipeline (парсер, инспектор, декодер, OCR, чтение источника) | готово |
| `application/services/` | RunManager, ArtifactService, ConfigurationService, HealthCheckService, Redaction; добавлено в Итерации 2: DocumentIngestionService, DocumentParsingService, QualityGateService, LanguageDetector | готово |
| `platform/artifact_store/` | локальное файловое хранилище (атомарность, sha256, path safety) | готово |
| `platform/persistence/` | `InMemoryRunRepository` | готово |
| `platform/checkpointing/` | `InMemoryCheckpointProvider` (LangGraph `InMemorySaver`) | готово |
| `platform/models/` | fake-адаптеры модели (offline) | готово |
| `platform/observability/` | JSONL-журнал с редактированием секретов | готово |
| `platform/contracts/` | экспорт 10 JSON Schema (7 прежних + 3 документных) | готово |
| `agents/agent_1a/graph/` | 4 узла, компоновка графа, раннер, фабрика | готово (технический) |
| `agents/agent_1a/contracts/` | пакет 2.1, пакет 2.0 (frozen), GraphState, адаптер v1→v2 | готово |
| `agents/agent_1b/contracts/` | вход 1.1/1.0, отчёт 1.1/1.0 | готово |
| `platform/{model_gateway,run_manager}/` | дом реальных адаптеров | заглушки (объяснены) |
| `platform/document_parsing/` | адаптеры разбора: Docling (PDF), PyMuPDF (инспекция/fallback), TXT (декодер, блочный парсер) | готово |
| `platform/documents/` | чтение пользовательского источника с проверками | готово |
| `workflows/document_*` | граф обработки документа, состояние, рантайм-кэш | готово |
| OCR, RAG, резолвер, PostgreSQL, FastAPI | — | следующие итерации |

---

## 3. Направления зависимостей (правила и их проверка)

| № | Правило | Как проверяется |
|---|---|---|
| 1 | `domain` не импортирует `application`, `platform`, `agents`, `config`, `cli`, LangChain/LangGraph, SQLAlchemy, Docling, PyMuPDF, provider SDK | AST-скан `tests/unit/test_import_boundaries.py` |
| 2 | `domain` не знает про файловую систему | там же |
| 3 | `application` не импортирует `platform`, `agents`, `cli`, LangGraph, LangChain, SQLAlchemy, Docling, SDK провайдера | там же |
| 4 | `application` может импортировать `config` — это типизированный контракт настроек без подключений; физические пути и адаптеры остаются вне application | там же + примечание в тесте |
| 5 | `agents` используют `domain`, `application.ports`, `application.services` и не импортируют `platform` | там же |
| 6 | Agent 1A и Agent 1B не импортируют внутренние модули друг друга | параметризованный тест для обоих направлений |
| 7 | Только `composition/` и `cli.py` знают о конкретном агенте и конкретных адаптерах | ревью структуры + правило 3 |
| 8 | Логирование идёт через `application/ports/logging.py`; редактирование секретов — `application/services/redaction.py` | те же тесты границ |

Относительные импорты разрешаются против пакета импортирующего модуля (отдельный
тест-«страж»), поэтому обход правила через `from ...platform import x` не проходит.

**Переносимость путей:** в Domain/Application нет абсолютных путей Windows
(`source_file_name` — «голое» имя файла, ссылки на артефакты — логические `artifact://`
URI). Физические пути создаются только в инфраструктурном слое через конфигурацию
(`paths.data_root` и т.п.) — ADR-010.

---

## 4. Контракты и версионирование

| Контракт | Версия | Pydantic | JSON Schema | Статус |
|---|---|---|---|---|
| Пакет Agent 1A → 1B | **2.1** | `Agent1AExtractionPackageV21` | `contracts/agent_1a/v2.1/extraction_package.schema.json` | текущий |
| Кандидаты evidence | **2.1** | `EvidenceCandidateList` | `contracts/agent_1a/v2.1/evidence_candidates.schema.json` | текущий |
| Вход Agent 1B | **1.1** | `Agent1BInputPackageV11` | `contracts/agent_1b/v1.1/input_package.schema.json` | текущий |
| Отчёт Agent 1B | **1.1** | `Agent1BVerificationReportV11` | `contracts/agent_1b/v1.1/verification_report.schema.json` | текущий |
| Пакет Agent 1A → 1B | 2.0 | `Agent1AExtractionPackageV2` | `contracts/agent_1a/v2/…` + `.sha256` | заморожен |
| Вход Agent 1B | 1.0 | `Agent1BInputPackageV1` | `contracts/agent_1b/v1/input_package.schema.json` + `.sha256` | заморожен |
| Отчёт Agent 1B | 1.0 | `Agent1BVerificationReportV1` | `contracts/agent_1b/v1/verification_report.schema.json` + `.sha256` | заморожен |
| Legacy-карточка | 1.0 | — | `contracts/legacy/agent_1a_v1/card.schema.json` + `.sha256` | заморожен |

Правила:

1. Источник истины — Pydantic-модели; JSON Schema **генерируется**
   (`tools/export_schemas.py`) и проверяется побайтово.
2. Замороженные схемы генерируются из замороженных снимков моделей (`domain/runs/v2_0.py`,
   `domain/evidence/v2_0.py`) и защищены контрольными суммами
   (`tests/contract/test_frozen_schemas.py`).
3. Все контрактные модели `frozen=True` и `extra="forbid"`.
4. Смена смысла поля, статуса или правила — только новая версия контракта + ADR.
5. Внутренние (непубликуемые) контракты Итерации 1 — `RunRecord`, `ArtifactMetadata`,
   `HealthReport`, `SmokeRunRequest` — версионируются кодом и тестами, но не входят в
   JSON Schema пакетов; их изменение не требует новой версии 2.1/1.1.

Подробности: [`evidence_contract.md`](../contracts/evidence_contract.md),
[`coverage_trail.md`](../contracts/coverage_trail.md),
[`run_and_artifact_contracts.md`](../contracts/run_and_artifact_contracts.md),
[`artifact_store.md`](../development/artifact_store.md).

---

## 5. Retrieval: два независимых понятия

| Понятие | Значения | Смысл |
|---|---|---|
`RetrievalMode` | `full_context`, `rag`, `hybrid`, `not_applicable` | что получает модель |
`RetrievalStrategy` | `dense`, `lexical`, `dense_lexical` | как выполняется поиск |

Режим выбирает детерминированная политика Python (токены, доступный контекст модели,
структура документа, конфигурация), а не LLM. `rag`/`hybrid` требуют
`embedding_model_id`, `index_version` и `retrieval_strategy`; остальные режимы их
запрещают (ADR-007). В Итерации 1 retrieval не реализован — контракт режимов уже действует
в `RunMetadata` и в состоянии графа.

---

## 6. Evidence и Coverage

**Evidence.** LLM предлагает только `quote` и подсказки (`page_hint`, `section_hint`,
`block_id_hint`, `context_before`/`context_after`). `EvidenceRecord` создаёт Python:
страница, `source_sha256`, `char_start`/`char_end`, `bbox`, `occurrence_index`,
`match_status`, `match_type`, `verification_method`, версии парсера и резолвера. Fuzzy
никогда не становится `resolved` (ADR-008).

**Coverage.** `FieldCoverageRecord` фиксирует по каждому из 27 полей: режим и стратегию
поиска, статус покрытия, область поиска, найденные блоки, попытки, факт fallback и
`not_found_eligible`. `not_found` допустим только при `complete` + `not_found_eligible`, а
для `rag`/`hybrid` — только при выполненном полнотекстовом fallback.

В Итерации 1 реализованы контракты и правила, но не сам поиск: резолвер и retrieval —
Итерация 4.

---

## 7. Runtime Итерации 1

```text
CLI ──► ApplicationBootstrap ──► ApplicationContext
                                      │
        ┌─────────────────────────────┼────────────────────────────┐
        │                             │                            │
   RunManager                  ArtifactService              HealthCheckService
        │                             │                            │
 InMemoryRunRepository     LocalFileArtifactStore        (проверки + статусы)
        │
 RuntimeFactory ──► Agent1ASmokeGraph ──► LangGraph (4 узла)
                          │                    │
                          └── InMemoryCheckpointProvider (thread_id = run_id)
                          └── FakeModelGateway (offline, детерминированный)
                          └── StructuredLogger (JSONL, redaction)
```

Ключевые свойства:

- **явные зависимости:** узлы графа получают сервисы через `GraphDependencies`; глобальных
  синглтонов нет;
- **контракт состояния:** `Agent1AState` (компактный, JSON-сериализуемый) с двумя новыми
  полями — `correlation_id` и `technical_trace_ref`;
- **жизненный цикл:** `RunManager` — единственный владелец переходов статуса;
- **неизменяемые артефакты:** запись атомарна, повтор с другим содержимым — конфликт; артефакт
  видим только при наличии объекта и валидного sidecar метаданных;
- **наблюдаемость:** JSONL с `run_id`/`correlation_id`, редактирование секретов на выходе;
- **проверяемость:** `platform check` и `agent1a workflow-smoke` — исполняемые команды, а не
  описание.

Подробности: [`langgraph_runtime.md`](../development/langgraph_runtime.md),
[`run_manager.md`](../development/run_manager.md),
[`artifact_store.md`](../development/artifact_store.md),
[`observability.md`](../development/observability.md),
[`cli.md`](../development/cli.md).

---

## 8. Хранение и провайдер

| Область | Решение | Статус в Итерации 1 |
|---|---|---|
| Метаданные и состояние | PostgreSQL 18 + pgvector (ADR-006) | не реализовано; `InMemoryRunRepository` |
| Крупные артефакты | файловое хранилище за `ArtifactStorePort` | реализовано локально |
| Адресация артефактов | три пространства: документное `artifact://documents/…/runs/…`, запуска `artifact://runs/…`, платформенное `artifact://platform/…`; публикуемый `ArtifactReference` — только документное | реализовано (техническое закрытие после Итерации 1) |
| Checkpointing | LangGraph-совместимый saver | in-memory; durable — позже |
| LLM | provider-neutral Model Gateway (ADR-005) | fake-адаптер; реальный отложен |

Ни PostgreSQL, ни реальный провайдер не подключались: первый требует решения владельца по
эксплуатации (`OI-006`), второй — подтверждения параметров (`OI-001…OI-004`).

---

## 9. Границы ответственности

| Компонент | Отвечает | Не отвечает |
|---|---|---|
| Python-код платформы | порядок выполнения, проверки, координаты evidence, coverage, запись артефактов, переходы статусов | смысловая интерпретация статьи |
| LLM (через Model Gateway) | извлечение значений по переданному контексту, кандидаты evidence | маршрут, файловые операции, координаты, статусы, coverage, научный вердикт |
| Agent 1A | карточка, ledger, техническая валидация, coverage trail, пакет | объявление смысловой достоверности |
| Agent 1B | независимая проверка, отчёт, `scientific_review_status` | изменение карточки Agent 1A |
| DSH | разработка, тесты, диагностика | участие в runtime |

`scientific_review_status` существует **только** в отчёте Agent 1B (ADR-004).

---

## 10. Что заведомо не реализовано

Парсинг PDF/TXT (Docling/PyMuPDF установлены и импортируются, обработки нет), OCR,
quality gate, chunking, embeddings, retrieval, reranking, Evidence Resolver, извлечение
полей карточки, реальный Model Gateway adapter, PostgreSQL/ORM/миграции, FastAPI, Docker,
Agent 1B workflow, durable checkpointing, обработка реальных статей.

> **Поправка Итерации 2.** Парсинг цифровых PDF и TXT и Quality Gate реализованы — см. §11.
> Всё остальное из этого списка по-прежнему не реализовано; OCR остаётся заблокированным
> решением владельца (`OI-008`).

Соответствие итерациям ADD §22: 0 — контракты и каркас (завершено), 1 — platform skeleton
(завершено, см. `docs/iteration_reports/iteration_1_report.md`), 2 — parsing/quality gate
(завершено с условиями, см. `docs/iteration_reports/iteration_2_report.md`),
3 — LangGraph workflow, 4 — RAG и Evidence Resolver, 5 — validation и пакет для 1B,
6 — evaluation.

---

## 11. Document pipeline (Итерация 2)

Отдельный конвейер обработки одного документа: приём → разбор → канонический текст → контроль
качества. Он **не** является частью графа Agent 1A и не связан с моделью: ни одного вызова LLM в
нём нет.

### 11.1. Размещение по слоям

```text
domain/documents/            identity · models (SourceDocumentManifest, DocumentManifest)
                             elements · parsed · quality · provenance
        ▲                    (чистые контракты: ни ФС, ни Docling, ни LangGraph)
        │
application/ports/document_pipeline.py
        ▲                    DocumentParserPort · DocumentInspectorPort · TextDecoderPort
        │                    OcrEnginePort (объявлен, реализации нет) · SourceReaderPort
application/services/        document_ingestion · document_parsing · quality_gate
                             language_detection · document_errors · artifact_ids
        ▲
platform/                    document_parsing/docling · document_parsing/pymupdf
                             document_parsing/txt · documents/file_reader
        ▲                    (единственное место, где импортируются Docling и PyMuPDF)
composition/document_pipeline.py
        ▲                    build_document_pipeline · document_dependencies
workflows/                   document_state · document_workflow (LangGraph)
        ▲
cli.py                       document process · document report
```

### 11.2. Поток

```text
validate_input → initialize_run → ingest_source → inspect_document
      → route_by_format ─┬→ parse_pdf
                         └→ parse_txt
      → build_canonical_document → run_quality_gate
      → persist_parsed_artifacts → finalize_run
```

| Этап | Что происходит | Артефакты |
|---|---|---|
| ingestion | allow-list, лимиты, сигнатура, SHA-256, `document_id`, коллизия, инспекция PDF, декодирование TXT, сохранение оригинала, повторная проверка источника | `original_document`, `handoff_package` (манифест) |
| parsing | Docling (PDF) или блочный разбор (TXT); fallback PyMuPDF только при техническом отказе | `parsed_document` (json) |
| canonical text | детерминированная сборка текста и символьных диапазонов | `parsed_document` (text/plain) |
| quality gate | именованные проверки, постраничные статусы, вердикт | `quality_report`, `run_log` (диагностика) |

Крупные объекты (байты источника, `ParsedDocument`, отчёт) передаются между узлами через
`DocumentRuntimeCache` и **не** попадают в состояние графа: состояние остаётся компактным и
JSON-сериализуемым.

### 11.3. Правило границы

| Правило | Проверка |
|---|---|
| `domain` не знает ни Docling, ни PyMuPDF, ни файловой системы | `tests/unit/test_import_boundaries.py` |
| `application` работает с **байтами** через порты и не импортирует `platform` | там же |
| Docling и PyMuPDF импортируются только в `platform/document_parsing/**` | `grep` по `src/` + правила теста границ |
| тип парсера не пересекает границу: адаптер возвращает `ParsedPayload` из доменных моделей | код адаптеров |
| `composition/` — единственное место, где известны и сервисы, и адаптеры | ревью + правило 3 |
| существующий граф `agent1a workflow-smoke` не тронут и продолжает исполняться | `tests/unit/test_runtime_graph.py` |

Практическое следствие: замена Docling на другой парсер не затрагивает контракты, артефакты и
Quality Gate — меняется только адаптер в `platform/` и, возможно, значения провенанса.

### 11.4. Документация конвейера

[`../contracts/document_manifest.md`](../contracts/document_manifest.md),
[`../contracts/parsed_document.md`](../contracts/parsed_document.md),
[`../contracts/document_provenance.md`](../contracts/document_provenance.md),
[`../contracts/quality_report.md`](../contracts/quality_report.md),
[`../development/document_ingestion.md`](../development/document_ingestion.md),
[`../development/document_parsing.md`](../development/document_parsing.md),
[`../development/quality_gate.md`](../development/quality_gate.md),
[`../development/document_parser_configuration.md`](../development/document_parser_configuration.md),
[`../iteration_reports/iteration_2_report.md`](../iteration_reports/iteration_2_report.md).
