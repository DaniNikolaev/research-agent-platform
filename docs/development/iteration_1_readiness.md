# Готовность к Итерации 1: критерии входа

**Дата:** 2026-11-12 · **Основание:** `docs/reports/pre_iteration_1_architecture_closure.md` · **Статусы:** `PASS` / `PARTIAL` / `BLOCKED` / `NOT_STARTED`

> **Исторический документ.** Это критерии *входа* в Итерацию 1, составленные до её начала.
> Итерация 1 завершена: фактические результаты, реализованные компоненты и ограничения —
> в [`docs/iteration_reports/iteration_1_report.md`](../iteration_reports/iteration_1_report.md).
> Открытые вопросы владельца (`OI-001…OI-011`) по-прежнему актуальны и отслеживаются в
> [`docs/decisions/open_items.md`](../decisions/open_items.md).

Документ фиксирует, что именно проверено перед стартом Итерации 1 (Platform Skeleton) и что
должно быть закрыто владельцем проекта. `NOT_STARTED` для позиции, которая по плану
относится к Итерации 1, — это норма, а не провал: здесь важно отделить «не начато по
плану» от «не готово к старту».

---

## A. Архитектура

| Критерий | Статус | Подтверждение |
|---|---|---|
| Модульный монолит сохранён | PASS | `src/research_platform/{domain,application,platform,agents}`, ADR-002; переписывания архитектуры в рамках закрытия вопросов не было |
| Dependency boundaries проверены | PASS | `tests/unit/test_import_boundaries.py`: AST-скан, включая относительные импорты, плюс тесты-«стражи» |
| Domain/Application не зависят от конкретных инфраструктурных адаптеров | PASS | `domain` не импортирует `application`/`platform`/`agents` и не знает ФС; `application` не импортирует `platform` |
| CLI остаётся единой локальной точкой входа | PASS | `cli.py` — единственный entry point; тест на единственность; `research-platform --help/--version/contracts validate` |
| Переносимость путей | PASS | абсолютные пути Windows запрещены в контрактах (`source_file_name`, `artifact://` URI), физические пути — только в адаптере |

## B. Контракты

| Критерий | Статус | Подтверждение |
|---|---|---|
| Agent 1A → Agent 1B согласован | PASS | пакет 2.1 → вход 1.1; `tests/contract/test_agent_1a_package.py`, `test_agent_1b_contract.py` |
| Evidence Contract согласован | PASS | `domain/evidence/models.py` (2.1), `tests/unit/test_evidence_contract.py`; кандидат не может нести координаты |
| Coverage Trail согласован | PASS | `domain/coverage/models.py` + `rules.py`, `docs/contracts/coverage_trail.md`, `tests/unit/test_coverage_contract.py` |
| RetrievalMode и RetrievalStrategy однозначны | PASS | `domain/runs/models.py`; `hybrid` реализован; ADR-007 |
| Версии схем зафиксированы | PASS | 2.1/1.1 — текущие; 2.0/1.0 и legacy v1.0 — заморожены с `.sha256`, проверка в `tests/contract/test_frozen_schemas.py` |
| `not_found` связан с покрытием поиска | PASS | правило в `coverage/rules.py` и тесты «not_found без complete coverage отклоняется» |
| Научная проверка принадлежит Agent 1B | PASS | `scientific_review_status` только в `Agent1BVerificationReportV11`; в пакете 1A поля нет |

## C. Платформа

| Критерий | Статус | Подтверждение |
|---|---|---|
| Windows native выбран для локальной разработки | PASS | ADR-010, уточнён при закрытии вопросов |
| Python и uv зафиксированы | PASS | `.python-version` = `3.13.18`; `[tool.uv] required-version = "==0.13.7"`; `uv.lock` (168 пакетов) |
| PostgreSQL + pgvector определён как целевой backend | PASS | ADR-006; версия расширения vs Python-пакет разведены |
| ArtifactStore определён как интерфейс с локальной реализацией для первого релиза | PARTIAL | `ArtifactStorePort` готов; реализация — Итерация 1 (`platform/artifact_store` пуст осознанно) |
| Docker/WSL2 не требуется для локальной разработки | PASS | ADR-010; Linux/Docker — перспективный вариант |

