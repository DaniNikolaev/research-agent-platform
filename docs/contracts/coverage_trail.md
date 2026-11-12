# Контракт Coverage Trail 1.0: учёт выполненного поиска

**Политика:** `1.0` (`COVERAGE_POLICY_VERSION`) · **Модели:** `FieldCoverageRecord`, `SearchAttempt`, `PageRange` · **Модуль:** `src/research_platform/domain/coverage/` · **Дата:** 2026-11-12

Coverage Trail — машинно-проверяемый отчёт о том, **какой поиск был выполнен для каждого
поля карточки**. Он входит в пакет Agent 1A 2.1 и является единственным основанием для
статуса `not_found`.

---

## 1. Зачем это нужно

Пустой результат retrieval **не доказывает** отсутствие информации в статье: не найденное
может лежать в неиндексированном разделе, за пределами контекста или в нераспознанном OCR.
Поэтому `not_found` нельзя выводить из пустого ответа поиска — этот статус должен быть
**заработан**:

* зафиксировано, какие разделы и физические страницы были в области поиска;
* зафиксировано, какие блоки реально получены и переданы модели;
* зафиксировано, какие попытки выполнялись и с каким исходом;
* зафиксировано, что обязательный полнотекстовый fallback для `rag`/`hybrid` действительно
  выполнялся, а не был объявлен;
* зафиксировано, что все обязательные шаги утверждённого плана поиска завершились без
  критической ошибки.

---

## 2. `FieldCoverageRecord` — покрытие одного поля

| Поле | Тип | Обяз. | Ограничения и смысл |
|---|---|---|---|
| `field_path` | dotted path | да | `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`, ≤ 128; один из 27 путей карточки |
| `retrieval_mode` | `RetrievalMode` | да | `full_context`, `rag`, `hybrid`, `not_applicable` |
| `retrieval_strategy` | `RetrievalStrategy` или `null` | нет | `dense`, `lexical`, `dense_lexical`; обязательна для `rag`/`hybrid` |
| `coverage_status` | `CoverageStatus` | да | `complete`, `partial`, `failed`, `not_applicable` |
| `search_plan_id` | строка | да | 1–256; идентификатор утверждённого плана поиска |
| `searched_sections` | массив строк | нет | ≤ 64 элементов, каждый непустой |
| `searched_page_ranges` | массив `PageRange` | нет | ≤ 200; физические страницы |
| `retrieved_block_ids` | массив строк | нет | ≤ 500; блоки, реально переданные модели |
| `search_attempt_ids` | массив строк | нет | ≤ 64; обязан совпадать с `attempts[].attempt_id` по порядку |
| `full_text_fallback_performed` | bool | нет (по умолчанию `false`) | требует реально выполненной попытки с префиксом `fallback_` |
| `required_checks_completed` | bool | да | все обязательные шаги плана завершены |
| `not_found_eligible` | bool | да | вычисляется Python; `true` только при `complete` и завершённых проверках |
| `coverage_policy_version` | строка | нет | по умолчанию `1.0`, шаблон `^\d+\.\d+$` |
| `attempts` | массив `SearchAttempt` | нет | ≤ 64; `attempt_id` уникальны |
| `notes` | массив строк | нет | ≤ 20, элементы непустые |

## 3. `SearchAttempt` и `PageRange`

| Поле `SearchAttempt` | Тип | Обяз. | Ограничения и смысл |
|---|---|---|---|
| `attempt_id` | строка | да | 1–256; уникален в пределах записи |
| `step_id` | строка | да | 1–256; шаг плана; префикс `fallback_` помечает полнотекстовый fallback |
| `retrieval_strategy` | `RetrievalStrategy` или `null` | нет | стратегия конкретного шага |
| `searched_sections` | массив строк | нет | ≤ 64 |
| `searched_page_ranges` | массив `PageRange` | нет | ≤ 200 |
| `retrieved_block_ids` | массив строк | нет | ≤ 500 |
| `outcome` | `SearchAttemptOutcome` | да | `succeeded`, `no_hits`, `failed`, `skipped` |
| `error_code` | код или `null` | нет | `^[a-z][a-z0-9_]*$`, 2–64; обязателен при `failed`, запрещён при остальных исходах |

| Поле `PageRange` | Тип | Обяз. | Ограничения и смысл |
|---|---|---|---|
| `start_page` | целое | да | ≥ 1; физическая страница, нумерация с 1 |
| `end_page` | целое | да | ≥ 1 и ≥ `start_page`; диапазон включающий |

---

## 4. `CoverageStatus`: семантика

| Значение | Смысл |
|---|---|
| `complete` | все обязательные шаги утверждённого плана поиска завершились без критической ошибки |
| `partial` | часть обязательных шагов не завершена; поиск нельзя читать как исчерпывающий |
| `failed` | поиск не выполнен; вывод об отсутствии информации запрещён |
| `not_applicable` | поле неприменимо к документу, поиск не требовался |

**Оговорка.** `complete` означает «обязательные шаги плана выполнены без критической
ошибки», а **не** математическое доказательство того, что в статье нет нужной информации.
План мог не покрыть раздел, а нормализация — не совпасть с формулировкой. Поэтому
`complete` — условие необходимости для `not_found`, но не утверждение об отсутствии.

---

## 5. Проверяемые инварианты

Проверяются в `FieldCoverageRecord` и в правилах пакета
(`validate_coverage_trail` в `src/research_platform/domain/coverage/rules.py`):

