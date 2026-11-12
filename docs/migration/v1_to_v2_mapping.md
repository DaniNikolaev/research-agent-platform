# Отображение карточки Agent 1A v1.0 → пакет 2.1

**Целевой контракт:** `2.1` · **Дата:** 2026-11-12 · **Интерфейс:** `Agent1ACardV1ToV2Adapter` (`src/research_platform/agents/agent_1a/contracts/adapter.py`) · **Статус:** спецификация; миграция не выполняется

Документ описывает, **что** и **как** переносится из legacy-карточки v1.0 в пакет 2.1,
и — главное — чего в legacy-карточке нет, а значит, и не может быть перенесено без
домысливания. Соответствие полей карточки от версии контракта не зависит: оно одно и то же
для 2.0 и 2.1, различаются только провенанс evidence и наличие покрытия поиска.

---

## 1. Что переносится без изменения смысла

| v1.0 | 2.1 | Комментарий |
|---|---|---|
| `bibliography.*` (6 полей) | `card.bibliography.*` | имена, типы и статусы сохраняются |
| `research_design.*` (6) | `card.research_design.*` | без изменений |
| `methodology` | `card.methodology` | `stringListField` |
| `data.*` (5) | `card.data.*` | без изменений |
| `variables.*` (4) | `card.variables.*` | без изменений |
| `findings.*` (2) | `card.findings.*` | без изменений |
| `limitations`, `future_research`, `contribution` | одноимённые поля | без изменений |
| статусы `found` / `not_found` / `unclear` / `not_applicable` | `FieldStatus` | словарь сохранён дословно |
| типы `textField` / `stringListField` / `yearField` | `TextField` / `StringListField` / `YearField` | равенство множеств и типов проверяется тестом |
| `source.file_name` | `document.source_file_name` | имя файла без пути |
| `source.file_type` (`pdf`/`txt`) | `document.source_media_type` (`application/pdf`/`text/plain`) | нормализовано к MIME |
| `source.language` | `document.language` | `ru`/`en`/`unknown` |
| `source.total_pages` | `document.page_count` | `null` допустим только для `text/plain` |
| `source.ocr_used` | `run.*` / parsed-артефакт | в 2.1 признак OCR относится к парсингу, а не к карточке |
| `document_id` | `document.document_id` | шаблон `^[a-z0-9][a-z0-9_\-]{2,63}$` сохранён |
| `processing.agent` = `1A` | `run.agent_id` = `1A` | |
| `processing.agent_version` | `run.agent_version` | |
| `processing.schema_version` = `1.0` | `run.schema_version` = `2.1` | версия схемы карточки заменена версией контракта пакета |
| `processing.processed_at` | `run.completed_at` | v1 хранил локальное время со смещением; 2.1 требует UTC |
| `processing.warnings` | `validation.technical.warnings` + `run`-контекст | предупреждения становятся структурированными |
| `processing.needs_human_review` | `validation.technical_status` / статусы 1B | в 2.1 решение о ручной проверке формализовано статусами |
| `processing.extraction_status` | `validation.technical_status` | `completed`→`valid`, `completed_with_warnings`→`valid_with_warnings`, `validation_failed`/`failed`→`blocked`/`failed` |
| `evidence[].page` | `EvidenceRecord.page` | физическая страница, с 1 |
| `evidence[].section` | `EvidenceRecord.section_path` | строка → массив уровней (один элемент, если вложенность неизвестна) |
| `evidence[].quote` | `EvidenceRecord.quote` | дословный текст, язык источника |
| `evidence[].location` | — | в 2.1 заменено `section_path` + `char_start`/`char_end` |

---

## 2. Что меняется структурно

1. **Evidence выносится в ledger.** В v1 каждый объект evidence жил внутри поля и нёс
   `page`/`section`/`quote`, назначенные моделью. В 2.1 поле ссылается на `evidence_id`
   общего `evidence_ledger`, а координаты и `source_sha256` назначает Python.
   Денормализованные `quote`/`page`/`section_path` в ссылке допустимы, но обязаны
   совпадать с ledger.
