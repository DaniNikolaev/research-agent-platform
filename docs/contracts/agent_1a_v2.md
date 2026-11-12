# Контракт пакета Agent 1A 2.1

**Версия контракта:** `2.1` · **Модель:** `Agent1AExtractionPackageV21` · **Схема:** [`contracts/agent_1a/v2.1/extraction_package.schema.json`](../../contracts/agent_1a/v2.1/extraction_package.schema.json) · **Дата:** 2026-11-12

Пакет — единственный штатный способ передать результат Agent 1A дальше. Он
самодостаточен: читается и проверяется офлайн, без БД, LLM и файловой системы.

---

## 1. Состав пакета

```json
{
  "contract_version": "2.1",
  "package_id": "pkg_20261112_120000",
  "document":    { "...": "DocumentManifest" },
  "run":         { "...": "RunMetadata" },
  "card":        { "...": "Agent1ACardV2 — 9 разделов, 27 полей" },
  "evidence_ledger": [ "..." ],
  "coverage_trail":  [ "..." ],
  "validation":  { "...": "ValidationReport" },
  "artifacts":   [ "..." ]
}
```

| Секция | Модель | Что фиксирует |
|---|---|---|
| `document` | `DocumentManifest` | идентичность и происхождение оригинала: `document_id`, имя файла, media type, язык, число физических страниц, `source_sha256`, `original_artifact_uri` |
| `run` | `RunMetadata` | воспроизводимость запуска: `run_id`, версия агента, промпта и схемы, провайдер/модель, отпечаток конфигурации модели, парсер, времена начала/окончания (UTC), `retrieval_mode` и `retrieval_strategy`, provenance индекса |
| `card` | `Agent1ACardV2` | предметный результат: 9 разделов и 27 полей |
| `evidence_ledger` | `list[EvidenceRecord]` | канонические подтверждения (источник истины) |
| `coverage_trail` | `list[FieldCoverageRecord]` | по каждому из 27 полей — какой поиск фактически выполнен ([контракт Coverage Trail](coverage_trail.md)) |
| `validation` | `ValidationReport` | техническая валидация и место семантической проверки |
| `artifacts` | `list[ArtifactReference]` | все артефакты запуска: логические URI, media type, размер, SHA-256, версия |

`coverage_trail` обязательна: не менее 1 и не более 64 записей, фактически — ровно 27,
по одной на поле карточки. Минимальный набор артефактов (проверяется кодом):
`original_document`, `parsed_document`, `evidence_ledger`, `validation_report`. Кроме того,
`document.original_artifact_uri` обязан присутствовать в `artifacts` — иначе независимая
проверка невозможна.

### Режим и стратегия retrieval

| Поле `run` | Тип | Обяз. | Правило |
|---|---|---|---|
| `retrieval_mode` | enum | да | `full_context`, `rag`, `hybrid`, `not_applicable` |
| `retrieval_strategy` | enum или `null` | нет | `dense`, `lexical`, `dense_lexical`; обязательна ровно для `rag`/`hybrid` и запрещена для остальных режимов |
| `embedding_model_id` | строка или `null` | нет | обязателен для `rag`/`hybrid`, запрещён иначе |
| `index_version` | строка или `null` | нет | обязателен для `rag`/`hybrid`, запрещён иначе |

`hybrid` — это краткий глобальный контекст плюс целевые фрагменты, а не способ протолкнуть
полный текст большой статьи за бюджет контекста (ADR-007). Режим выбирает детерминированная
политика Python, а не LLM.

---

## 2. Девять разделов и 27 содержательных полей

Имена разделов, полей и типов унаследованы от legacy `card_schema.json` v1.0 **без
смысловых изменений**. Полное соответствие — в
[`docs/migration/v1_to_v2_mapping.md`](../migration/v1_to_v2_mapping.md), равенство
множеств и типов проверяется тестом `tests/contract/test_legacy_compat.py`.

