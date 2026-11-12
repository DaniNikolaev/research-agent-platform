# Отчёт DSH по Итерации 1 — Platform Skeleton

**Проект:** `research-agent-platform` · **Дата:** 2026-11-12 · **Основание:** ТЗ Итерации 1 v1.0, ADD v1.0, `docs/reports/pre_iteration_1_architecture_closure.md` · **Исполнитель:** DSH

> **Дополнение.** После приёмки Итерации 1 выполнено техническое закрытие трёх замечаний
> (адресация Artifact Store, атомарность sidecar метаданных, воспроизводимость pytest).
> Раздел 6 этого отчёта описан как «document_id и run_id обязательны» — это состояние до
> закрытия; актуальная семантика хранилища и цифры тестов (586 passed) — в
> [`docs/reports/post_iteration_1_technical_closure.md`](../reports/post_iteration_1_technical_closure.md).

---

## 1. Executive Summary

**Итог: `PASS`.**

Платформенный каркас исполняется как единая система: приложение поднимается из одной точки
(`app.py`), читает и валидирует типизированную конфигурацию, создаёт адаптеры и сервисы,
запускает минимальный LangGraph-граф на синтетическом состоянии, хранит неизменяемые
артефакты локально, ведёт жизненный цикл запусков под контролем доменной машины состояний и
пишет структурированные журналы с correlation id и редактированием секретов.

Проверки (фактические результаты — §11):

- `uv run pytest -q` → **526 passed**, 0 failed, 0 skipped;
- `ruff check`, `ruff format --check`, `ty check` → 0;
- `tools/export_schemas.py --check` → все 7 схем актуальны (замороженные не изменены);
- `research-platform --version | --help | config validate | platform check | agent1a workflow-smoke`
  → ожидаемые коды возврата, включая smoke-запуск, оканчивающийся `completed_with_warnings`
  с обязательной строкой `SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED`.

Ограничения, зафиксированные в §14: нет парсинга, OCR, RAG, резолвера, реального
провайдера, PostgreSQL, FastAPI; checkpointing только in-memory. Ни одна из запрещённых
Итерацией 1 возможностей не реализована и не подменена заглушкой, выдающей результат.

Готовность к Итерации 2 — §15: платформенные условия (Bootstrap, Run Manager, Artifact
Store, Runtime, логирование, ошибки, тесты) выполнены; для парсинга нужно решение владельца
по OCR-движкам (`OI-008`).

---

## 2. Реализованные компоненты

| Компонент | Назначение | Фактические файлы | Статус | Тесты |
|---|---|---|---|---|
| Application Bootstrap | сборка контекста, порядок инициализации, закрытие ресурсов | `app.py` | готово | `test_platform_foundation.py`, `test_scenarios.py` |
| Config Manager | 8 секций, YAML + env, валидация, поиск секретов | `config.py`, `application/services/configuration_service.py` | готово | `test_config.py`, `test_configuration_service` (в `test_platform_foundation.py`), `test_cli_platform.py` |
| CLI | 5 команд, коды 0–4, `--json` | `cli.py` | готово | `test_cli.py`, `test_cli_platform.py` |
| Run Manager | жизненный цикл, переходы, ошибки, повторы | `application/services/run_manager.py` | готово | `test_run_manager.py` (25) |
| RunRepositoryPort + InMemory | хранение записей, потокобезопасность | `application/ports/run_repository.py`, `platform/persistence/in_memory_run_repository.py` | готово | `test_run_manager.py`, `test_platform_foundation.py` |
| ArtifactStorePort + LocalFile | неизменяемые артефакты, sha256, атомарность | `application/ports/artifact_store.py`, `platform/artifact_store/*` | готово | `test_artifact_store.py` (33) |
| LangGraph Runtime | граф из 4 узлов, раннер, фабрика | `agents/agent_1a/graph/{state,nodes,workflow,factory}.py`, `composition/runtime_factory.py` | готово (технический) | `test_runtime_graph.py` (19) |
| Checkpointing | LangGraph `InMemorySaver`, изоляция по `thread_id` | `platform/checkpointing/in_memory.py` | готово (только in-memory) | `test_runtime_graph.py`, `test_platform_foundation.py` |
| Model Gateway + Fake | offline-адаптер, capability gate, сценарные ошибки | `platform/models/fake_model_adapter.py` | готово (fake) | `test_platform_foundation.py` |
| Observability | JSONL, словарь событий, redaction, correlation id | `platform/observability/logging.py`, `application/ports/logging.py`, `application/services/redaction.py` | готово | `test_observability.py` (21) |
| Health checks | 8 проверок со статусами PASS/WARNING/FAILED/NOT_CONFIGURED | `application/services/health_check.py` | готово | `test_platform_foundation.py`, `test_cli_platform.py` |
| Интеграционные сценарии A–F | сквозные проверки | `tests/integration/test_scenarios.py` | готово | 12 тестов |

