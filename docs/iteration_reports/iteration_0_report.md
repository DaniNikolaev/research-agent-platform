# Отчёт DSH по Итерации 0

**Проект:** `research-agent-platform` · **Дата:** 2026-11-11 · **Основание:** `TZ_DSH_Iteration_0_v1.0` (ADD v1.0) · **Исполнитель:** DSH

> **Исторический документ.** Отчёт отражает состояние на 11.11.2026 (контракты 2.0/1.0,
> 246 тестов). После него проведено закрытие архитектурных вопросов: контракты 2.1/1.1,
> Coverage Trail, разделение `RetrievalMode`/`RetrievalStrategy`, 373 теста. Актуальное
> состояние — [`docs/reports/pre_iteration_1_architecture_closure.md`](../reports/pre_iteration_1_architecture_closure.md),
> готовность к Итерации 1 — [`docs/development/iteration_1_readiness.md`](../development/iteration_1_readiness.md).

---

## 1. Executive summary

**Итог: `PASS`.**

Все обязательные артефакты Итерации 0 созданы; все проверки, которые можно было
запустить, запущены и пройдены (см. §8). Функциональная реализация Agent 1A, парсинг,
RAG, вызовы LLM, БД и API не создавались — это соответствует границам итерации.

Три пункта требуют **подтверждения заказчиком** (не блокируют Итерацию 1, вынесены в §11
и §12):

1. режим `HYBRID` из ADD §10.2 не входит в `RetrievalMode` (ТЗ 0.4.2 перечисляет только
   `full_context` / `rag` / `not_applicable`) — реализовано по ТЗ, вопрос зафиксирован в
   ADR-007;
2. прочтение противоречия ТЗ 0.5 про «текстовый фрагмент/диапазон» кандидата evidence
   (ADR-008) — предлагается подтвердить;
3. размещение `scientific_review_status` только в отчёте Agent 1B, а не в пакете
   Agent 1A (ADR-004, §5.4 этого отчёта).

Один инцидент окружения (права на целевую папку) произошёл и устранён — §2.3.

---

## 2. Окружение

### 2.1. Фактические значения

| Параметр | Значение |
|---|---|
| ОС | Windows 10 Pro 22H2, `10.0.19045`, x64 |
| Python | `3.13.18` (managed CPython от uv, `x86_64-pc-windows-msvc`) |
| uv | `0.13.7` (`316c827e0`, 2026-11-03) |
| Целевой каталог | `D:\MultiAgentPipeline\research-agent-platform` |
| Legacy-проект (только чтение) | `D:\MultiAgentPipeline\Agent1\Agent1A` |
| Пакет | `research-agent-platform 0.1.0` |
| Lock-файл | `uv.lock`, 168 пакетов |

В системном `PATH` нужной версии Python **не было**: доступны 3.14, 3.11, 3.10, 3.9.
`3.13.18` установлена как managed-интерпретация uv и зафиксирована в `.python-version`;
подмены версии не произошло.

### 2.2. Что установлено вне репозитория (не является артефактом поставки)