## D. Model Gateway

| Критерий | Статус | Подтверждение |
|---|---|---|
| Provider-neutral interface готов | PASS | `application/ports/model_gateway/` (DTO, ошибки, Protocols); `tests/unit/test_model_gateway.py` |
| FakeModelAdapter может использоваться в Итерации 1 | PASS | `tests/fixtures/fakes.py`: `FakeChatModel`, `FakeEmbeddingModel`, `FakeReranker` |
| Реальный адаптер отложен до подтверждения документации агрегатора | PASS | ADR-005; кандидат `DeepSeek V4.1 Flash 1017` зафиксирован, adapter `Deferred`, вопросы `OI-001…OI-005` |
| Выбор провайдера не влияет на Domain/Application/LangGraph | PASS | контракты не содержат provider-специфичных полей; проверяется границами импорта |

## E. Runtime (требования, а не реализация)

| Критерий | Статус | Подтверждение |
|---|---|---|
| Требования к Run Manager определены | PARTIAL | `RunRecord`, `RunStatus`, `RunError`, `RunRepositoryPort`, ADR-010; реализация — Итерация 1 |
| Требования к LangGraph checkpointing определены | PARTIAL | `Agent1AState` (ссылки, а не данные), `retry_counters`, лимиты из `WorkflowSettings`; checkpointer не реализован (Итерация 3) |
| Требования к журналированию и ошибкам определены | PASS | `RunError` (code/message/stage/retryable/correlation_id, запрет секретов), иерархия `ModelError`, `config.observability`, `privacy`; реализация логов — Итерация 1 |
| Идемпотентность и неизменяемость запусков зафиксированы | PASS | rule «новый `run_id` на попытку», запрет перезаписи артефактов в `ArtifactStorePort`, пакет `frozen=True` |

## F. Остаточные риски

| Риск | Статус | Что делать |
|---|---|---|
| Бинарные зависимости парсинга | PASS (с оговоркой) | установка и импорт `docling 2.150.0` / `pymupdf 2.1` на Windows 10 x64 / CPython 3.13.18 подтверждены; качество парсинга и OCR не проверялись (Итерация 2) |
| Выбор модели и её лимиты | BLOCKED | решение владельца: `OI-001…OI-004`; до этого — только fake adapter |
| Конфигурация и запуск локальной БД | PARTIAL | архитектура зафиксирована (ADR-006), политика запуска/бэкапа — `OI-006` |
| Ограничения среды разработки | PARTIAL | инцидент прав на каталог проверен (см. отчёт, §6); системные настройки не менялись; риск повторного отказа записи остаётся за владельцем |
| OCR-движки, embedding-модель, reranker, gold-set | BLOCKED (для своих итераций) | `OI-008`, `OI-009`, `OI-010`, `OI-011`; на Итерацию 1 не влияют |
| Канал human-in-the-loop | PARTIAL | `OI-007`; `processing_status="waiting_for_human"` уже предусмотрен, но интерфейса нет |

---

## Итог

**К старту Итерации 1 готово:** архитектура и границы, контракты Agent 1A/1B, Evidence
Contract, Coverage Trail, версионирование схем, платформа разработки, provider-neutral
Model Gateway с fake adapter, требования к Run Manager/checkpointing/логированию.

**Требует участия владельца до соответствующих шагов Итерации 1:**

1. `OI-001…OI-004` — агрегатор, протокол, capabilities, лимиты и фактический model id
   (без этого нельзя писать настоящий provider adapter; с fake adapter Итерация 1
   стартует нормально);
2. `OI-006` — политика запуска локальной PostgreSQL (Docker Compose против локальной
   службы) и бэкапа: это первый шаг storage-части Итерации 1.

**Не является блокером:** OCR-движки, embedding-модель, reranker, состав gold-set,
канал HITL — они нужны позже (Итерации 2, 4, 6) и уже отслеживаются в
[`docs/decisions/open_items.md`](../decisions/open_items.md).
