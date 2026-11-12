# Закрытие архитектурных вопросов перед Итерацией 1

**Проект:** `research-agent-platform` · **Дата:** 2026-11-12 · **Основание:** корректирующее задание «закрытие вопросов после Итерации 0» · **Исполнитель:** DSH

---

## 1. Executive Summary

**Общий результат: `PASS WITH CONDITIONS`.**

Закрыты все архитектурные вопросы, из-за которых Итерации 1 пришлось бы принимать решения
заново:

- `RetrievalMode` и `RetrievalStrategy` разделены, режим `hybrid` реализован — противоречие
  ADD §10.2 устранено (ADR-007);
- контракт evidence переработан: модель предлагает только цитату и подсказки, `quote_occurrence`
  удалён, `ambiguous` стал полноценным исходом (ADR-008);
- введён обязательный **Coverage Trail**: `not_found` теперь нельзя выставить без
  доказанного покрытия поиска;
- `scientific_review_status` закреплён исключительно за Agent 1B (ADR-004);
- зафиксированы целевое хранилище (PostgreSQL 18 + pgvector, ADR-006) и платформа разработки
  (Windows-native CLI-first, ADR-010); кандидат провайдера записан, реальный адаптер отложен
  (ADR-005);
- исправлена арифметика legacy-mapping (было указано 34 при 29 строках);
- инцидент ACL проверен в режиме только чтения, системные настройки не менялись;
- установка и импорт parsing-зависимостей подтверждены на целевой среде.

**Условия** (не блокируют архитектуру, но требуют решения владельца до соответствующих шагов
Итерации 1): `OI-001…OI-004` — агрегатор, протокол, capabilities и лимиты модели;
`OI-006` — политика запуска локальной PostgreSQL. Полный перечень — §10 и
[`docs/decisions/open_items.md`](../decisions/open_items.md).

Проверки: **373 passed**, `ruff check` / `ruff format --check` / `ty check` /
`uv lock --check` / экспорт схем — все exit 0.

---

## 2. Принятые решения

| ADR | Статус до | Статус после | Что изменилось |
|---|---|---|---|
| ADR-004 | Accepted | **Accepted** | Добавлена матрица версий (2.1/2.0, 1.1/1.0, candidates 2.1), описан механизм заморозки через `.sha256`, закреплено, что `scientific_review_status` существует только в отчёте Agent 1B |
| ADR-005 | Accepted (интерфейс) / Deferred (провайдер) | **Accepted (интерфейс) / Deferred (реальный адаптер)** | Записан кандидат `DeepSeek V4.1 Flash 1017` через ранее использованный агрегатор; добавлен чек-лист из 12 свойств, которые нужно подтвердить; запрет секретов и реальных вызовов |
| ADR-006 | Proposed / Deferred (бэкенд) | **Accepted (целевая архитектура) / Deferred (эксплуатация)** | Целевой backend — PostgreSQL 18 + pgvector; разведены версии расширения (0.8.7+) и Python-пакета (0.5.1); правила хранения и неизменяемости артефактов |
| ADR-007 | Proposed (с открытым вопросом HYBRID) | **Accepted (контракт) / Deferred (реализация retrieval)** | `RetrievalMode` (4 значения) отделён от `RetrievalStrategy` (3 значения); описана детерминированная политика выбора и безопасный откат; `HYBRID` представлен |
| ADR-008 | Accepted (контракт) / Deferred (резолвер) | **Accepted (контракт 2.1) / Deferred (резолвер)** | Кандидат: только `quote` + подсказки; `match_status`/`match_type`, `occurrence_index`, версии парсера и резолвера; 8 правил разрешения; вопрос про «диапазон» закрыт структурно |
| ADR-010 | Accepted (CLI-first) / Deferred (остальное) | **Accepted (Windows-native CLI-first) / Deferred (Linux/Docker, HITL)** | Целевая платформа первого релиза: Windows 10 Pro 22H2+, Python 3.13.x, uv, CLI-first, без обязательного Docker/WSL2; требование переносимости путей |