`D:\MultiAgentPipeline\.tooling\` — окружение инструментов: venv с `uv 0.13.7`,
managed-интерпретатор `cpython-3.13.18`, кэш uv, а также служебные материалы этой сессии
(расшифровка предыдущей аудиторской сессии, логи проверок, тестовый «сломанный» пакет).
Удалять не обязательно; на поставку не влияет.

### 2.3. Инцидент окружения (устранён) — важно для заказчика

**Симптом.** Папка `D:\MultiAgentPipeline\research-agent-platform` (существовала до
начала работы и была **пуста**) отказывала в записи **любому обычному процессу**:
`uv lock` падал с `os error 5`, `python.exe` и `pytest` — с `PermissionError`, даже когда
у текущей учётной записи было явное право Full Control и сессия работала в режиме полного
доступа. Копия того же дерева в соседней папке писалась нормально, то есть блокировка
была привязана именно к этому пути (признак защитной метки самого каталога).

**Что сделано.**

1. Диагностическим скриптом DSH проверены и исправлены права на `D:\MultiAgentPipeline`
   (добавлено полное право для учётной записи; команда отмены напечатана и сохранена в
   `D:\MultiAgentPipeline\.dsh-acl-recovery\`).
2. Так как после этого запись в целевую папку всё равно была запрещена, **пустая**
   папка-заглушка переименована и убрана в
   `D:\MultiAgentPipeline\.trash-it0\research-agent-platform.preexisting`
   (в ней находились только 6 только что созданных служебных файлов этого же проекта),
   после чего каталог создан заново по тому же пути.
3. Новый каталог пишется всеми инструментами; сборка и проверки выполнялись в нём.

**Что это означает для вас.** Если папка снова начнёт отказывать в записи, причина не в
проекте: проверьте защиту папок в антивирусе/Windows Security и добавьте исключение для
`python.exe` и `uv.exe` либо для каталога проекта. Старая пустая папка лежит в
`.trash-it0\` и может быть удалена вручную.

**Ещё одно наблюдение.** В песочнице DSH дочерние процессы не могут создавать временные
каталоги, поэтому при запуске `pytest` под песочницей часть тестов с `tmp_path` падает
(`236 passed / 10 errors`). В обычном терминале вне DSH такого ограничения нет; все
приведённые в §8 результаты получены при разовом расширении прав на конкретную команду.
`pytest` также может создавать каталог `pytest-of-<user>` в корне проекта — он
исключён из git и из ruff.

---

## 3. Legacy-аудит

Полный документ: [`docs/migration/legacy_agent1a_audit.md`](../migration/legacy_agent1a_audit.md).

| Параметр | Значение |
|---|---|
| Путь | `D:\MultiAgentPipeline\Agent1\Agent1A` |
| Кандидатов найдено | ровно один (подтверждён `scripts/process_documents.py`, `config/card_schema.json`, `prompts/agent_1a_prompt.md`) |
| Метод | только статический анализ; pipeline не запускался, артефакты не перегенерировались |
| Содержательных полей карточки | 27 в 9 разделах (подтверждено и схемой, и кодом) |
| Дефекты | D1–D9 сверены с кодом (реестр восстановлен из расшифровки предыдущей сессии) |
| Тестов | 81 (по README и расшифровке; прогон в этой итерации не повторялся) |
| Изменено в legacy | ничего |

Таблица переносимости (29 уникальных строк, классификации взаимоисключающие):
`REUSE_AS_REFERENCE` — 5, `REIMPLEMENT` — 13, `COMPATIBILITY_ONLY` — 2,
`DEPRECATE` — 8, `NEEDS_DECISION` — 1; сумма 29 совпадает с числом строк.

> **Исправление (2026-11-12).** В первой редакции отчёта были указаны значения
> 6 / 14 / 3 / 9 / 2 (сумма 34). Пересчёт по фактической таблице
> (`docs/migration/legacy_agent1a_audit.md`, §5.1) показал, что показатели считались как
> число вхождений слов в разделе и включали строку-легенду с перечнем допустимых значений
> (плюс одно вхождение на категорию). Содержание таблицы не менялось. Подробнее — в отчёте
> [`docs/reports/pre_iteration_1_architecture_closure.md`](../reports/pre_iteration_1_architecture_closure.md), §5.

Сохранено для истории и совместимости: `card_schema.json` v1.0 (побайтовая копия,
SHA-256 `0c7075d2adba9ea6f3e148f1110f995f833b87028ced9d7145c8d1e2ace8bbf1`) и одна
реальная карточка `tests/fixtures/legacy/card_v1_en_article.json`.

Что **не** переносится: оркестрация и хранение результатов через файловую систему,
выполнение ИИ-прохода силами DSH, `vendor/fetch_wheels.py`, эвристика разделов как
источник истины, лексикографическая сортировка частей (D4), цикл исправлений, не
покрывающий синтаксическую ошибку JSON (D3).

---

## 4. Архитектура

Карта модулей и правила зависимостей:
[`docs/architecture/architecture_overview.md`](../architecture/architecture_overview.md).

Реализованные слои: `domain` → `application/ports` → `platform` (адаптеры),
`agents/agent_1a` и `agents/agent_1b` поверх `domain` + `application.ports`, `cli.py` —
единственная точка входа.

Направления зависимостей проверяются **кодом**, а не ревью:
`tests/unit/test_import_boundaries.py` сканирует AST импортов, разрешает относительные
импорты, запрещает `domain` знать про инфраструктуру и файловую систему, запрещает
`application` импортировать `platform`, запрещает агентам импортировать друг друга,
проверяет единственность `cli.py`.

Осознанное отклонение от дерева ADD §21: DTO и Protocols Model Gateway размещены в
`application/ports/model_gateway/` (ADD допускает оба варианта), чтобы `platform` не мог
протечь в контракты. Зафиксировано в ADR-003.

---

## 5. Контракты

### 5.1. Версии и схемы

| Контракт | Версия | Pydantic | JSON Schema | Тест |
|---|---|---|---|---|
| Agent 1A → 1B | 2.0 | `Agent1AExtractionPackageV2` | `contracts/agent_1a/v2/extraction_package.schema.json` | `test_agent_1a_package.py` |
| Вход Agent 1B | 1.0 | `Agent1BInputPackageV1` | `contracts/agent_1b/v1/input_package.schema.json` | `test_agent_1b_contract.py` |
| Отчёт Agent 1B | 1.0 | `Agent1BVerificationReportV1` | `contracts/agent_1b/v1/verification_report.schema.json` | `test_agent_1b_contract.py` |
| Legacy-карточка | 1.0 | — | `contracts/legacy/agent_1a_v1/card.schema.json` + `.sha256` | `test_legacy_compat.py` |

Все схемы — Draft 2020-12, генерируются из Pydantic (`tools/export_schemas.py`) и
сверяются побайтово; дополнительно проверяются независимым `jsonschema`.

### 5.2. Карточка Agent 1A v2

9 разделов, 27 полей, 3 типа поля, 4 статуса — имена, типы и статусы унаследованы от
legacy без смысловых изменений (равенство множеств и типов проверяется тестом).
Документ: [`docs/contracts/agent_1a_v2.md`](../contracts/agent_1a_v2.md).

### 5.3. Evidence

`EvidenceCandidate` (предложение модели) и `EvidenceRecord` (результат детерминированного
разрешения) разделены структурно: у кандидата физически нет полей `source_sha256`,
`char_start`, `char_end`, `bbox`, `match_status`. Документ:
[`docs/contracts/evidence_contract.md`](../contracts/evidence_contract.md).
Сам resolver не реализован (Итерация 4).

### 5.4. Agent 1B

Вход требует обязательную read-only ссылку на оригинал и запрещает мутацию карточки
(`Literal[False]`), несовместимая версия 1A отклоняется с диагностикой. Отчёт различает
6 статусов findings и не позволяет объявить `verified` при наличии непроверенных находок.
`semantic.status` в пакете 1A — закрытое перечисление без значения `verified`.
Документ: [`docs/contracts/agent_1b_input_contract.md`](../contracts/agent_1b_input_contract.md).

### 5.5. GraphState и Model Gateway

`Agent1AState` — `TypedDict`, хранит только ссылки и компактные метаданные; проверка
запрещает bulk-ключи, байты и встроенные строки больше 100 000 символов; счётчики
повторов раздельные и ограничены конфигурацией
([`docs/contracts/agent_1a_graph_state.md`](../contracts/agent_1a_graph_state.md)).

Model Gateway provider-neutral: capabilities проверяются **до** вызова, ошибки
нормализованы в 8 классов, retry/timeout принадлежат workflow, `ModelRequest` не имеет
поля для секрета и отвергает секретоподобные метаданные
([`docs/contracts/model_gateway.md`](../contracts/model_gateway.md)).

---

## 6. ADR

Журнал: [`docs/decisions/README.md`](../decisions/README.md).

| ADR | Решение | Статус |
|---|---|---|
| ADR-001 | Python + LangGraph управляют runtime; DSH — только разработка | Accepted |
| ADR-002 | Модульный монолит | Accepted |
| ADR-003 | Границы домена/адаптеров, расположение Model Gateway | Accepted |
| ADR-004 | Версионированные контракты и неизменяемый пакет | Accepted |
| ADR-005 | Model Gateway и capabilities | Accepted (интерфейс) / Deferred (провайдер) |
| ADR-006 | Метаданные в БД, крупные артефакты в Artifact Store | Proposed / Deferred (бэкенд) |
| ADR-007 | Условный full-context/RAG | Proposed |
| ADR-008 | Детерминированный Evidence Resolver | Accepted (контракт) / Deferred (реализация) |
| ADR-009 | Dependency baseline, пины, группы | Accepted |
| ADR-010 | Локальный CLI-first runtime | Accepted (CLI-first) / Deferred (остальное) |

Отложенные решения: провайдер и модели, OCR-движки, embedding-модель, reranker,
storage-бэкенд, платформа запуска (Windows native vs WSL2/Docker), канал ручной проверки,
gold-набор. Ни одно не закрыто от имени заказчика.

---

## 7. Зависимости

Отчёт: [`docs/development/dependency_baseline.md`](../development/dependency_baseline.md).

* Core: `pydantic 2.14.3`, `pyyaml 6.0.3`, `langgraph 1.2.20`, `langchain-core 1.8.0`.
* `dev`: `pytest 9.2.0`, `pytest-cov 7.1.0`, `ruff 0.17.4`, `ty 0.0.101`, `jsonschema 4.26.0`.
* `parsing`: `docling 2.150.0`, `pymupdf 2.1`.
* `storage`: `sqlalchemy 2.1.8`, `alembic 1.20.1`, `psycopg[binary] 3.3.8`, `pgvector 0.5.1`.
* `api`: `fastapi 0.162.1`.

Полный `langchain` **не** устанавливается (LangChain не должен быть вторым
оркестратором). Wildcard-версий нет. `uv.lock` — 168 пакетов, `uv lock --check` проходит.
Версия менеджера зафиксирована в `pyproject.toml`
(`[tool.uv] required-version = "==0.13.7"`), версия интерпретатора — в `.python-version`.
Ограничение: колёса `docling`/`pymupdf` не устанавливались, поэтому бинарная
совместимость parsing-группы на этой ОС **не подтверждена** — её нужно проверить в
Итерации 2 до начала работ по парсингу.

---

## 8. Тесты и проверки (точные команды и коды возврата)

Все команды выполнены в `D:\MultiAgentPipeline\research-agent-platform`.

| Команда | Exit | Результат |
|---|---|---|
| `uv lock --check` | 0 | lock актуален |
| `uv run pytest` | 0 | **246 passed**, 0 failed, 0 skipped (0.66 s) |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | все файлы отформатированы |
| `uv run ty check` | 0 | All checks passed |
| `uv run python tools/export_schemas.py --check` | 0 | 3 схемы актуальны |
| `uv run research-platform --help` | 0 | команды `contracts validate` и параметры |
| `uv run research-platform --version` | 0 | `research-platform 0.1.0` |
| `uv run research-platform contracts validate --input tests\fixtures\agent_1a_package_v2_valid.json` | 0 | `valid: … satisfies Agent1AExtractionPackageV2` |
| `uv run research-platform contracts validate --input <сломанный пакет>` | 1 | `1 contract violation(s)` + путь до поля |

Ни один тест не требует сети, реального API, LLM, OCR-весов или БД.

---

## 9. Критерии приёмки ТЗ (§8)

### 8.1. Архитектура и документация

| Критерий | Статус | Подтверждение |
|---|---|---|
| ADD v1.0 изучен; связь решений с разделами ADD документирована | PASS | ADR-001…010 ссылаются на §6–§27 ADD |
| Legacy обследован статически, файлы не изменены | PASS | `docs/migration/legacy_agent1a_audit.md`; записей в legacy не было |
| Legacy-to-v2 disposition table создана | PASS | §5 аудита (29 строк) |
| ADR-001…ADR-010 созданы и имеют статус | PASS | `docs/decisions/` |
| Структура модульного монолита отражена в репозитории | PASS | `src/research_platform/{domain,application,platform,agents}` |
| Dependency direction зафиксирован | PASS | `docs/architecture/architecture_overview.md` + AST-тесты |
| Открытые вопросы не закрыты самовольно | PASS | §6 отчёта, ADR со статусом Deferred |

### 8.2. Контракты

| Критерий | Статус | Подтверждение |
|---|---|---|
| Все 27 legacy-полей учтены и отображены в v2 | PASS | `test_all_twenty_seven_legacy_fields_are_preserved` |
| Legacy schema v1 сохранена и валидируется без изменений | PASS | SHA-256 зафиксирован; `test_legacy_example_card_still_validates_against_v1_schema` |
| Pydantic и JSON Schema синхронизированы тестами | PASS | `test_schema_export.py` (побайтовое сравнение + `jsonschema`) |
| `EvidenceCandidate` и `EvidenceRecord` разделены | PASS | `test_evidence_contract.py`, ADR-008 |
| Вход 1B ссылается на оригинал и immutable card | PASS | `Agent1BInputPackageV1` + `MutationPolicy` |
| Семантический статус 1A не может быть `verified` | PASS | закрытое перечисление, `test_semantic_verification_cannot_be_verified` |
| GraphState не содержит полных документов и крупных payloads | PASS | `test_graph_state.py` |
| Model Gateway provider-neutral, проверен fake-адаптером | PASS | `test_model_gateway.py` |
| Run/Artifact контракты задают версионирование и неизменяемость | PASS | `test_run_and_artifact_contracts.py` |

### 8.3. Репозиторий и зависимости

| Критерий | Статус | Подтверждение |
|---|---|---|
| Новый репозиторий в целевом каталоге, отделён от legacy | PASS | `D:\MultiAgentPipeline\research-agent-platform` |
| `pyproject.toml`, `.python-version`, `uv.lock` созданы | PASS | lock: 168 пакетов |
| Группы зависимостей описаны, без лишних библиотек | PASS | `dev`/`parsing`/`storage`/`api`; `langchain` отсутствует |
| `.env.example` и example YAML без секретов | PASS | `test_example_env_file_contains_no_real_secret`, `test_example_model_profiles_have_no_literal_key` |
| CLI `--help`, `--version`, `contracts validate` работают | PASS | §8 |

### 8.4. Тесты

| Критерий | Статус | Подтверждение |
|---|---|---|
| Все тесты Итерации 0 пройдены | PASS | 246 passed / 0 failed / 0 skipped |
| `uv lock --check` пройден | PASS | exit 0 |
| Ruff lint и format check пройдены | PASS | exit 0 / exit 0 |
| Type check пройден выбранным инструментом | PASS | `ty check`, exit 0 |
| Нет тестов, требующих API, LLM, OCR-весов или БД | PASS | только fake-адаптеры и локальные данные |
| В отчёте есть точные команды и exit codes | PASS | §8 |

### 8.5. Контроль объёма

| Критерий | Статус | Подтверждение |
|---|---|---|
| Полный Agent 1A не реализован | PASS | нет узлов графа; `graph/` пуст и объяснён |
| RAG не реализован | PASS | нет chunking/embeddings/retrieval |
| Реальное извлечение PDF/OCR не выполнено | PASS | нет кода парсинга |
| Реальный вызов LLM не выполнен | PASS | нет адаптеров провайдеров |
| Рабочая БД не запущена, прикладные миграции не созданы | PASS | нет моделей SQLAlchemy/Alembic |
| Legacy-проект не изменён | PASS | только чтение |

---

## 10. Что не выполнено и почему

| Пункт | Причина | Статус |
|---|---|---|
| Установка колёс и smoke-import `docling`/`pymupdf`/`sqlalchemy`/`fastapi` | вне объёма Итерации 0 (ТЗ 0.10 п. 9: не скачивать модели/OCR; пакеты не импортируются), но бинарная совместимость parsing-группы поэтому не подтверждена | осознанно отложено до Итерации 2 |
| Реализация Evidence Resolver | ТЗ 0.5: «реальный resolver в Итерации 0 не реализуется» | отложено (Итерация 4) |
| Миграция v1→v2 как функция | ТЗ 0.4.4: допустим только интерфейс и mapping specification | отложено + требуется решение по метаданным |
| Git-репозиторий и коммиты | не требовалось ТЗ; фиксацию версий выполняет владелец. `.gitignore` готов, PDF и `.env` исключены | не выполнялось |
| Gold-набор и evaluation | ТЗ: вне объёма (Итерация 6) | отложено |
| Docker Compose, PostgreSQL, Alembic, FastAPI | ТЗ §3.2 запрещает в этой итерации | запрещено объёмом |
| `pip-audit` / аудит транзитивных зависимостей | не требовалось; 168 транзитивных пакетов приняты lock-файлом | отложено |

---

## 11. Риски

| Риск | Влияние | Мера |
|---|---|---|
| Бинарная совместимость `docling`/`pymupdf` на Windows/3.13 не проверена | Итерация 2 может начаться с установки, а не с кода | `uv sync --group parsing` + smoke-import первой задачей Итерации 2 |
| Папка проекта уже один раз блокировала запись | срыв сборки у владельца | исключение в защите папок/антивирусе; §2.3 |
| Реестр D1–D9 восстановлен из расшифровки сессии, исходный документ в репозиторий не входит | возможна потеря одного из дефектов | сверить с ТЗ-источником при приёмке (отмечено в §8 аудита) |
| Расхождение ADD §10.2 (`HYBRID`) и ТЗ 0.4.2 | несовместимость ожиданий по режимам retrieval | решение заказчика, ADR-007 |
| Интерпретация «диапазона» в `EvidenceCandidate` | переработка поля, если заказчик прочтёт иначе | `quote_occurrence` изолирован: удаление не ломает ledger (ADR-008) |
| `ty 0.0.x` | возможны ложные срабатывания при обновлении | версия зафиксирована; обновление — вместе с ADR-009 |
| `not_found` не имеет поля «coverage trail» в контракте | правило ADD §10.5 сейчас только в документации | добавить поле в v2.1 или в контракт состояния (Итерация 3–4) |
| Legacy-карточки нельзя полностью превратить в v2 | «тихая» миграция создала бы ложный провенанс | импорт только как `COMPATIBILITY_ONLY`; §6 mapping-документа |

---

## 12. Рекомендация

**Проект готов к Итерации 1 (Platform Skeleton)** при выполнении трёх условий:

1. закрыть или подтвердить решения из §11, влияющие на контракты: режим `HYBRID`,
   прочтение кандидата evidence, место `scientific_review_status`;
2. выбрать провайдера моделей и платформу запуска (Windows native vs WSL2/Docker) —
   без этого нельзя начинать Model Gateway adapter и Run Manager;
3. проверить установку parsing-группы (`docling`, `pymupdf`) на целевой ОС до начала
   работ по парсингу.

Блокеров для старта Итерации 1 нет: структура репозитория, публичные контракты, границы
ответственности и baseline зависимостей зафиксированы и проверены.

---

## Приложение. Где что лежит

| Артефакт | Путь |
|---|---|
| Обзор архитектуры | `docs/architecture/architecture_overview.md` |
| ADR-журнал | `docs/decisions/README.md` |
| Аудит legacy | `docs/migration/legacy_agent1a_audit.md` |
| Mapping v1 → v2 | `docs/migration/v1_to_v2_mapping.md` |
| Контракты (6 документов) | `docs/contracts/` |
| Отчёт о зависимостях | `docs/development/dependency_baseline.md` |
| Схемы | `contracts/` |
| Примеры конфигурации | `config/*.example.yaml`, `.env.example` |
| Тесты и их описание | `tests/`, `tests/README.md` |
| Примеры пакетов | `tests/fixtures/agent_1a_package_v2_valid.json`, `tests/fixtures/agent_1b_*_valid.json` |