---

## 3. Application Bootstrap и Config Manager

**Bootstrap** (`app.py`) выполняет строго заданный порядок: загрузить и провалидировать
настройки → создать логгер с redaction → создать адаптеры (artifact store, run repository,
checkpoint provider, fake model gateway) → создать сервисы (RunManager, ArtifactService) →
создать `RuntimeFactory` → вернуть `ApplicationContext`. `close()` закрывает ресурсы в
обратном порядке и идемпотентен.

Глобальных синглтонов нет: контекст — обычный объект, который передаётся в CLI, тесты и
рантайм. Bootstrap не подключается к сети и к БД: единственные касания файловой системы —
создание каталогов данных и чтение `config/*.yaml`.

Ошибка конфигурации прерывает инициализацию **до** создания графа; диагностика собирается
из типа ошибки и пути параметра и проходит через redactor, поэтому значение, похожее на
секрет, в сообщение не попадает (проверено сценарием B).

**Config Manager** (`config.py` + `ConfigurationService`) поддерживает 8 секций: `app`,
`paths`, `runtime`, `storage`, `models`, `workflow`, `privacy`, `observability`.
Приоритет: значения по умолчанию → `config/application.yaml` и `config/models.yaml` →
переменные окружения из `ENVIRONMENT_OVERRIDES`. Валидируются типы, enum-значения,
относительность путей, отсутствие `..`, обязательность `default_profile`, бюджет токенов
профиля, правило приватности, взаимозависимость `supports_json_schema` →
`supports_structured_output`, а также то, что `database_dsn_reference` — имя переменной, а
не DSN. Дополнительно сервис сканирует YAML на литеральные секретоподобные значения.

---

## 4. CLI

| Команда | Что делает | Exit |
|---|---|---|
| `research-platform --version` | имя, версия приложения, версия Python, версии langgraph/langchain-core/pydantic | 0 |
| `research-platform --help` | список команд и их назначение | 0 |
| `research-platform contracts validate --input <path>` | валидация пакета 2.0/2.1 | 0 / 1 |
| `research-platform config validate` | загрузка и валидация конфигурации, поиск секретов | 0 / 2 |
| `research-platform platform check` | 8 самопроверок со статусами | 0 / 2 / 3 |
| `research-platform agent1a workflow-smoke` | технический smoke-запуск графа | 0 / 1 / 2 / 3 |

Коды возврата: `0` успех, `1` ошибка выполнения/валидации, `2` ошибка конфигурации,
`3` недоступная инфраструктура, `4` контролируемая остановка (зарезервирован под
human-in-the-loop). Изменение относительно Итерации 0: нечитаемый JSON больше не код 2
(теперь 2 — только конфигурация), `EXIT_INPUT_ERROR` сохранён как псевдоним кода 1.

Флаги `--project-root` и `--json` принимаются как до, так и после подкоманды.

Пример `platform check`:

```text
python_version           PASS            3.13.18
package_version          PASS            0.1.0
configuration            PASS            environment=local, profiles=…
data_directories         PASS            …
artifact_store           PASS            local file store is writable
model_gateway            WARNING         profile 'fake-primary' is a FAKE adapter …
langgraph_runtime        PASS            graph compiled (CompiledStateGraph)
imports                  PASS            5 modules importable
```

`workflow-smoke` печатает `run_id`, `correlation_id`, статус, URI артефакта-трассы и
обязательную строку `SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED`.

---

## 5. Run Manager