ADR-001, ADR-002, ADR-003, ADR-009 не меняли решения; журнал
[`docs/decisions/README.md`](../decisions/README.md) обновлён (таблица решений и таблица
открытых вопросов без закрытых пунктов).

---

## 3. Обновлённые контракты

| Контракт | Версия | Pydantic | Схема | Статус |
|---|---|---|---|---|
| Пакет Agent 1A → 1B | **2.1** | `Agent1AExtractionPackageV21` | `contracts/agent_1a/v2.1/extraction_package.schema.json` | текущий |
| Кандидаты evidence | **2.1** | `EvidenceCandidateList` | `contracts/agent_1a/v2.1/evidence_candidates.schema.json` | текущий |
| Вход Agent 1B | **1.1** | `Agent1BInputPackageV11` | `contracts/agent_1b/v1.1/input_package.schema.json` | текущий |
| Отчёт Agent 1B | **1.1** | `Agent1BVerificationReportV11` | `contracts/agent_1b/v1.1/verification_report.schema.json` | текущий |
| Пакет Agent 1A → 1B | 2.0 | `Agent1AExtractionPackageV2` | `contracts/agent_1a/v2/…` + `.sha256` | **заморожен** |
| Вход Agent 1B | 1.0 | `Agent1BInputPackageV1` | `contracts/agent_1b/v1/input_package.schema.json` + `.sha256` | **заморожен** |
| Отчёт Agent 1B | 1.0 | `Agent1BVerificationReportV1` | `contracts/agent_1b/v1/verification_report.schema.json` + `.sha256` | **заморожен** |
| Legacy-карточка | 1.0 | — | `contracts/legacy/agent_1a_v1/card.schema.json` + `.sha256` | **заморожен** |

**Как обеспечена неизменяемость истории.** Замороженные схемы генерируются из замороженных
Pydantic-снимков (`domain/runs/v2_0.py`, `domain/evidence/v2_0.py`, сами модели пакетов 2.0/1.0),
а их байты закреплены контрольной суммой в файлах `*.sha256`. Тест
`tests/contract/test_frozen_schemas.py` падает, если изменился хотя бы один байт, — то есть
согласованная правка модели и схемы не проходит незамеченной. Проверено фактически: после
перегенерации SHA-256 всех трёх замороженных схем совпали с исходными
(`caa0b289…` пакет 2.0, `2a6deb80…` вход 1.0, `8c62d4f6…` отчёт 1.0).

**Как Agent 1B принимает новую и старую версии.** Вход 1.1 объявляет поддерживаемые версии
пакета 1A: `("2.0", "2.1")`. Форма `run` и `evidence_ledger` выбирается детерминированно по
`coverage.source_contract_version` — до валидации, а не «по совпадению структуры» (2.0-run
является структурным подмножеством 2.1, и простая union-схема молча повышала бы провенанс).
Пакет 2.0 нельзя молча повысить до 2.1: у него нет ни `retrieval_strategy`, ни coverage trail,
и это проверяется (см. §4).

---

## 4. Coverage Trail

Новый модуль `domain/coverage/` (`models.py`, `rules.py`) и раздел `coverage_trail` пакета 2.1.

**Поля `FieldCoverageRecord`:** `field_path`, `retrieval_mode`, `retrieval_strategy`,
`coverage_status`, `search_plan_id`, `searched_sections`, `searched_page_ranges`,
`retrieved_block_ids`, `search_attempt_ids`, `full_text_fallback_performed`,
`required_checks_completed`, `not_found_eligible`, `coverage_policy_version` (`1.0`), а также
`attempts` (аудит шагов) и `notes`. `SearchAttempt`: `attempt_id`, `step_id`,
`retrieval_strategy`, область поиска, `retrieved_block_ids`, `outcome`
(`succeeded`/`no_hits`/`failed`/`skipped`), `error_code`. `PageRange` — включительный диапазон
физических страниц (с 1).

**Статусы:** `complete` (обязательные шаги утверждённого плана выполнены без критических
ошибок — это **не** математическое доказательство отсутствия сведений в статье), `partial`,
`failed`, `not_applicable`.