2. **Появляется словарь разрешения.** Вместо одного «статуса цитаты» v1 запись несёт
   `match_status` (`resolved`/`ambiguous`/`unresolved`) и `match_type`
   (`exact`/`normalized`/`fuzzy`/`none`) плюс `verification_method`; `fuzzy` никогда не
   повышается до `resolved`. Запись также несёт `occurrence_index` и провенанс
   парсера/резолвера — этого v1 не фиксировал.
3. **Появляется версия контракта и идентичность пакета** (`contract_version`,
   `package_id`).
4. **Появляется секция `run`** с моделью, промптом, парсером, режимом и стратегией
   retrieval и версией индекса.
5. **Появляется секция `artifacts`** с checksum-ами всех артефактов.
6. **Появляется `validation`** с разделением технической проверки и семантической
   (последняя у Agent 1A всегда `not_performed` / `pending_agent_1b`).
7. **Появляется `coverage_trail`** — по одной записи на каждое из 27 полей: статус
   покрытия, область поиска, попытки, факт выполнения fallback
   ([контракт Coverage Trail](../contracts/coverage_trail.md)). `not_found` в карточке
   допустим только при `complete` + `not_found_eligible`.
8. **Все модели неизменяемы** (`frozen=True`) и закрыты (`extra="forbid"`).

---

## 3. Чего в v1 нет и что нельзя придумывать

| Требуется в 2.1 | Есть в v1? | Поведение адаптера |
|---|---|---|
| `document.source_sha256` | нет (SHA-256 вычислялся для `document_id`, но в карточку не попадал) | сообщить как отсутствующее |
| `run.run_id` | нет (run id существовал только в журнале запуска) | сообщить как отсутствующее |
| `run.prompt_version`, `run.model_provider`, `run.model_id` | нет | сообщить как отсутствующее |
| `run.model_configuration_fingerprint` | нет | сообщить как отсутствующее |
| `run.parser_name`, `run.parser_version` | нет (PyMuPDF + pytesseract, версии не фиксировались) | сообщить как отсутствующее |
| `run.started_at`, `run.completed_at` | частично: `processing.processed_at` = момент сборки, не запуска | требуется подтверждение; иначе сообщить как отсутствующее |
| `EvidenceRecord.block_id` | нет (блоки не выделялись, текст хранился постранично) | сообщить как отсутствующее |
| `EvidenceRecord.char_start`/`char_end` | нет | сообщить как отсутствующее |
| `EvidenceRecord.bbox` | нет | допустимо `null` |
| `EvidenceRecord.match_status` / `match_type` | нет (проверка цитат возвращала предупреждение, но не сохраняла ни статус, ни тип совпадения в карточке) | сообщить как отсутствующее |
| `EvidenceRecord.verification_method` | нет | сообщить как отсутствующее |
| `EvidenceRecord.occurrence_index` | нет (порядковый номер вхождения не фиксировался) | сообщить как отсутствующее |
| `EvidenceRecord.parser_name` / `parser_version` / `resolver_version` | нет | сообщить как отсутствующее |
| `EvidenceRecord.source_sha256` | нет | сообщить как отсутствующее |
| `coverage_trail` (27 записей) | нет: в v1 нет понятия покрытия — не было ни плана поиска, ни попыток, ни факта fallback | сообщить как отсутствующее; выдумывать покрытие запрещено |
| `artifacts[]` (parsed, ledger, validation report) | нет как сущностей | сообщить как отсутствующее (parsed-текст существует, но без checksum и без ссылочной модели) |

---

## 4. Правила адаптера

```python
class Agent1ACardV1ToV2Adapter(Protocol):
    def adapt(
        self, card_v1: Mapping[str, Any], *, provenance: Mapping[str, Any]
    ) -> AdapterResult: ...
```

