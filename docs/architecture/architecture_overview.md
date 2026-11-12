# Архитектура платформы `research-agent-platform` (состояние после Итерации 1)

**Версия:** 1.2 · **Дата:** 2026-11-12 · **Основание:** `ADD_MultiAgent_Research_Platform_v1.0`, ТЗ Итерации 1, `docs/iteration_reports/iteration_1_report.md` · **Статус:** платформенный каркас исполняется; функциональная обработка документов — следующие итерации.

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
| `cli.py` | единственная точка входа: 5 команд, коды возврата 0–4 | готово |
| `app.py` | `ApplicationBootstrap` → `ApplicationContext`, закрытие ресурсов | готово |
| `config.py` | типизированные настройки: 8 секций, YAML + env, валидация | готово |
| `composition/runtime_factory.py` | сборка LangGraph-рантайма из сервисов | готово |
| `domain/base.py` | контрактный базовый тип (`extra="forbid"`, `frozen`, UTC) | готово |
| `domain/documents/` | `DocumentManifest` | готово |
| `domain/cards/` | карточка: 9 разделов, 27 полей | готово (контракт) |
| `domain/evidence/` | evidence 2.1 + frozen 2.0 + правила ledger | готово (контракт) |
| `domain/coverage/` | Coverage Trail и правила `not_found` | готово (контракт) |
| `domain/runs/` | `RunRecord`, `RunStatus`, `ALLOWED_TRANSITIONS`, `RunMetadata`, артефакты, генераторы идентификаторов | готово |
| `domain/validation/` | техническая валидация + плейсхолдер семантики | готово |
| `application/ports/` | 6 портов: run repository, artifact store, checkpoint, logging, model gateway, document repository | готово |
| `application/services/` | RunManager, ArtifactService, ConfigurationService, HealthCheckService, Redaction | готово |
| `platform/artifact_store/` | локальное файловое хранилище (атомарность, sha256, path safety) | готово |
| `platform/persistence/` | `InMemoryRunRepository` | готово |
| `platform/checkpointing/` | `InMemoryCheckpointProvider` (LangGraph `InMemorySaver`) | готово |
| `platform/models/` | fake-адаптеры модели (offline) | готово |
| `platform/observability/` | JSONL-журнал с редактированием секретов | готово |
| `platform/contracts/` | экспорт 7 JSON Schema | готово |
| `agents/agent_1a/graph/` | 4 узла, компоновка графа, раннер, фабрика | готово (технический) |
| `agents/agent_1a/contracts/` | пакет 2.1, пакет 2.0 (frozen), GraphState, адаптер v1→v2 | готово |
| `agents/agent_1b/contracts/` | вход 1.1/1.0, отчёт 1.1/1.0 | готово |
| `platform/{model_gateway,run_manager}/` | дом реальных адаптеров | заглушки (объяснены) |
| парсинг PDF/TXT, OCR, RAG, резолвер, PostgreSQL, FastAPI | — | следующие итерации |

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

Соответствие итерациям ADD §22: 0 — контракты и каркас (завершено), 1 — platform skeleton
(завершено, см. `docs/iteration_reports/iteration_1_report.md`), 2 — parsing/quality gate,
3 — LangGraph workflow, 4 — RAG и Evidence Resolver, 5 — validation и пакет для 1B,
6 — evaluation.