**Проверяемые инварианты:** `complete` ⇒ `required_checks_completed`; `complete` ⇒ есть хотя бы
одна записанная попытка; для `rag`/`hybrid` `complete` требует указанной области поиска;
`not_found_eligible` ⇒ `complete` + проверки завершены; `partial`/`failed` никогда не eligible;
`not_applicable` пуст (нет области, попыток и fallback) и не eligible; `search_attempt_ids`
обязан совпадать со списком записанных попыток; `full_text_fallback_performed=True` требует
фактически выполненной попытки с `step_id`, начинающимся на `fallback_`; режим и стратегия
coverage обязаны совпадать с режимом и стратегией run.

**Правила `not_found`:** допустим только при `coverage_status=complete` и
`not_found_eligible=true`; для `rag`/`hybrid` — только при реально выполненном полнотекстовом
fallback; при `partial`/`failed` вывод об отсутствии сведений запрещён (используется `unclear`
или фиксируется ошибка обработки); `not_found_eligible` формирует Python, а не модель.

**Связь с Agent 1B:** вход 1.1 содержит секцию `coverage` (`availability` =
`recorded`/`not_recorded`, `source_contract_version`, `policy_version`, `records`). Для пакета
2.1 обязательны `recorded` и ровно 27 записей; для пакета 2.0 — `not_recorded` без записей
(«выдумывать покрытие запрещено»). Отчёт 1.1 позволяет проверить покрытие: новый статус
находки `omitted` («в источнике есть, в карточке нет»), `coverage_verdict`
(`confirmed_complete`/`confirmed_partial`/`disputed_complete`/`coverage_not_recorded`) и
`coverage_reviewed`. Вердикт `verified` невозможен при `disputed_complete` или любом
не-`verified` finding.

---

## 5. Legacy Audit

**Проверка арифметики.** Таблица в `docs/migration/legacy_agent1a_audit.md` содержит **29**
уникальных строк; каждая имеет ровно одну классификацию; строк без классификации нет;
компонентов-дубликатов нет.

| Категория | В отчёте Итерации 0 | Фактически |
|---|---|---|
| `REUSE_AS_REFERENCE` | 6 | **5** |
| `REIMPLEMENT` | 14 | **13** |
| `COMPATIBILITY_ONLY` | 3 | **2** |
| `DEPRECATE` | 9 | **8** |
| `NEEDS_DECISION` | 2 | **1** |
| Сумма | 34 | **29** |

**Причина расхождения установлена:** показатели считались как число вхождений слов во всём
разделе, поэтому в каждую категорию попадала ещё одна строка — легенда с перечнем допустимых
значений (по одному вхождению на категорию: 29 + 5 = 34). Классификации взаимоисключающие,
поэтому сумма обязана равняться числу строк.

**Что исправлено:** в аудит добавлен §5.1 с таблицей учёта, пересчитанными значениями, явным
объяснением ошибки и итогом по назначению (образец — 5, переписать — 13, только чтение
истории — 2, не переносить — 8, решение заказчика — 1); в отчёте Итерации 0 числа исправлены
с пометкой о корректировке. Содержание строк и выводы не менялись. Legacy-проект не
изменялся.

Дополнительно обновлены ссылки аудита на новую терминологию evidence (`match_status` +
`match_type`) и на версию пакета 2.1.

---

## 6. ACL Incident

Проверка выполнена **только чтением**; права, антивирус и настройки Windows не изменялись;
команды восстановления не запускались; перемещённая папка не удалялась.