`AdapterResult`:

```json
{
  "outcome": "convertible | missing_metadata | rejected",
  "missing_required": ["source_sha256", "run.run_id"],
  "package": { "...": "Agent1AExtractionPackageV2 | null" },
  "notes": ["..."]
}
```

Правила, проверяемые самой моделью результата:

1. `outcome = convertible` требует непустой `package` и пустой `missing_required`;
2. `outcome = missing_metadata` требует непустой `missing_required` и **запрещает**
   `package` — неполные метаданные не «дополняются» догадками;
3. `outcome = rejected` — неизвестное поле или неизвестный статус: диагностика в
   `notes`, без молчаливого отбрасывания данных.

`missing_required_provenance(provenance)` возвращает список отсутствующих
обязательных путей; пустая строка и `None` считаются отсутствием, то есть
плейсхолдерами контракт не обходится.

Обязательные пути (`REQUIRED_V1_PROVENANCE`): `source_sha256`, `run.run_id`,
`run.agent_version`, `run.prompt_version`, `run.schema_version`, `run.model_provider`,
`run.model_id`, `run.model_configuration_fingerprint`, `run.parser_name`,
`run.parser_version`, `run.started_at`, `run.completed_at`,
`evidence_provenance.source_sha256`, `evidence_provenance.block_id`,
`evidence_provenance.char_span`, `evidence_provenance.match_status`.

Оговорка о целевом контракте: интерфейс и правила адаптера описывают получение
v2-пакета, а тип `package` в `AdapterResult` сегодня — замороженная модель
`Agent1AExtractionPackageV2` (контракт 2.0). Пакет 2.1 требует ещё и `coverage_trail`,
которого из legacy-карточки получить нельзя, поэтому адаптер принципиально не может
изготовить 2.1-пакет без отдельного запуска поиска и его событий.

Legacy JSON при адаптации **не изменяется**: вход только читается.

---

## 5. Обратная совместимость

* Legacy `card_schema.json` v1.0 хранится побайтово
  (`contracts/legacy/agent_1a_v1/card.schema.json`, SHA-256
  `0c7075d2adba9ea6f3e148f1110f995f833b87028ced9d7145c8d1e2ace8bbf1`) и продолжает
  валидировать старые карточки; она не используется как схема 2.1.
* Проверено на реальном артефакте legacy:
  `tests/fixtures/legacy/card_v1_en_article.json` проходит собственную v1-схему.
* Несовместимое изменение v1-схемы запрещено: любое изменение смысла — только через
  новую версию контракта (ADR-004).

---

## 6. Практический вывод для миграции

Полное восстановление пакета 2.1 **из одной legacy-карточки невозможно**: отсутствуют
хеш источника, идентичность запуска и provenance уровня блока. К этому добавляется второе,
уже непреодолимое ограничение: **покрытие поиска нельзя восстановить ретроспективно** —
в v1 не было ни плана поиска, ни попыток, ни событий fallback, а `coverage_trail` 2.1
описывает именно фактически выполненный поиск. Придумать 27 записей покрытия нельзя:
`not_found_eligible` вычисляется по событиям выполнения, а не декларируется.

Реалистичные варианты:

1. **Перезапуск** статьи в 2.1 и сравнение с legacy-карточкой (основной путь, ADD §29.3) —
   единственный вариант, дающий и полный провенанс, и coverage trail;
2. **Импорт как исторического артефакта** (`COMPATIBILITY_ONLY`): карточка читается,
   отображается и валидируется своей v1-схемой, но не превращается в пакет 2.1;
3. **Гибрид** — только если заказчик подтвердит источник метаданных (архив запуска,
   журнал, git-тег) и это будет описано отдельным ADR; покрытие и в этом случае придётся
   получать новым запуском поиска, а не реконструкцией.

Тесты: `tests/contract/test_legacy_compat.py`.