Модель: `RunRecord` (см. [`docs/development/run_manager.md`](../development/run_manager.md)).
Добавлены `contract_version`, `current_stage`, `retry_count`, `correlation_id`, `metadata`;
`RunStage` получил значение `smoke`; `finished_at` — свойство, возвращающее `completed_at`
(имя в контракте не менялось).

Переходы (`ALLOWED_TRANSITIONS`): `created → running | failed | cancelled`;
`running → waiting_for_human | completed | completed_with_warnings | failed | cancelled`;
`waiting_for_human → running | failed | cancelled`; терминальные — без исходящих переходов.
Каждый переход проверяется программно до записи; нарушение — `InvalidRunTransitionError`.

Операции сервиса: `start_run`, `get_run`, `list_runs`, `update_stage`, `complete_stage`,
`fail_stage`, `record_error`, `record_retry`, `request_human_review`, `resume_run`,
`complete_run`, `fail_run`, `cancel_run`. Каждая: проверка существования → проверка
перехода → новая неизменяемая запись → сохранение → событие в журнал → возврат записи.

`RunRepositoryPort`: `create`, `get_by_id`, `update`, `exists`, `list_by_status`, `close`.
`InMemoryRunRepository` — только для тестов и локальной демонстрации: данные исчезают с
процессом, `is_durable = False`, потокобезопасен, отдаёт копии, запрещает дубликаты
`run_id` и переписывание терминальных записей. JSON-хранилище как замена PostgreSQL не
создавалось.

---

## 6. Artifact Store

Локальный файловый адаптер (`platform/artifact_store/`), логическая адресация
`artifact://documents/<document_id>/runs/<run_id>/<artifact_type>/<artifact_id>`.

Структура: `<root>/sha256/<aa>/<artifact_id><ext>`, `<root>/metadata/<artifact_id>.json`,
`<root>/tmp/`. Корень задаётся конфигурацией (`paths.data_root` + `paths.artifact_subdir`) и
исключён в `.gitignore`.

Запись: staging-файл в `tmp/` на той же ФС → запись и `fsync` → проверка размера →
исключительная публикация (`os.link`, при недоступности — создание с `xb`) → проверка
размера опубликованного файла → побочный JSON метаданных → возврат метаданных. При ошибке
staging-файл удаляется; объект никогда не остаётся «выглядящим завершённым».

Неизменяемость: повтор с идентичным содержимым идемпотентен; другое содержимое под тем же
`artifact_id` → `ArtifactConflictError`, исходный файл не меняется. Целостность: SHA-256
считает хранилище, проверка выполняется при чтении и отдельной операцией.

Отдельно обработан сбой между публикацией объекта и записью метаданных: оставшийся без
sidecar объект **усыновляется** при повторе с тем же содержимым (метаданные дописываются,
артефакт становится видимым), а при другом содержимом — отвергается конфликтом. Без этого
сбой в узком окне делал бы повтор операции невозможным.

Безопасность путей: `artifact_id`/`document_id`/`run_id` проверяются по шаблонам, каждый
путь разрешается и проверяется на принадлежность корню; `..`, абсолютные пути, пробелы,
точки, не-ASCII и слишком длинные идентификаторы отвергаются.

**Отклонение от ТЗ (осознанное):** `document_id` и `run_id` сделаны обязательными, а не
«при наличии»: опубликованный контракт артефактного URI адресует артефакт парой
«документ + запуск», и хранилище не изобретает вторую схему адресации. Зафиксировано в
[`artifact_store.md`](../development/artifact_store.md).

---

## 7. LangGraph Runtime

Граф: `START → initialize_run → validate_runtime_state → execute_smoke_step → complete_run → END`.

- `initialize_run` — проверяет минимальные входы, создаёт Run через RunManager, фиксирует
  `run_id`/`correlation_id`;
- `validate_runtime_state` — `validate_state` + сверка состояния с сохранённым запуском;
- `execute_smoke_step` — детерминированный технический шаг: необязательный вызов
  fake-модели с ограниченными повторами и запись трассы в Artifact Store;
- `complete_run` — завершает запуск и пишет терминальное событие.

Состояние — `Agent1AState` (компактное, JSON-сериализуемое), добавлены `correlation_id` и
`technical_trace_ref`. Ни PDF, ни текст, ни embeddings, ни секреты в состояние не попадают
(`validate_state` проверяет и это).