**Сохранённые материалы** (`D:\MultiAgentPipeline\.dsh-acl-recovery\`):

| Файл | Назначение |
|---|---|
| `acl-backup-c2e9ee50…json` (274 Б) | состояние `D:\MultiAgentPipeline` до правки: `Observed` (полный SDDL на момент проверки) + `Dacl` (собственные ACE) + `Protected` |
| `acl-backup-c2e9ee50…json.ps1` (45 КБ) | независимый скрипт восстановления (режим `-Restore <record>`) |
| `acl-backup-4a4ddd70…json` (303 Б) + `.ps1` | состояние папки `research-agent-platform`, снятое в 22:53 |
| `acl-report-6eb51648….jsonl`, `acl-report-b0328472….jsonl` | полные журналы диагностики и правок |

**Перемещённая папка** `.trash-it0\research-agent-platform.preexisting\` содержит 10 файлов,
созданных в этой же сессии до пересоздания каталога (`pyproject.toml`, `README.md`,
`.python-version`, `src/research_platform/{__init__.py,py.typed,domain/{__init__.py,base.py}}`).
Пользовательских данных в ней нет. Второй каталог `.trash-it0\rap-probe\` — временная копия,
сделанная при диагностике.

**Текущее состояние прав.**

- `D:\MultiAgentPipeline`: владелец — текущая учётная запись; **собственные** (не наследуемые)
  ACE: `Все: Deny DeleteSubdirectoriesAndFiles`, capability-ACE DSH
  (`S-1-4-397607488-622765443`: Write/Delete/Synchronize) и `FullControl` для учётной записи
  (добавлен диагностическим скриптом); остальное наследуется от `D:\`.
- `D:\MultiAgentPipeline\research-agent-platform`: **ни одного собственного ACE** — только
  наследуемые от родителя записи (Администраторы, СИСТЕМА, «Прошедшие проверку» Modify,
  «Пользователи» Read) плюс наследуемые deny/capability от DSH.

**Можно ли объективно восстановить исходное состояние.** Частично:

1. Для `D:\MultiAgentPipeline` запись содержит собственные ACE до правки (`Dacl: "D:"` —
   собственных ACE не было) и наблюдаемый SDDL, поэтому восстановление «собственные ACE
   отсутствуют, наследование от `D:\`» обосновано; команда восстановления зафиксирована в
   JSONL-журнале.
2. Для папки проекта запись снята **после** правки родителя и относится к объекту, который
   позже был перемещён в `.trash-it0`: текущий каталог — новый объект с чистой
   наследственностью. Поэтому восстановление именно этой записи к текущему каталогу
   неприменимо.
3. Восстановление уберёт и capability-ACE DSH, который появился после снятия записи; DSH может
   добавить его снова при следующей записи. Это особенность песочницы, а не порча данных.
4. Точное «документное» состояние до инцидента (до первого запуска диагностики) объективно
   подтвердить нельзя: записи снимаются перед каждой правкой, а не до инцидента.

**Риски:** повторный отказ записи в каталог проекта (тогда — исключение в защите папок для
`python.exe`/`uv.exe` или для каталога, решение владельца); ручной запуск скрипта восстановления
без понимания последствий; удаление `.trash-it0` и `.dsh-acl-recovery` до того, как владелец
решит, нужны ли они.

**Требует решения владельца:** нужны ли сохранённые материалы дальше (можно удалить после
приёмки); нужна ли отмена добавленных ACE; нужно ли исключение в защите папок.

---

## 7. Testing

Все команды выполнены в `D:\MultiAgentPipeline\research-agent-platform`, окружение:
Windows 10 Pro 22H2 (`10.0.19045`) x64, CPython `3.13.18` (managed uv), `uv 0.13.7`.

| Команда | Exit | Результат |
|---|---|---|
| `uv lock --check` | 0 | lock актуален (168 пакетов, не менялся) |
| `uv run pytest -q` | 0 | **373 passed**, 0 failed, 0 skipped (1.70 s) |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | все файлы отформатированы |
| `uv run ty check` | 0 | All checks passed |
| `uv run python tools/export_schemas.py --check` | 0 | 7 схем актуальны |
| `uv run research-platform --help` | 0 | команды и параметры |
| `uv run research-platform --version` | 0 | `research-platform 0.1.0` |
| `… contracts validate --input tests\fixtures\agent_1a_package_v2_1_valid.json` | 0 | `valid … (contract 2.1)` |
| `… contracts validate --input tests\fixtures\agent_1a_package_v2_valid.json` | 0 | `valid … (contract 2.0)` |
| `… contracts validate --input <сломанный пакет>` | 1 | `N contract violation(s)` + путь до поля |

**Ограничение среды (честно).** В песочнице DSH дочерние процессы не могут создавать
временные каталоги, поэтому `pytest` запускался с `--basetemp` вне репозитория. Это влияет
только на размещение временных файлов; набор тестов и их результат не меняются. В обычном
PowerShell владельца достаточно `uv run pytest -q`. Команды, требующие записи в каталог
проекта (`uv`, `pytest`, `ruff`, `ty`), выполнялись с разовым расширением прав на конкретную
команду; коды возврата — фактические.

**Что не проверялось:** реальные API-вызовы LLM, работа с БД, парсинг документов, OCR-качество,
нагрузочные характеристики.

---

## 8. Dependency Check

```powershell
uv lock --check                  # exit 0, lock не изменился
uv sync --group parsing          # exit 0
uv run --group parsing python -c "import docling, pymupdf; print('PARSING_IMPORTS_OK')"
```

Фактический результат: `PARSING_IMPORTS_OK`; `docling 2.150.0`, `pymupdf 2.1`,
транзитивные `torch 2.15.0`, `transformers 5.22.2`, `docling-core 2.106.0`,
`docling-ibm-models 4.0.4`.

**Подтверждено:** для Windows 10 x64 / CPython 3.13.18 существуют подходящие колёса, установка
и импорт работают; версии не менялись.

**Не подтверждено:** качество парсинга, корректность извлечения таблиц/страниц, работа OCR
(движки не выбирались, веса не скачивались, Tesseract не устанавливался). Вывод о качестве
возможен только после Итерации 2 на реальном корпусе.

**Побочный эффект:** последующий `uv run` без `--group parsing` синхронизирует окружение по
умолчанию и может удалить эти пакеты; для парсинга нужно повторять `uv sync --group parsing`.

---

## 9. Platform Decisions

| Область | Решение | Статус |
|---|---|---|
| Платформа разработки | Windows 10 Pro 22H2+ / Python 3.13.x (`>=3.13.18,<3.14`) / uv / CLI-first, без обязательного Docker/WSL2 | Accepted (ADR-010) |
| Переносимость | в Domain/Application нет абсолютных путей Windows; `source_file_name` — «голое» имя; артефакты — логические `artifact://` URI; пути создаются через конфигурацию и `pathlib` | Accepted |
| Метаданные и состояние | PostgreSQL 18 + **pgvector** (расширение 0.8.7 или новее с проверенной безопасностью); Python-пакет `pgvector 0.5.1` — другая линейка версий | Accepted (ADR-006) |
| Крупные артефакты | файловый Artifact Store за `ArtifactStorePort`; S3-совместимый backend позже без изменения Domain/Application | Accepted (ADR-006) |
| Правила хранения | метаданные и ссылки в БД, крупные артефакты в хранилище; PDF не попадает в GraphState; hash и version у артефактов; оригинал неизменяем; результаты не перезаписываются; новая попытка — новый run | Accepted |
| Model Gateway | provider-neutral интерфейс; кандидат `DeepSeek V4.1 Flash 1017` через ранее использованный агрегатор; реальный адаптер отложен; в Итерации 1 допустим fake adapter | Accepted / Deferred (ADR-005) |

Фактическая БД, Docker, миграции и реальные вызовы провайдера в этой работе **не создавались и
не запускались**.

---

## 10. Open Items

Полный реестр — [`docs/decisions/open_items.md`](../decisions/open_items.md). Сводка:

| ID | Вопрос | Блокирует | Срок |
|---|---|---|---|
| `OI-001` | точное имя и API-протокол LLM-агрегатора | реальный provider adapter | до выхода из Итерации 1 / Итерации 3 |
| `OI-002` | подтверждённые capabilities модели (structured output / JSON Schema, tool calling, streaming, reasoning-блоки) | структурный путь извлечения | там же |
| `OI-003` | подтверждённые лимиты контекста/выхода и rate limits | политика retrieval-режима и бюджет контекста | там же |
| `OI-004` | фактический `model_id`, возвращаемый провайдером | воспроизводимость `RunMetadata` | там же |
| `OI-005` | ограничения DSH при работе с API провайдера | рабочий процесс разработки (не runtime) | до начала работ с адаптером |
| `OI-006` | политика запуска локальной PostgreSQL (Docker Compose vs служба) и бэкап | storage-часть Итерации 1 | до первого шага Итерации 1 |
| `OI-007` | канал human-in-the-loop для quality gate | узел quality gate и interrupts | Итерация 2–3 |
| `OI-008` | OCR-движки и языковые пакеты RU/EN, потребность в GPU | OCR-ветка | Итерация 2 |
| `OI-009` | embedding-модель и revision | индексация RAG | Итерация 4 |
| `OI-010` | нужен ли multilingual reranker | ничего сейчас; нужно до retrieval evaluation | до оценки retrieval |
| `OI-011` | состав первого gold-набора статей | evaluation | Итерация 6 |

Ни одно значение не выдумано: там, где нужны данные владельца, стоит «решение владельца».

---

## 11. Изменённые файлы

### Код (`src/`)

| Файл | Изменение |
|---|---|
| `domain/evidence/models.py` | переписан под контракт 2.1: `EvidenceCandidate` (quote + hints), `EvidenceCandidateList`, `EvidenceRecord` (+`match_status`/`match_type`/`occurrence_index`/parser/resolver), `ResolutionStatus`, `MatchType`, `VerificationMethod`, таблица допустимых пар |
| `domain/evidence/v2_0.py` | **создан**: замороженный снимок evidence 2.0 |
| `domain/evidence/ledger.py` | правила ledger переведены на `EvidenceRecordLike` (Protocol), работает для 2.0 и 2.1 |
| `domain/evidence/__init__.py` | экспорт 2.1 + алиасы замороженных типов |
| `domain/coverage/{__init__,models,rules}.py` | **создано**: Coverage Trail и правила `not_found` |
| `domain/runs/models.py` | `RetrievalMode` (+`hybrid`), новый `RetrievalStrategy`, `SEARCH_RETRIEVAL_MODES`, `RunMetadata.retrieval_strategy` с условными правилами |
| `domain/runs/v2_0.py` | **создан**: замороженный `RunMetadata`/`RetrievalMode` 2.0 |
| `domain/runs/__init__.py` | экспорт новых типов |
| `agents/agent_1a/contracts/package_v21.py` | **создан**: `Agent1AExtractionPackageV21` (coverage trail, проверка провенанса парсера) |
| `agents/agent_1a/contracts/package.py` | переведён на замороженные снимки 2.0 (байты схемы не изменились) |
| `agents/agent_1a/contracts/graph_state.py` | добавлено `retrieval_strategy` + проверка согласованности режима и стратегии |
| `agents/agent_1a/contracts/adapter.py` | зафиксировано: адаптер целевой версией имеет 2.0, а не 2.1 (coverage нельзя восстановить) |
| `agents/agent_1b/contracts/input_package_v11.py` | **создан**: вход 1.1 с `CoverageInput`, поддержкой 2.0/2.1, детерминированным выбором формы по версии |
| `agents/agent_1b/contracts/verification_report_v11.py` | **создан**: отчёт 1.1 (`omitted`, `CoverageVerdict`, `coverage_reviewed`) |
| `agents/agent_1b/contracts/{input_package,verification_report}.py` | переведены на замороженные снимки 2.0 |
| `platform/contracts/schemas.py`, `__init__.py` | экспорт 7 схем (3 замороженных + 4 текущих) |
| `cli.py` | диспетчеризация по `contract_version` (2.0/2.1), диагностика неподдерживаемой версии |

### Схемы (`contracts/`)

Созданы: `agent_1a/v2.1/extraction_package.schema.json`,
`agent_1a/v2.1/evidence_candidates.schema.json`,
`agent_1b/v1.1/input_package.schema.json`,
`agent_1b/v1.1/verification_report.schema.json`,
три файла `*.schema.json.sha256`. Замороженные схемы перегенерированы байт-в-байт (проверено).

### Тесты (`tests/`)

Созданы: `unit/test_coverage_contract.py`, `contract/test_frozen_schemas.py`,
`fixtures/README.md`, фикстуры `agent_1a_package_v2_1_valid.json`,
`agent_1a_candidates_v2_1_valid.json`, `agent_1b_input_v1_1_valid.json`,
`agent_1b_report_v1_1_valid.json`.
Обновлены: `unit/test_evidence_contract.py`, `unit/test_run_and_artifact_contracts.py`,
`unit/test_graph_state.py`, `contract/test_agent_1a_package.py`,
`contract/test_agent_1b_contract.py`, `contract/test_schema_export.py`,
`contract/test_cli.py`, `fixtures/builders.py`.
Замороженные фикстуры (`agent_1a_package_v2_valid.json`, `agent_1b_input_v1_valid.json`,
`agent_1b_report_v1_valid.json`, `legacy/card_v1_en_article.json`) **не перезаписывались** —
`tools/generate_fixtures.py` их валидирует.

### Инструменты и документация

`tools/generate_fixtures.py` (перегенерирует только текущие фикстуры, замороженные —
проверяет). Документация: `README.md`, `docs/architecture/architecture_overview.md`,
`docs/decisions/ADR-004/005/006/007/008/010` + `README.md` + новый `open_items.md`,
`docs/contracts/{evidence_contract,agent_1a_v2,agent_1b_input_contract,coverage_trail,run_and_artifact_contracts,agent_1a_graph_state}.md`,
`docs/migration/{legacy_agent1a_audit,v1_to_v2_mapping}.md`,
`docs/development/{dependency_baseline,iteration_1_readiness}.md`,
`docs/iteration_reports/iteration_0_report.md` (пометка + исправленные числа),
этот отчёт.

**Вне репозитория:** `D:\MultiAgentPipeline\.tooling\` (окружение uv, managed Python, кэш,
служебные скрипты проверки) — не входит в поставку; `.trash-it0\` и `.dsh-acl-recovery\` — см. §6.

---

## 12. Итоговая готовность

| Проверяемое условие | Статус |
|---|---|
| ADR-004/006/007/008 приведены к непротиворечивому состоянию | PASS |
| Изменения публичных контрактов версионированы (2.1/1.1, 2.0/1.0 заморожены) | PASS |
| Coverage Trail имеет формальный типизированный контракт | PASS |
| Правило `not_found` связано с реальным покрытием поиска | PASS |
| `EvidenceCandidate` и `EvidenceRecord` разделены | PASS |
| Научная проверка принадлежит Agent 1B | PASS |
| Legacy mapping арифметически согласован (29 = 5+13+2+8+1) | PASS |
| ACL-инцидент проверен без изменения системных прав | PASS |
| Тесты запущены, результаты зафиксированы (373 passed) | PASS |
| Parsing smoke test выполнен | PASS (установка и импорт; качество — Итерация 2) |
| Dependency baseline и платформенные решения задокументированы | PASS |
| `open_items.md` и `iteration_1_readiness.md` созданы | PASS |
| Pydantic и JSON Schema синхронизированы (7 схем, 3 замороженных под контролем сумм) | PASS |
| Legacy `D:\MultiAgentPipeline\Agent1\Agent1A` не изменён | PASS |
| Реализация Итерации 1 не начата | PASS |

**Вердикт: к Итерации 1 проект готов, с двумя условиями владельца**, которые не блокируют
архитектуру, но должны быть закрыты до соответствующих шагов:

1. `OI-001…OI-004` — агрегатор, протокол, capabilities, лимиты и фактический `model_id`;
   до этого Итерация 1 развивается на fake adapter.
2. `OI-006` — политика запуска локальной PostgreSQL и бэкапа: это первый шаг storage-части.

Остальные открытые вопросы (OCR, embedding-модель, reranker, канал HITL, gold-набор)
относятся к более поздним итерациям и уже зарегистрированы.

**Архитектурных и контрактных противоречий не осталось.** Публичные контракты
непротиворечивы, замороженная история защищена контрольными суммами, все проверки доступными
средствами выполнены и зафиксированы.
