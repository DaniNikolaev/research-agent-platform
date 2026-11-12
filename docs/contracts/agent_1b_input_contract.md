# Контракт входа Agent 1B 1.1 и отчёта проверки 1.1

**Версии:** вход `1.1`, отчёт `1.1` · **Модели:** `Agent1BInputPackageV11`, `Agent1BVerificationReportV11` · **Схемы:** [`input_package.schema.json`](../../contracts/agent_1b/v1.1/input_package.schema.json), [`verification_report.schema.json`](../../contracts/agent_1b/v1.1/verification_report.schema.json) · **Дата:** 2026-11-12

Agent 1B — отдельный workflow, который проверяет карточку Agent 1A **по
первоисточнику**, самостоятельно и независимо. Определены контракты; workflow не
реализован.

---

## 1. Что получает Agent 1B

```json
{
  "contract_version": "1.1",
  "package_id": "pkg_1b_input_0001",
  "agent_1a_contract_version": "2.1",
  "agent_1a_package_id": "pkg_20261112_120000",
  "document":  { "...": "DocumentManifest" },
  "source_access": { "original_artifact_uri": "artifact://...", "source_sha256": "...", "read_only": true },
  "run":       { "...": "RunMetadata Agent 1A" },
  "card":      { "...": "Agent1ACardV2 — неизменяемая карточка" },
  "evidence_ledger": [ "..." ],
  "coverage":  { "availability": "recorded", "source_contract_version": "2.1", "policy_version": "1.0", "records": [ "..." ] },
  "validation": { "...": "технический отчёт Agent 1A" },
  "artifacts": [ "..." ],
  "mutation_policy": { "card_mutation_allowed": false, "corrections_require_new_package": true }
}
```

| Секция | Зачем она нужна проверяющему |
|---|---|
| `document` | идентификатор, SHA-256 и `original_artifact_uri`: какой именно файл проверять |
| `source_access` | явное право **только на чтение** оригинала; обязательное, без него независимая проверка невозможна |
| `run` | происхождение генерации 1A: модель, промпт, парсер, режим и стратегия retrieval |
| `card` | проверяемая карточка; Agent 1B её не изменяет |
| `evidence_ledger` | подтверждения 1A, на которые ссылается карточка |
| `coverage` | учёт выполненного поиска по каждому полю ([контракт Coverage Trail](coverage_trail.md)) |
| `validation` | что 1A уже проверил технически (и что осталось непроверенным) |
| `artifacts` | доступ к parsed-документу, ledger и отчёту валидации |
| `mutation_policy` | машинночитаемое «не изменять»; литерал `false`, обойти нельзя |

Обе агентские схемы не импортируют друг друга: обе собираются из общих `domain`-моделей.
Поэтому вход 1B повторно объявляет композицию, а не импортирует тип пакета 1A.

### Жёсткие проверки

1. `agent_1a_contract_version` обязан входить в
   `SUPPORTED_AGENT_1A_CONTRACT_VERSIONS = ("2.0", "2.1")`. Иначе — отказ с диагностикой,
   называющей поддерживаемые версии.
2. `source_access.original_artifact_uri` обязан совпадать с
   `document.original_artifact_uri`, а `source_access.source_sha256` — с
   `document.source_sha256`. Проверять другой файл «примерно того же содержания» нельзя.
3. `source_access` обязан ссылаться на артефакт типа `original_document`, а не на
   parsed-версию.
4. `mutation_policy.card_mutation_allowed` — литерал `false`; значение `true` отвергается
   валидацией.
5. Обязательные типы артефактов: `original_document`, `evidence_ledger`,
   `validation_report`; ссылка на оригинал обязана присутствовать в `artifacts`.
6. Все правила ledger/пагинации/ссылок — те же, что у пакета 1A (общий модуль
   `domain/evidence/ledger.py`), поэтому «расхождение на границе» невозможно.

---

## 2. Секция `coverage`