Зависимости передаются явно через `GraphDependencies`; узлы не создают подключений и не
читают пути. Ошибка узла нормализуется в `RunError`, запуск помечается `failed`, исключение
пробрасывается — граф не объявляет успех после критической ошибки. Повторы ограничены
`profile.max_retries`, отражаются в `retry_count` и событии `retry_started`.

---

## 8. Checkpointing

Реализован `InMemoryCheckpointProvider` поверх LangGraph `InMemorySaver`:
`create_saver()`, `thread_id_for_run(run_id) → thread_<run_id>`, `close()`. `thread_id`
выводится из `run_id` один-к-одному, поэтому состояние и checkpoint двух запусков не
пересекаются (проверено тестами изоляции).

**Ограничения:** состояние живёт только в процессе; восстановления после перезапуска нет;
durable checkpointing относится к storage/runtime-подэтапу и требует решения `OI-006`.
Формат checkpoint не изобретался — используется механизм LangGraph.

---

## 9. Model Gateway

Интерфейс не менялся (Итерация 0). Добавлен fake-адаптер в пакете
(`platform/models/fake_model_adapter.py`): `FakeModelGateway`, `FakeChatModel`,
`FakeEmbeddingModel`, `FakeReranker`.

Свойства: полностью offline, детерминированный, помечен `is_fake = True`, поддерживает
сценарные ошибки (`timeout`, `rate_limit`, `invalid_response`), пишет
`fake_model_request_completed` (никогда — `model_request_completed`), не использует ключи и
не возвращает научного содержания. Перед вызовом structured output проверяется capability:
если профиль её не поддерживает, операция падает до обращения к модели
(`ModelUnsupportedCapabilityError`).

Реальный адаптер не реализован: провайдер не выбран (`OI-001…OI-004`). Настоящие лимиты
DeepSeek не выдумывались: в example-конфигурации стоит fake-профиль и профиль-заглушка с
`UNRESOLVED_CHOOSE_PROVIDER`.

---

## 10. Observability

Формат: JSON Lines (или `text` по настройке). Обязательные поля: `timestamp`, `level`,
`event`, `application`; при наличии — `run_id`, `correlation_id`, `document_id`, `agent_id`,
`stage`, `error_code`.

Словарь событий закрыт (`EVENT_NAMES`): `application_started`, `application_shutdown`,
`config_loaded`, `config_validation_failed`, `check_completed`, `run_created`, `run_started`,
`stage_started`, `stage_completed`, `stage_failed`, `model_request_started`,
`model_request_completed`, `fake_model_request_completed`, `artifact_created`,
`artifact_validation_failed`, `run_waiting_for_human`, `run_completed`, `run_failed`,
`retry_started`, `runtime_error`. Неизвестное имя события — ошибка, а не молчаливая запись.

Redaction — централизованная политика (`application/services/redaction.py`): маскируются
ключи провайдеров, `Bearer`-заголовки, пары `api_key=…`/`token: …`/`password=…`, учётные
данные в URI и длинные base64-подобные строки; поля с «секретными» именами заменяются
целиком; рекурсивная обработка вложенных словарей. Redaction применяется к каждой записи
журнала и к диагностике конфигурации.

Токены и стоимость: поля есть в структуре `TokenUsage`, но остаются `null`, пока нет
реального провайдера. Фиктивная статистика не создаётся; fake-ответ маркирует синтетические
значения отдельным событием.

---

## 11. Тестирование

Все команды выполнены в `D:\MultiAgentPipeline\research-agent-platform`:
Windows 10 Pro 22H2 (`10.0.19045`) x64, CPython `3.13.18`, `uv 0.13.7`.

| Команда | Exit | Результат |
|---|---|---|
| `uv lock --check` | 0 | lock актуален |
| `uv run pytest -q` | 0 | **526 passed**, 0 failed, 0 skipped |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 141 файл отформатирован |
| `uv run ty check` | 0 | All checks passed |
| `uv run python tools/export_schemas.py --check` | 0 | 7 схем актуальны |
| `uv run research-platform --version` | 0 | имя, версия, Python, пакеты |
| `uv run research-platform --help` | 0 | 5 команд |
| `uv run research-platform config validate` | 0 | `valid: environment=local, profiles=…` |
| `uv run research-platform platform check` | 0 | PASS/WARNING по 8 проверкам |
| `uv run research-platform agent1a workflow-smoke` | 0 | `completed_with_warnings`, артефакт-трасса |