1. `coverage_status = complete` ⇒ `required_checks_completed = true`.
2. `not_found_eligible = true` ⇒ `coverage_status = complete` и
   `required_checks_completed = true`.
3. `partial` и `failed` никогда не могут быть `not_found_eligible` (при любом значении флага).
4. `not_applicable` пуст: нет `searched_sections`, `searched_page_ranges`,
   `retrieved_block_ids` и `attempts`; `full_text_fallback_performed = false`;
   `not_found_eligible = false`.
5. `complete` требует хотя бы одной записанной попытки; для `rag`/`hybrid` — ещё и
   непустого `searched_sections` или `searched_page_ranges`. Полнота без выполненного
   поиска не заявляется.
6. `search_attempt_ids` равен списку `attempts[].attempt_id` **по порядку**; дубликаты
   `attempt_id` запрещены. Trail не может ссылаться на попытки, которых в записи нет.
7. `full_text_fallback_performed = true` требует `retrieval_mode` ∈ {`rag`, `hybrid`} и
   попытку с `step_id`, начинающимся с `fallback_`, и `outcome` ∈ {`succeeded`, `no_hits`};
   один флаг без такой попытки — ошибка.
8. `retrieval_strategy` обязательна для `rag`/`hybrid` и запрещена (`null`) для остальных
   режимов: если поиска не было, стратегии быть не может.
9. В пакете — ровно 27 записей, по одной на поле карточки; дубликат `field_path`,
   неизвестный путь и пропущенное поле отвергаются.
10. `retrieval_mode` и `retrieval_strategy` каждой записи совпадают с режимом и стратегией
    запуска (`run`).
11. `searched_page_ranges` допустимы только для пагинированного документа и не выходят за
    `page_count`.
12. `not_found` в карточке ⇒ `coverage_status = complete`, `not_found_eligible = true` и —
    для `rag`/`hybrid` — `full_text_fallback_performed = true`.
13. `found` и `unclear` не могут быть `not_found_eligible`.
14. `not_applicable` статуса поля и `coverage_status = not_applicable` совпадают в обе
    стороны.

---

## 6. Правила `not_found`

1. `not_found` допустим только при `coverage_status = complete` и
   `not_found_eligible = true`.
2. При `partial` и `failed` он запрещён: незавершённый или упавший поиск не означает
   «в статье этого нет».
3. Для `rag` и `hybrid` обязателен **выполненный** полнотекстовый fallback, зафиксированный
   попыткой (`step_id` с префиксом `fallback_`, исход `succeeded` или `no_hits`).
4. Если условия не выполнены, корректный путь — `unclear` в карточке либо явная ошибка
   обработки, а не `not_found` «по умолчанию».
5. `not_found_eligible` вычисляет Python по фактическим событиям выполнения; модель это
   значение не назначает и не может назначить.

---

## 7. Как формируется trail

Trail собирается детерминированным кодом из **событий выполнения** workflow: какие запросы
и шаги плана запускались, какие разделы и диапазоны страниц были в области поиска, какие
блоки получены, какие операции fallback выполнялись, завершились ли обязательные шаги.
LLM не пишет coverage: у модели нет ни соответствующего поля, ни доступа к событиям.
Отдельный workflow (LangGraph-граф) в текущей итерации не реализован — определены модели и
правила.

---

## 8. Замечания по режимам

* `full_context` — запись фиксирует проход по всему документу и тот факт, что текст был
  передан в пределах бюджета контекста; полнотекстовый fallback для этого режима не нужен.
* `rag`/`hybrid` — для полей без подтверждения обязателен план полнотекстового fallback;
  без выполненного fallback `not_found` запрещён.
* `searched_page_ranges` — физические страницы, нумерация с 1. Для источника без
  физической пагинации (TXT) диапазоны страниц запрещены.

---

## 9. Что делает Agent 1B

Agent 1B получает trail вместе с пакетом и по контракту обязан различать четыре разных
ситуации:

| Ситуация | Основание |
|---|---|
| информации нет после полного поиска | `coverage_status = complete` + `not_found_eligible` |
| поиск неполный | `coverage_status = partial` |
| поиск не выполнен | `coverage_status = failed` |
| информация в источнике есть, но не попала в карточку | статус finding `omitted` |

Последний случай — единственный способ выразить дефект полноты: verifier нашёл материал,
которого в карточке нет. В отчёте 1.1 это опирается на `coverage_reviewed` (проверялась ли
trail вообще), а во входе — на `CoverageAvailability` (`recorded`/`not_recorded`). Вердикты
покрытия и их правила — в [контракте входа Agent 1B](agent_1b_input_contract.md).

---

## 10. Версионирование

* Trail — часть пакета Agent 1A **2.1** (`Agent1AExtractionPackageV21`, схема
  `contracts/agent_1a/v2.1/extraction_package.schema.json`).
* Замороженная схема **2.0** (`contracts/agent_1a/v2/extraction_package.schema.json`)
  coverage trail не содержит и **не должна** описываться как покрывающая поиск.
* Пакет 2.0 принимается Agent 1B с `availability = not_recorded` и без записей;
  выдумывать покрытие запрещено (`validate_coverage_is_not_claimed`).
* Версия политики — `1.0` (`COVERAGE_POLICY_VERSION`); она записывается в каждую запись и
  в `CoverageInput.policy_version` на входе Agent 1B.

Тесты: `tests/unit/test_coverage_contract.py`, `tests/contract/test_agent_1a_package.py`,
`tests/contract/test_agent_1b_contract.py`.