| Поле `CoverageInput` | Тип | Обяз. | Правило |
|---|---|---|---|
| `availability` | enum | да | `recorded` или `not_recorded` |
| `source_contract_version` | строка | да | версия контракта Agent 1A, из которого пришёл пакет |
| `policy_version` | строка или `null` | нет | шаблон `^\d+\.\d+$`; обязательна при `recorded`, запрещена при `not_recorded` |
| `records` | массив `FieldCoverageRecord` | нет | ≤ 64; при `recorded` — ровно 27, при `not_recorded` — пусто |

Правила (проверяются `validate_coverage_is_not_claimed`):

* пакет 2.1 — всегда `recorded`: ровно 27 записей, по одной на поле карточки, и
  обязательная `policy_version` (текущая политика — `1.0`);
* пакет 2.0 — всегда `not_recorded`: записей нет и `policy_version` нет. Для 2.0
  покрытие не существовало, и его нельзя выдумать;
* `not_recorded` с записями, `recorded` с чужим числом записей или `recorded` для версии,
  которая trail не знала, — нарушение контракта, а не «мягкое предупреждение».

---

## 3. Согласование версий

1. Поддерживаются версии пакета Agent 1A `2.0` и `2.1`; другая версия — отказ,
   называющий поддерживаемые.
2. Форма `run` и `evidence_ledger` следует **полученной** версии: 2.0 сохраняет форму 2.0
   (нет `retrieval_strategy`, нет режима `hybrid`, нет `match_type`, нет
   `occurrence_index`), 2.1 — форму 2.1.
3. Молчаливого повышения версии нет: 2.0-пакет не может быть прочитан как 2.1. Форма
   выбирается по `coverage.source_contract_version` до валидации, потому что 2.0-run
   структурно является подмножеством 2.1 и иначе прошёл бы валидацию как «более новый».
4. `run.schema_version` обязан равняться `agent_1a_contract_version`, а
   `coverage.source_contract_version` — тому же значению.
5. Evidence-форма сверяется с версией: 2.0-записи не подменяются 2.1-записями и наоборот.

---

## 4. Что Agent 1B обязан и не вправе делать

Обязан:

* самостоятельно читать исходную статью (оригинал по `source_access`);
* независимо проверять содержательные утверждения и evidence — не доверяя ledger 1A;
* использовать coverage trail, чтобы отличать «нет в статье после полного поиска» от
  «поиск неполный», «поиск не выполнен» и «есть в источнике, но не в карточке»;
* выявлять пропуски, противоречия, ошибки интерпретации и чисел;
* возвращать собственный отчёт проверки и список findings;
* при необходимости инициировать **отдельную** процедуру корректировки с новой версией
  пакета, сохраняя историю.

Не вправе:

* переписывать или модифицировать пакет Agent 1A и его карточку;
* «исправлять на месте» найденные ошибки;
* считать `technical_status = valid` доказательством смысловой корректности;
* подтверждать утверждение без собственного evidence;
* объявлять покрытие за Agent 1A, если trail не записан.

---

## 5. Отчёт проверки 1.1

```json
{
  "contract_version": "1.1",
  "report_id": "rep_1b_0001",
  "agent_1a_package_id": "pkg_20261112_120000",
  "agent_1a_contract_version": "2.1",
  "agent_id": "1B",
  "agent_version": "0.1.0",
  "prompt_version": "1b-prompt-1.0.0",
  "model_provider": "...", "model_id": "...", "model_configuration_fingerprint": "...",
  "started_at": "...", "completed_at": "...",
  "scientific_review_status": "verified_with_issues",
  "coverage_reviewed": true,
  "summary": "...",
  "findings": [ "..." ]
}
```

### Статусы findings (семь значений, закрытый набор)