Тестовые категории: конфигурация, bootstrap, CLI, Run Manager, repository, artifact store,
построение графа, GraphState, жизненный цикл workflow, checkpointer, fake-адаптеры,
нормализация ошибок, политика повторов, журналирование, redaction, границы зависимостей,
интеграционные сценарии A–F.

**Ограничение среды.** В песочнице DSH дочерние процессы не могут создавать временные
каталоги, поэтому тесты запускались с `--basetemp` вне репозитория. На результат это не
влияет; вне песочницы достаточно:

```powershell
uv run pytest -q
```

---

## 12. Архитектурная проверка

`tests/unit/test_import_boundaries.py` — AST-скан с разрешением относительных импортов:

| Правило | Результат |
|---|---|
| `domain` не импортирует `application`/`platform`/`agents`/`config`/`cli`/инфраструктуру | проходит |
| `domain` не знает о файловой системе | проходит |
| `application` не импортирует `platform`/`agents`/`cli`/LangGraph/SQLAlchemy/Docling/SDK | проходит |
| `application` может импортировать `config` (типизированные настройки, без подключений) — задокументированное исключение | проходит |
| `agents` не импортируют `platform` | проходит |
| Agent 1A и Agent 1B не импортируют друг друга | проходит |
| CLI не содержит бизнес-логики | проходит (только разбор аргументов и вызовы сервисов) |

Найдено и исправлено в ходе итерации:

1. `application` импортировал `platform.observability.logging` → введён порт
   `application/ports/logging.py`; адаптер остался в `platform`.
2. `application` импортировал `platform.observability.redaction` → политика перенесена в
   `application/services/redaction.py`, адаптерный путь оставлен как совместимый шим.
3. `application/runtime/runtime_factory.py` импортировал конкретного агента → сборка
   вынесена в `composition/` (композиционный корень). Это осознанное отклонение от эскиза
   каталогов в ТЗ: правило «application не импортирует конкретные реализации» важнее
   расположения файла.

Запрещённые Итерацией 1 возможности не появились: нет парсинга, OCR, RAG, embeddings,
retrieval, резолвера, реального провайдера, SQLAlchemy/ORM, Alembic, PostgreSQL, FastAPI,
Web UI, Docker, распределённого исполнения.

---

## 13. Изменённые файлы

### Создано — код

`app.py`; `composition/{__init__,runtime_factory}.py`;
`application/ports/{checkpoint,logging,run_repository,artifact_store}.py`;
`application/services/{run_manager,artifact_service,configuration_service,health_check,redaction}.py`;
`platform/artifact_store/{paths,metadata,local_file_store}.py`;
`platform/persistence/in_memory_run_repository.py`;
`platform/checkpointing/in_memory.py`;
`platform/models/fake_model_adapter.py`;
`platform/observability/logging.py`;
`agents/agent_1a/graph/{state,nodes,workflow,factory}.py`.

### Изменено — код

`cli.py` (5 команд, коды 0–4, `--json`, `--project-root`), `config.py` (секции `paths` и
`runtime`, расширенный профиль модели, `discover_settings_paths`, `load_settings_from_project`),
`domain/runs/models.py` (`RunRecord` + переходы + генераторы идентификаторов +
`RunStage.SMOKE`), `domain/runs/__init__.py`, `agents/agent_1a/contracts/graph_state.py`
(`correlation_id`, `technical_trace_ref`, `initial_state` с `source_ref=None`),
`application/ports/__init__.py`, `platform/observability/redaction.py` (шим).

### Создано — тесты

`tests/unit/test_run_manager.py` (25), `tests/unit/test_artifact_store.py` (33),
`tests/unit/test_observability.py` (21), `tests/unit/test_runtime_graph.py` (19),
`tests/unit/test_platform_foundation.py` (27), `tests/contract/test_cli_platform.py` (15),
`tests/integration/test_scenarios.py` (10), `tests/fixtures/logging.py`.

