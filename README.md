# research-agent-platform

Каркас мультиагентной платформы анализа научных публикаций — **контракты и архитектура
перед Итерацией 1**.

**Что это.** Модульный монолит на Python, в котором порядок обработки статьи определяют код
и LangGraph, а LLM выполняет только узкие задачи извлечения с типизированным входом и
выходом. Agent 1A извлекает карточку статьи, подтверждения и отчёт о покрытии поиска и
формирует неизменяемый пакет; Agent 1B проверяет его независимо по первоисточнику и
выносит научный вердикт.

**Что это НЕ есть.** Здесь пока нет обработки статей: нет парсинга PDF, OCR, RAG, индексов,
вызовов LLM, БД и веб-интерфейса. Установка и импорт парсинговых библиотек проверены,
прикладная обработка документов не реализована.

---

## 1. Быстрый старт

Требуется [`uv`](https://docs.astral.sh/uv/) и Python `3.13.18` (uv умеет поставить его сам).

```powershell
uv python install 3.13.18
uv sync --locked

uv run research-platform --help
uv run research-platform --version
uv run research-platform contracts validate --input tests\fixtures\agent_1a_package_v2_1_valid.json
```

Ожидаемый вывод последней команды:

```text
valid: tests\fixtures\agent_1a_package_v2_1_valid.json satisfies Agent1AExtractionPackageV21 (contract 2.1)
```

Исторический пакет 2.0 тоже валидируется своим контрактом:

```powershell
uv run research-platform contracts validate --input tests\fixtures\agent_1a_package_v2_valid.json
# valid: … satisfies Agent1AExtractionPackageV2 (contract 2.0)
```

Проверки качества:

```powershell
uv lock --check
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run python tools/export_schemas.py --check
```

Точные результаты последнего прогона — в
[`docs/reports/pre_iteration_1_architecture_closure.md`](docs/reports/pre_iteration_1_architecture_closure.md).

---

## 2. Команды CLI (и только они)

| Команда | Назначение | Коды возврата |
|---|---|---|
| `research-platform --help` | список команд | 0 |
| `research-platform --version` | версия пакета | 0 |
| `research-platform contracts validate --input <path>` | валидация JSON по контракту, выбранному из `contract_version` | 0 — валиден, 1 — нарушение контракта или неизвестная версия, 2 — файл не прочитан / не JSON |

Команд `process`, `run-agent`, `parse`, `index` нет: заглушка, имитирующая обработку статьи,
была бы хуже отсутствующей команды.

---

## 3. Структура репозитория

```text
research-agent-platform/
├── pyproject.toml · uv.lock · .python-version      воспроизводимая среда (uv закреплён)
├── .env.example · .gitignore · README.md
├── src/research_platform/
│   ├── cli.py                    тонкий entry point (контракты, диспетчеризация по версии)
│   ├── config.py                 типизированные настройки, ссылки на секреты
│   ├── domain/
│   │   ├── base.py               extra="forbid", frozen, UTC
│   │   ├── documents/            DocumentManifest
│   │   ├── cards/                карточка: 9 разделов, 27 полей, 4 статуса
│   │   ├── evidence/             контракт 2.1 + замороженный 2.0 + правила ledger
│   │   ├── coverage/             Coverage Trail и правила not_found
│   │   ├── runs/                 RetrievalMode/RetrievalStrategy, RunMetadata, артефакты
│   │   └── validation/           техническая валидация + семантика-плейсхолдер
│   ├── application/ports/        Protocols + provider-neutral Model Gateway
│   ├── platform/                 дом адаптеров (contracts реализован, остальное — заглушки)
│   └── agents/
│       ├── agent_1a/             contracts/ (пакет 2.1, 2.0 frozen, GraphState, адаптер)
│       │                         graph/ (пусто — Итерация 3), README.md
│       └── agent_1b/             contracts/ (вход 1.1/1.0, отчёт 1.1/1.0), README.md
├── contracts/
│   ├── legacy/agent_1a_v1/       замороженная схема v1.0 + SHA-256
│   ├── agent_1a/v2/              пакет 2.0 — заморожен (+ .sha256)
│   ├── agent_1a/v2.1/            пакет 2.1 + схема кандидатов evidence (текущие)
│   ├── agent_1b/v1/              вход и отчёт 1.0 — заморожены (+ .sha256)
│   └── agent_1b/v1.1/            вход и отчёт 1.1 (текущие)
├── config/                       application.example.yaml, models.example.yaml
├── docs/                         architecture, decisions, contracts, migration, development, reports
├── tests/                        unit, contract, fixtures (365 тестов)
├── tools/                        export_schemas.py, generate_fixtures.py
└── evals/                        место для gold-набора (Итерация 6)
```

---

## 4. Направления зависимостей

```text
cli → agents / application → domain
platform → application.ports / domain      (обратное запрещено)
agents/agent_1a ↔ agents/agent_1b          (взаимные импорты запрещены)
```

Проверяется тестами `tests/unit/test_import_boundaries.py` (AST-скан, включая
относительные импорты): `domain` не импортирует инфраструктуру и не знает про файловую
систему, `application` не импортирует `platform`. Подробнее —
[`docs/architecture/architecture_overview.md`](docs/architecture/architecture_overview.md).

---

## 5. Контракты

| Контракт | Версия | Документ | Схема |
|---|---|---|---|
| Пакет Agent 1A → 1B | **2.1** | [agent_1a_v2.md](docs/contracts/agent_1a_v2.md) | `contracts/agent_1a/v2.1/extraction_package.schema.json` |
| Кандидаты evidence | **2.1** | [evidence_contract.md](docs/contracts/evidence_contract.md) | `contracts/agent_1a/v2.1/evidence_candidates.schema.json` |
| Coverage Trail | **2.1** | [coverage_trail.md](docs/contracts/coverage_trail.md) | входит в схему пакета 2.1 |
| Вход Agent 1B | **1.1** | [agent_1b_input_contract.md](docs/contracts/agent_1b_input_contract.md) | `contracts/agent_1b/v1.1/input_package.schema.json` |
| Отчёт Agent 1B | **1.1** | [agent_1b_input_contract.md](docs/contracts/agent_1b_input_contract.md) | `contracts/agent_1b/v1.1/verification_report.schema.json` |
| GraphState | — | [agent_1a_graph_state.md](docs/contracts/agent_1a_graph_state.md) | — (TypedDict) |
| Model Gateway | — | [model_gateway.md](docs/contracts/model_gateway.md) | — |
| Run / артефакты / конфигурация | — | [run_and_artifact_contracts.md](docs/contracts/run_and_artifact_contracts.md) | — |
| Legacy-карточка v1.0 | 1.0 | [v1_to_v2_mapping.md](docs/migration/v1_to_v2_mapping.md) | `contracts/legacy/agent_1a_v1/card.schema.json` |

Источник истины — Pydantic-модели; JSON Schema **генерируется** (`tools/export_schemas.py`)
и проверяется побайтово. Замороженные схемы дополнительно защищены контрольными суммами.

Ключевые гарантии:

* пакет неизменяем (`frozen=True`) и закрыт (`extra="forbid"`);
* каждое `found` имеет минимум одну ссылку на evidence, и ссылка разрешается в ledger;
* модель предлагает только цитату и подсказки: у `EvidenceCandidate` нет координат, хеша,
  статуса разрешения и номера вхождения; `fuzzy` никогда не становится `resolved`;
* `not_found` допустим только при `coverage_status=complete` и `not_found_eligible`, а для
  `rag`/`hybrid` — только при реально выполненном полнотекстовом fallback;
* `scientific_review_status` существует только в отчёте Agent 1B;
* Agent 1B получает пакет только вместе со ссылкой на неизменяемый оригинал и не может
  изменить карточку;
* абсолютные пути Windows не являются частью публичного контракта.

---

## 6. Конфигурация и секреты

```powershell
Copy-Item config\application.example.yaml config\application.yaml
Copy-Item config\models.example.yaml     config\models.yaml
Copy-Item .env.example                  .env
```

В конфигурации **нет значений секретов** — только имена переменных окружения
(`api_key_env`, `database_dsn_reference`). Значение, похожее на DSN, отвергается
валидацией: закоммиченный DSN выдаёт пароль. `.env` в git не попадает.

---

## 7. Ограничения текущего состояния

Реализовано в Итерации 1: единый bootstrap, типизированная конфигурация, CLI (5 команд),
Run Manager с контролем переходов, локальный Artifact Store, технический LangGraph-граф
Agent 1A, in-memory checkpointing, fake-адаптеры модели, структурный журнал с redaction.

По-прежнему **не** реализовано: парсинг PDF/TXT, OCR, quality gate, chunking, embeddings,
retrieval, reranking, Evidence Resolver, извлечение полей карточки, реальный адаптер
провайдера, PostgreSQL и миграции, FastAPI, Docker, Agent 1B workflow, durable
checkpointing, обработка реальных статей. Полный список —
[`docs/development/limitations.md`](docs/development/limitations.md).

Установка и импорт `docling 2.150.0` / `pymupdf 2.1` на целевой Windows/CPython 3.13
проверены; качество парсинга и OCR — нет.

Открытые решения заказчика — [`docs/decisions/open_items.md`](docs/decisions/open_items.md)
(провайдер и лимиты модели, OCR-движки, embedding-модель, reranker, политика локальной БД,
канал ручной проверки, gold-набор). Ни одно не закрыто «от имени заказчика».

---

## 8. Лицензия и данные

Репозиторий приватный. PDF научных статей, персональные данные и API-ключи в git не
добавляются (`.gitignore`). Legacy-проект `D:\MultiAgentPipeline\Agent1\Agent1A`
используется только для чтения.

---

## Изменения в Итерации 1

**Назначение этапа — платформенный каркас, а не обработка статей.** Итерация 1 добавила
исполняемый скелет: типизированную конфигурацию с env-override, жизненный цикл запусков,
локальное неизменяемое хранилище артефактов, структурный журнал с редактированием секретов,
технический LangGraph-граф Agent 1A и само-проверки платформы. Парсинга PDF, OCR, retrieval,
извлечения полей, реального провайдера и БД по-прежнему нет — полный список в
[`docs/development/limitations.md`](docs/development/limitations.md).

### Новые команды CLI

| Команда | Назначение | Коды возврата |
|---|---|---|
| `research-platform config validate` | загрузка YAML, env-override, проверка типов, значений, путей и секретов | 0 — валидна; 2 — ошибка конфигурации |
| `research-platform platform check` | восемь само-проверок платформы | 0 — нет `FAILED`; 2 — ошибка сборки контекста; 3 — провал проверок |
| `research-platform agent1a workflow-smoke` | один прогон технического графа Agent 1A | 0 — `completed`/`completed_with_warnings`; 1 — исключение или иной статус; 2 — ошибка конфигурации |

Прежние `--help`, `--version` и `contracts validate` сохранены. Раздел 2 выше описывает
состояние Итерации 0 (в частности, код 2 для нечитаемого входа больше не используется: этот
случай возвращает 1, а 2 означает ошибку конфигурации). Актуальные команды, флаги
`--project-root`/`--json` и коды возврата — в [`docs/development/cli.md`](docs/development/cli.md).

### `platform check`

Восемь проверок: `python_version`, `package_version`, `configuration`, `data_directories`,
`artifact_store`, `model_gateway`, `langgraph_runtime`, `imports`. Каждая получает один из
статусов:

| Статус | Значение |
|---|---|
| `PASS` | проверка пройдена |
| `WARNING` | работает, но с оговоркой: fake-профиль модели помечается именно так |
| `FAILED` | платформа непригодна к работе (нерабочее хранилище, неимпортируемый модуль, каталог нельзя создать) |
| `NOT_CONFIGURED` | не настроено; это не провал — платформа работает на значениях по умолчанию |

Команда не подключается к PostgreSQL и к провайдеру модели, не создаёт запуск и не читает
документы. Побочные эффекты: создаются каталоги `data/` и `logs/`, пишутся и удаляются пробные
файлы.

### `agent1a workflow-smoke`

Выполняет граф `initialize_run → validate_runtime_state → execute_smoke_step → complete_run` на
синтетическом состоянии, создаёт запуск в памяти процесса и артефакт трассы в
`data/artifacts/`, при наличии профиля вызывает fake-модель (`--no-model` отключает вызов).
Научный документ не обрабатывается и карточка не формируется.

Обязательная строка вывода в любом режиме (и в `--json` как поле `note`):

```text
SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED
```

### Документация этапа

- отчёт об итерации — [`docs/iteration_reports/iteration_1_report.md`](docs/iteration_reports/iteration_1_report.md);
- [`run_manager.md`](docs/development/run_manager.md) — жизненный цикл запуска и репозиторий;
- [`langgraph_runtime.md`](docs/development/langgraph_runtime.md) — граф, состояние, отказы, повторы;
- [`artifact_store.md`](docs/development/artifact_store.md) — раскладка, атомарность, целостность;
- [`observability.md`](docs/development/observability.md) — JSONL, словарь событий, редакция;
- [`configuration.md`](docs/development/configuration.md) — восемь секций и правила валидации;
- [`cli.md`](docs/development/cli.md) — команды, коды и JSON-нагрузки;
- [`fake_adapters.md`](docs/development/fake_adapters.md) — fake-адаптеры и их границы;
- [`limitations.md`](docs/development/limitations.md) — чего в Итерации 1 нет;
- [`testing.md`](docs/development/testing.md) — команды pytest, временные каталоги и ограничения среды.

Контрольные цифры: **586 тестов пройдено, 0 провалено, 0 пропущено** (после технического
закрытия: три пространства адресации артефактов, атомарный sidecar метаданных); Python `3.13.18`,
uv `0.13.7`, langgraph `1.2.20`, pydantic `2.14.3`, ruff/ty/экспорт схем — без замечаний.
Итог закрытия — [`docs/reports/post_iteration_1_technical_closure.md`](docs/reports/post_iteration_1_technical_closure.md).

---

## Итерация 2 — обработка документов

Итерация 2 добавила конвейер обработки **одного** документа: приём (limits, SHA-256, стабильный
`document_id`), разбор (Docling для цифровых PDF, блочный разбор для TXT), канонический текст с
символьными диапазонами, детерминированный Quality Gate и неизменяемые артефакты на каждый запуск.
LLM в конвейере не участвует. OCR **не реализован**: сканированные страницы только детектируются,
а документ без текста вообще завершается ошибкой до Quality Gate (код 1), а не управляемой
остановкой.

### Две команды

```powershell
# PDF: группа parsing обязательна — Docling и PyMuPDF объявлены именно в ней
uv run --group parsing research-platform document process --input D:\papers\article.pdf

# TXT: группа parsing не нужна
uv run research-platform document process --input D:\notes\note.txt

# отчёт о качестве по сохранённым артефактам запуска
uv run research-platform document report --run-id run_20261114_120000_a1b2c3
```

Обе команды принимают `--project-root PATH` (корень, относительно которого читаются `config/` и
`data/`) и `--json`; `document process` дополнительно принимает `--dry-run`.

**Почему PDF требует `--group parsing`.** `docling` и `pymupdf` объявлены в группе зависимостей
`parsing`, а не в основных зависимостях пакета. Без `--group parsing` окружение синхронизируется без
них, и разбор PDF невозможен. Обратная сторона: `uv run` без этой группы может удалить
установленные пакеты группы `parsing` — после такого запуска нужен `uv sync --group parsing`.

### Коды возврата `document process`

| Код | Значение |
|---|---|
| 0 | `passed` или `passed_with_warnings` — документ готов к дальнейшим этапам |
| 1 | провал: ошибка входа/приёма/разбора или вердикт `failed` |
| 2 | ошибка конфигурации |
| 3 | инфраструктура или парсер недоступны (объявлено; в текущей реализации путь практически недостижим) |
| 4 | управляемая остановка: `requires_ocr` или `needs_human_review` |

### Что уже проверено, а что нет

Проверено: TXT-файл → `passed`, код 0; одностраничный цифровой PDF через настоящий Docling →
`passed`, код 0; `document report` для запуска в том же корне проекта.

Не проверено: сканированные PDF (нет OCR-движка), многостраничные и двухколоночные документы сверх
единичной пробы, качество извлечения таблиц на реальных статьях. Модельные веса Docling лежат в
`%USERPROFILE%\.cache\huggingface` (≈505 MB по замеру каталогов кеша), вне репозитория; лицензии
весов не проверены.

Полный отчёт — [`docs/iteration_reports/iteration_2_report.md`](docs/iteration_reports/iteration_2_report.md).
Документация конвейера:
[`docs/contracts/document_manifest.md`](docs/contracts/document_manifest.md),
[`docs/contracts/parsed_document.md`](docs/contracts/parsed_document.md),
[`docs/contracts/document_provenance.md`](docs/contracts/document_provenance.md),
[`docs/contracts/quality_report.md`](docs/contracts/quality_report.md),
[`docs/development/document_ingestion.md`](docs/development/document_ingestion.md),
[`docs/development/document_parsing.md`](docs/development/document_parsing.md),
[`docs/development/quality_gate.md`](docs/development/quality_gate.md),
[`docs/development/document_parser_configuration.md`](docs/development/document_parser_configuration.md),
[`docs/development/ocr_engine_evaluation.md`](docs/development/ocr_engine_evaluation.md),
[`docs/development/limitations.md`](docs/development/limitations.md).