| # | Раздел | Поле | Тип | Тип значения |
|---:|---|---|---|---|
| 1 | `bibliography` | `title` | `textField` | строка |
| 2 | `bibliography` | `authors` | `stringListField` | массив строк |
| 3 | `bibliography` | `year` | `yearField` | целое 1500–2100 |
| 4 | `bibliography` | `journal` | `textField` | строка |
| 5 | `bibliography` | `doi` | `textField` | строка |
| 6 | `bibliography` | `keywords` | `stringListField` | массив строк |
| 7 | `research_design` | `research_problem` | `textField` | строка |
| 8 | `research_design` | `research_objective` | `textField` | строка |
| 9 | `research_design` | `research_questions` | `stringListField` | массив строк |
| 10 | `research_design` | `hypotheses` | `stringListField` | массив строк |
| 11 | `research_design` | `theoretical_framework` | `stringListField` | массив строк |
| 12 | `research_design` | `literature_context` | `textField` | строка |
| 13 | `methodology` | `methodology` | `stringListField` | массив строк |
| 14 | `data` | `sample` | `textField` | строка |
| 15 | `data` | `period` | `textField` | строка |
| 16 | `data` | `geographical_scope` | `textField` | строка |
| 17 | `data` | `data_sources` | `stringListField` | массив строк |
| 18 | `data` | `observations` | `textField` | строка |
| 19 | `variables` | `dependent` | `stringListField` | массив строк |
| 20 | `variables` | `independent` | `stringListField` | массив строк |
| 21 | `variables` | `controls` | `stringListField` | массив строк |
| 22 | `variables` | `other_variables` | `stringListField` | массив строк |
| 23 | `findings` | `main_findings` | `stringListField` | массив строк |
| 24 | `findings` | `authors_conclusions` | `stringListField` | массив строк |
| 25 | `limitations` | `limitations` | `stringListField` | массив строк |
| 26 | `future_research` | `future_research` | `stringListField` | массив строк |
| 27 | `contribution` | `contribution` | `stringListField` | массив строк |

Группы извлечения ADD §8.3 (G1 библиография, G2 дизайн, G3 методы/данные/переменные,
G4 результаты и следствия) остаются логической группировкой workflow, а не структурой
карточки.

---

## 3. Поле карточки v2

Каждое поле имеет вид:

```json
{
  "value": "...",
  "status": "found",
  "evidence_refs": [{"evidence_id": "ev_000001"}]
}
```

Отличия от v1 (осознанные, версия контракта повышена):

1. вместо встроенного массива `evidence` (с координатами от модели) — ссылки
   `evidence_refs` на `evidence_id` общего ledger;
2. `quote`, `page`, `section_path` в ссылке допустимы только как денормализованные
   значения для чтения, и обязаны **побайтово совпадать** с ledger (проверяется при
   валидации пакета) — источником истины остаётся ledger;
3. `evidence_refs` не может содержать дубликат `evidence_id` внутри одного поля.

---

## 4. Правила статусов (проверяются кодом)

| Статус | `value` | `evidence_refs` | Дополнительно |
|---|---|---|---|
| `found` | непустое | ≥ 1 ссылка | каждая ссылка разрешается в ledger; `not_found_eligible = false` |
| `not_found` | `null` или `[]` | пусто | требуется `coverage_status = complete` и `not_found_eligible = true`; для `rag`/`hybrid` — выполненный полнотекстовый fallback |
| `not_applicable` | `null` или `[]` | пусто | требуется `coverage_status = not_applicable`; применять сдержанно |
| `unclear` | частичное значение **или** `null` | ссылки **или** пусто | хотя бы одно из двух обязано быть непустым; `not_found_eligible = false` |

`unclear` нельзя использовать как способ скрыть ошибку формата: поле без значения и без
подтверждений — это `not_found`, а не `unclear`. Обратное тоже верно: `unclear` признан
явной передачей неопределённости и не блокирует пакет. `not_found` больше не «ответственность
workflow»: он проверяется по coverage trail, поэтому пустой retrieval его не обосновывает.

---

## 5. Секция `validation`

```json
{
  "technical_status": "valid",
  "technical": {
    "schema_valid": true,
    "validator_name": "research-platform.contract-validator",
    "validator_version": "0.1.0",
    "performed_at": "2026-11-12T12:04:30Z",
    "checks": [], "errors": [], "warnings": []
  },
  "semantic": { "status": "pending_agent_1b", "independent_verification_required": true, "notes": [] }
}
```

* `technical_status` ∈ `valid` | `valid_with_warnings` | `blocked` | `failed`
  (терминология ADD §13.5) и согласован с содержимым: `valid` не может нести ошибки
  или предупреждения, `valid_with_warnings` требует хотя бы одного предупреждения,
  `blocked`/`failed` требуют ошибок, а провалидировать схему заново нельзя.
