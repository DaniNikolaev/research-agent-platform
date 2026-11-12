# ADR-004. Версионированные схемы и неизменяемый handoff-пакет

- **Статус:** Accepted
- **Дата:** 2026-11-12
- **Связанные требования ТЗ:** версионирование контрактов; неизменяемость передаваемого пакета;
  запрет объявления смысловой достоверности со стороны Agent 1A
- **Связанные разделы ADD:** §8.5, §12.5, §13.1–§13.6

## Контекст

ADD §13.1 требует, чтобы Agent 1B получал не один JSON-файл, а ссылку на неизменяемый пакет с
карточкой, метаданными источника, evidence ledger, результатами технической проверки и доступом к
оригиналу. ADD §13.6 требует неизменяемости и явной процедуры исправлений. ADD §12.5 запрещает
называть любой технический статус Agent 1A `scientifically_verified`.

После коррективного обзора контракты разделились на текущие (пакет Agent 1A 2.1, вход и отчёт
Agent 1B 1.1, evidence candidates 2.1) и замороженные (пакет 2.0, вход и отчёт 1.0). Legacy-схема
`card_schema.json` v1.0 (27 полей, 9 разделов) остаётся совместимым базисом для старых карточек.

## Решение

1. **Pydantic — единственный источник истины.** JSON Schema не пишется руками:
   `tools/export_schemas.py` — единственный писатель файлов `contracts/**/*.schema.json`, а
   `tests/contract/test_schema_export.py` сравнивает закоммиченные файлы с генерацией побайтово
   (`serialize_schema`: `indent=2`, `sort_keys=True`, LF). Диалект — Draft 2020-12, `$id` — локален.

2. **Контрактные модели закрыты и неизменяемы.** Все наследники `ContractModel` используют
   `frozen=True`, `extra="forbid"`, `str_strip_whitespace=True`: пакет нельзя «дополнить» полем и
   нельзя изменить после передачи.

3. **Матрица версий.**

   | Контракт | Текущая | Замороженная | Схемы и Pydantic |
   |---|---|---|---|
   | Пакет Agent 1A | `2.1` — `Agent1AExtractionPackageV21` | `2.0` — `Agent1AExtractionPackageV2` | `contracts/agent_1a/v2.1/…`; `v2/…` + `.sha256`; снимки `domain/runs/v2_0.py`, `domain/evidence/v2_0.py` |
   | Evidence candidates | `2.1` — `EvidenceCandidateList` | — | `contracts/agent_1a/v2.1/evidence_candidates.schema.json` |
   | Вход Agent 1B | `1.1` — `Agent1BInputPackageV11` | `1.0` — `Agent1BInputPackageV1` | `contracts/agent_1b/v1.1/…`; `v1/…` + `.sha256` |
   | Отчёт Agent 1B | `1.1` — `Agent1BVerificationReportV11` | `1.0` — `Agent1BVerificationReportV1` | `contracts/agent_1b/v1.1/…`; `v1/…` + `.sha256` |

4. **Заморозка доказана, а не декларирована.** Замороженные схемы по-прежнему генерируются из
   замороженных Pydantic-снимков, а их байты закреплены sidecar-файлами `.sha256` и проверяются в
   `tests/contract/test_frozen_schemas.py`. Скоординированная правка модели и схемы прошла бы обычное
   сравнение, но провалила бы проверку контрольной суммы; изменить опубликованную версию нельзя.

5. **Совместимость версий.** `SUPPORTED_CONTRACT_VERSIONS = ("2.0", "2.1")` в пакете 2.1;
   `SUPPORTED_AGENT_1A_CONTRACT_VERSIONS = ("2.0", "2.1")` во входе Agent 1B;
   `COVERAGE_RECORDED_CONTRACT_VERSIONS = ("2.1",)`. Объявленная версия выбирает форму run и evidence
   до валидации, поэтому 2.0 не может выглядеть как 2.1; `run.schema_version` обязан совпадать с
   `contract_version` пакета и `agent_1a_contract_version` входа.

6. **Научный вердикт существует только у Agent 1B.** `scientific_review_status` есть только в
   контракте отчёта Agent 1B — текущем `Agent1BVerificationReportV11` и замороженном
   `Agent1BVerificationReportV1`; в пакете Agent 1A поля для него нет. Agent 1A выполняет извлечение,
   выставляет extraction-статусы, создаёт evidence, технический validation report и coverage trail.
   `SemanticVerificationStatus` содержит только `not_performed` и `pending_agent_1b` — члена `verified`
   в enum нет, а `SemanticValidation.independent_verification_required` зафиксирован как `True`.

7. **`verified` в отчёте 1.1 ограничен findings.** `scientific_review_status="verified"` требует
   непустой список findings, все статусы `verified` и отсутствие `coverage_verdict="disputed_complete"`;
   любой `not_checked`, `not_verifiable`, `partially_supported`, `unsupported`, `contradicted` или
   `omitted` блокирует `verified`. Статусы `unsupported`/`contradicted`/`omitted` обязаны иметь
   severity `major`/`critical`, `omitted` дополнительно требует evidence проверяющего; `not_checked` не
   может нести evidence; `verified_with_issues` требует хотя бы один не-`verified` finding, а
   `human_review_required` — хотя бы один critical finding.

8. **Legacy v1.0 — замороженная копия.** `contracts/legacy/agent_1a_v1/card.schema.json` побайтово
   скопирован; SHA-256 записан в `card.schema.json.sha256` и сверяется тестом. Legacy-схема не
   используется как схема v2.

## Статус

Accepted. Текущие контракты — 2.1 и 1.1; 2.0 и 1.0 заморожены и проверяются отдельно. Изменение
смысла поля, статуса или правила допускается только новой версией контракта и новым ADR.

## Последствия

Положительные: сторонний потребитель валидирует пакет по JSON Schema без Pydantic; расхождение кода и
схемы обнаруживается на CI, а не в интеграции; история контрактов неизменяема и проверяема.

Цена: любое изменение схемы — регенерация и коммит; `frozen=True` требует `model_copy(update=…)`;
версия в `Literal` означает, что поддержка 2.2/1.2 потребует новой модели, а не только константы.

## Альтернативы

- **Schema-first с генерацией кода (`datamodel-code-generator`).** Отклонено: теряются валидаторы
  уровня модели, обеспечивающие инварианты evidence и пагинации.
- **Protobuf/Avro.** Отклонено: пакет должен оставаться читаемым JSON-документом для человека и Agent 1B.
- **Неверсионированные `dict` + `jsonschema`.** Отклонено: версия перестала бы быть частью данных.

## Открытые риски

1. Поддержка следующей версии не отработана: `contract_version` — `Literal["2.1"]`, поэтому 2.2
   потребует новой модели или union-типа. Владелец: архитектор, при первом изменении контракта.
2. `$id` в локальном пространстве имён не резолвится снаружи; внешний потребитель обязан получить файл.
3. Схемы экспортируются в `mode="validation"`; различия validation/serialization не покрыты тестами.
4. Адаптер v1 → v2 существует только как интерфейс; миграция legacy-карточек не проверена на архиве.