### Изменено — тесты

`tests/contract/test_cli.py` (новый набор команд, версия, коды возврата),
`tests/unit/test_config.py` (новые env-overrides), `tests/unit/test_import_boundaries.py`
(задокументированное исключение для `config`).

### Конфигурация и прочее

`config/application.example.yaml`, `config/models.example.yaml` (fake-профиль + секции
`paths`/`runtime`), `.gitignore` (`data/`).

### Документация

`docs/architecture/architecture_overview.md` (v1.2), `docs/contracts/run_and_artifact_contracts.md`,
`docs/development/run_manager.md`, `artifact_store.md`, `langgraph_runtime.md`,
`observability.md`, `configuration.md`, `cli.md`, `fake_adapters.md`, `limitations.md`,
`README.md` (раздел «Изменения в Итерации 1»), этот отчёт.

**Не изменялись:** контрактные схемы 2.0/1.0 (заморожены, SHA-256 совпадают), фикстуры
замороженных версий, ADR-001…ADR-010, `docs/decisions/open_items.md` (открытые вопросы
остались открытыми), legacy-проект `D:\MultiAgentPipeline\Agent1\Agent1A`.

---

## 14. Ограничения

| Область | Ограничение |
|---|---|
| Парсинг | нет PDF/TXT-парсинга, Docling-пайплайна, PyMuPDF-извлечения, обработки таблиц и рисунков |
| OCR | не реализован, движки не выбраны (`OI-008`) |
| RAG | нет chunking, embeddings, dense/lexical/hybrid retrieval, reranking, индексов |
| Evidence | нет резолвера и извлечения полей; контракт и правила готовы |
| Провайдер | реальный адаптер отсутствует (`OI-001…OI-004`), API-вызовов не было |
| Хранение | PostgreSQL, ORM, миграции не создавались; run repository in-memory (`OI-006`) |
| Checkpointing | только in-memory, без восстановления после перезапуска процесса |
| API/UI | нет FastAPI, веб-интерфейса, очередей, распределённого исполнения |
| Agent 1B | workflow не реализован, только контракты |
| Runtime | один документ на запуск, синхронное исполнение |
| Среда | тесты в песочнице DSH требуют `--basetemp` вне репозитория |

Отдельно: `agent1a workflow-smoke` — **техническая** проверка каркаса. Она не обрабатывает
научную статью, не создаёт карточку и не является демонстрацией качества извлечения.

---

## 15. Готовность к следующей итерации

Итерация 2 (Document Parsing / Quality Gate) может начинаться: платформенная основа
исполняется, и все её точки расширения проверены тестами.

| Условие для Итерации 2 | Статус |
|---|---|
| Единая точка инициализации с явными зависимостями | выполнено (`app.py`) |
| Типизированная конфигурация с валидацией и защитой секретов | выполнено (`config.py`) |
| Хранилище артефактов с неизменяемостью и целостностью | выполнено (LocalFileArtifactStore) |
| Управление запусками и статусами | выполнено (RunManager + InMemory) |
| Runtime-граф, в который добавляются parsing/quality-узлы | выполнено (4 узла, расширяемо) |
| Единая обработка ошибок и политика повторов | выполнено |
| Журналирование с correlation id и redaction | выполнено |
| Команды для ручной проверки состояния платформы | выполнено (`platform check`, `workflow-smoke`) |
| Отсутствие запрещённых возможностей и фиктивных результатов | выполнено |
| Регрессия контрактов Итерации 0 | 373 теста Итерации 0 сохранены и проходят (два теста CLI переписаны под новый набор команд, а не удалены) |

Что нужно от владельца до работ по парсингу и storage:

1. `OI-008` — движки OCR и языковые пакеты RU/EN (влияет на качество PDF и на выбор
   fallback-стратегии);
2. `OI-006` — политика запуска локальной PostgreSQL (если storage-подэтап стартует в
   Итерации 2);
3. `OI-001…OI-004` — параметры провайдера (нужны не раньше, чем появится реальный адаптер).

Остальные открытые вопросы (`OI-005`, `OI-007`, `OI-009`–`OI-011`) на Итерацию 2 не влияют.