* `semantic.status` ∈ `not_performed` | `pending_agent_1b`. Значения `verified`,
  `semantically_verified` и подобные **отсутствуют в перечислении**: Agent 1A не может
  объявить карточку семантически проверенной. `independent_verification_required`
  зафиксировано как константа `true`.
* `scientific_review_status` в пакете Agent 1A **отсутствует**; это поле есть только в
  отчёте Agent 1B.

---

## 6. Инварианты пакета

Проверяются в `Agent1AExtractionPackageV21`, в общих правилах
`domain/evidence/ledger.py` и в `domain/coverage/rules.py`:

1. `evidence_id` уникальны в пределах пакета;
2. каждый record принадлежит `document.document_id` и **точно** его `source_sha256`
   (подтверждение нельзя перенести на другую версию файла);
3. каждая ссылка карточки разрешается в record; денормализованные копии совпадают;
4. пагинация: при заданном `page_count` у каждого record обязана быть `page` в
   диапазоне `1..page_count`; при `page_count = null` (TXT) `page` обязана быть `null`;
5. `run.schema_version` равен `contract_version`;
6. артефакты: уникальные `artifact_id`, наличие минимального набора, принадлежность
   тому же документу и тому же run, наличие `original_artifact_uri`;
7. `page_count = null` допустим только для `text/plain`;
8. `coverage_trail`: ровно 27 записей, по одной на поле; `retrieval_mode` и
   `retrieval_strategy` записи совпадают с `run`; `not_found` требует
   `complete` + `not_found_eligible` (+ выполненный fallback для `rag`/`hybrid`);
   `not_applicable` статуса поля и `coverage_status` совпадают в обе стороны.

---

## 7. Версии

| Версия | Статус | Модель и схема |
|---|---|---|
| `2.1` | текущая | `Agent1AExtractionPackageV21`, `contracts/agent_1a/v2.1/extraction_package.schema.json` |
| `2.0` | замороженная история | `Agent1AExtractionPackageV2`, `contracts/agent_1a/v2/extraction_package.schema.json` + `.sha256` |

Версия 2.0 остаётся валидной под своим контрактом: в ней нет `coverage_trail`, нет режима
`hybrid` и нет `retrieval_strategy`, а evidence-записи имеют замороженную форму
([контракт Evidence](evidence_contract.md), §10). Замороженные схемы генерируются из
замороженных Pydantic-снимков и защищены контрольными суммами; вторая версия не
«дообогащается» полями 2.1.

CLI выбирает модель по объявленной версии: `PACKAGE_VALIDATORS` в
`src/research_platform/cli.py` содержит обе — `"2.0"` и `"2.1"`. Неизвестная версия — это
нарушение контракта с диагностикой, называющей поддерживаемые версии, а не попытка
провалидировать «по последней».

---

## 8. Пример

Полный валидный пакет 2.1:
[`tests/fixtures/agent_1a_package_v2_1_valid.json`](../../tests/fixtures/agent_1a_package_v2_1_valid.json).
Замороженный пример 2.0 — `tests/fixtures/agent_1a_package_v2_valid.json`.

```powershell
uv run research-platform contracts validate --input tests\fixtures\agent_1a_package_v2_1_valid.json
# valid: tests\fixtures\agent_1a_package_v2_1_valid.json satisfies Agent1AExtractionPackageV21 (contract 2.1)
```

Невалидный пакет возвращает код `1` и путь до поля:

```text
invalid: package.json does not satisfy Agent1AExtractionPackageV21
  card.bibliography.title: Value error, status 'found' requires at least one evidence reference [value_error]
```

Коды возврата: `0` — валиден, `1` — нарушение контракта (включая неизвестную
`contract_version`), `2` — файл не прочитан или не является JSON.

---

## 9. Границы контракта

Контракт **не** утверждает, что значения полей верны с научной точки зрения, и не
заменяет проверку первоисточника. Он гарантирует: структуру, типы, наличие и
прослеживаемость подтверждений, корректную пагинацию, воспроизводимость метаданных
запуска, обоснованность `not_found` покрытием поиска и техническую согласованность пакета.

Смысловая поддержка утверждений проверяется независимо: Agent 1B
([контракт входа](agent_1b_input_contract.md)) и человеком.