| Статус | Значение |
|---|---|
| `verified` | утверждение подтверждено собственным evidence проверяющего |
| `partially_supported` | источник подтверждает часть утверждения |
| `unsupported` | подтверждения нет, но и противоречия нет |
| `contradicted` | источник утверждает несовместимое (знак, число, выборка, направление) |
| `omitted` | в источнике есть, в карточке нет — дефект полноты |
| `not_verifiable` | проверить невозможно имеющимися материалами; это ограничение, а не «пройдено» |
| `not_checked` | поле не проверялось; **никогда** не считается подтверждённым |

### Правила согласованности

1. `verified` требует хотя бы одного `agent_1b_evidence`.
2. `not_checked` не может нести evidence — для «не смог проверить» есть `not_verifiable`.
3. `unsupported`, `contradicted` и `omitted` обязаны иметь severity `major` или `critical`.
4. `omitted` дополнительно требует собственного evidence проверяющего: дефект полноты
   обязан показывать материал, который карточка пропустила.
5. На одно поле — ровно один вердикт в отчёте; `finding_id` уникальны.
6. `agent_1a_evidence_ids` обязаны существовать в проверяемом ledger
   (`validate_findings_against_ledger`).
7. `scientific_review_status = verified` требует: findings непусты, **все** они
   `verified` и среди них нет `coverage_verdict = disputed_complete`.
8. `verified_with_issues` требует хотя бы одного не-`verified` finding.
9. `human_review_required` требует хотя бы одного `critical` finding.

### Вердикты покрытия

| `coverage_verdict` | Значение |
|---|---|
| `confirmed_complete` | проверяющий подтверждает заявленный полный поиск |
| `confirmed_partial` | проверяющий подтверждает, что поиск был неполным |
| `disputed_complete` | 1A заявил полный поиск, проверяющий нашёл материал вне его |
| `coverage_not_recorded` | полученный пакет trail не содержит, судить о покрытии нельзя |

Правила: `disputed_complete` требует собственного evidence проверяющего; любой
`coverage_verdict` требует `coverage_reviewed = true` — объявить вердикт означает, что
trail действительно просмотрена; отчёт со статусом `verified` не может содержать
`disputed_complete`.

### Статусы проверки пакета

`scientific_review_status` ∈ `pending` | `in_review` | `verified_with_issues` |
`verified` | `human_review_required` (ADD §13.5). Важно: в пакете Agent 1A этого поля
нет вообще — оно появляется только в отчёте 1B. Разделение намеренное: технически
валидный пакет 1A всегда передаётся с `semantic.status = pending_agent_1b`.

---

## 6. Немутируемость

* Все контрактные модели `frozen=True`: изменение карточки после получения — ошибка,
  а не «неосторожность».
* Тест `test_input_validation_does_not_mutate_the_source_package` подтверждает, что
  валидация не изменяет входной словарь (сравнение с глубокой копией).
* Корректировка — новая версия пакета (новый `run_id`) + повторная проверка; предыдущая
  карточка и предыдущий отчёт сохраняются (ADD §13.6).

---

## 7. Замороженные 1.0

Вход `1.0` (`contracts/agent_1b/v1/input_package.schema.json`) и отчёт `1.0`
(`contracts/agent_1b/v1/verification_report.schema.json`) — история: у входа нет секции
`coverage`, у отчёта нет статуса `omitted`, нет `coverage_verdict` и нет
`coverage_reviewed`. Вход 1.0 принимает только пакет 2.0
(`SUPPORTED_AGENT_1A_CONTRACT_VERSIONS = ("2.0",)`). Замороженные схемы защищены
контрольными суммами и не редактируются.

---

## 8. Примеры и тесты

* примеры: `tests/fixtures/agent_1b_input_v1_1_valid.json`,
  `tests/fixtures/agent_1b_report_v1_1_valid.json`; замороженные —
  `tests/fixtures/agent_1b_input_v1_valid.json`,
  `tests/fixtures/agent_1b_report_v1_valid.json`;
* тесты: `tests/contract/test_agent_1b_contract.py` (все семь статусов findings,
  `omitted`, вердикты покрытия, согласование версий 2.0/2.1, отказ в неизвестной версии,
  отсутствие мутации, замороженные 1.0).
