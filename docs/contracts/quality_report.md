# Контракт `QualityReport` v1.0 (отчёт контроля качества)

**Модули:** `domain/documents/quality.py`, `application/services/quality_gate.py` ·
**Схема:** `contracts/documents/v1/quality_report.schema.json` ·
**Версия политики:** `1.0` (`QUALITY_POLICY_VERSION`) · **Итерация:** 2

Quality Gate отвечает на **один** вопрос: *насколько надёжно извлечено содержимое источника?* Он
не оценивает научную ценность статьи. Решение принимается именованными версионированными
проверками с наблюдаемым значением и порогом; агрегатная оценка, если появится, будет лишь
дополнительным числом, а не основанием вердикта.

---

## 1. Статусы

### 1.1. Итоговый статус документа (`QualityStatus`)

| Статус | Значение | Готов к продолжению |
|---|---|---|
| `passed` | извлечение надёжно | да |
| `passed_with_warnings` | извлечение пригодно, есть замечания | да |
| `requires_ocr` | есть страницы без пригодного текста; нужен OCR-движок | нет (управляемая остановка) |
| `needs_human_review` | извлечение сомнительно: провал `major`, повреждённый текст, неизвестные страницы или fallback | нет (управляемая остановка) |
| `failed` | провал `critical`: результат нельзя передавать дальше | нет |

`is_ready = True` только для `passed` и `passed_with_warnings`.

### 1.2. Статус проверки (`CheckStatus`)

`pass`, `warning`, `failed`, `not_applicable`.

### 1.3. Важность (`CheckSeverity`)

`info`, `minor`, `major`, `critical`. `critical` — то, что делает результат недействительным;
`major` — то, что требует человеческого решения; `minor` — замечание.

### 1.4. Статус страницы (`PageQualityStatus`)

| Статус | Условие (в порядке проверки) | Блокирует автоматическое продолжение |
|---|---|---|
| `parse_failed` | статус страницы `failed` | да (через вердикт `needs_human_review`) |
| `ocr_required` | текста нет, страница — скан (нет родного текста + есть изображения, либо пустой текст при площади изображений ≥ порога) | да (через вердикт `requires_ocr`) |
| `blank_or_unknown` | текста нет, скан не подтверждён | **нет** — свойство `is_blocking` объявляет `true`, но вердикт реагирует только на `parse_failed`/`suspected_garbled`; пустая страница даёт предупреждение `minor` |
| `suspected_garbled` | доля символов замены выше порога (при `detect_garbled_text`) **или** доля управляющих символов выше порога | да (через вердикт `needs_human_review`) |
| `image_dominant` | текста меньше `low_text_chars_per_page` и изображения занимают ≥ `image_dominant_area_ratio` | нет |
| `low_text` | текста меньше `low_text_chars_per_page` | нет |
| `ok` | всё остальное | нет |

Ограничение валидатора: страница `ocr_required` не может одновременно иметь много текста —
`text_char_count > 0` при доле замены `< 0.5` отвергается (`use low_text / suspected_garbled`).

Два свойства статуса объявлены, но **не используются** ни одним код-путём:
`is_ocr_candidate` (включает `ocr_required` и `image_dominant`) и `is_blocking` (включает
`ocr_required`, `suspected_garbled`, `parse_failed`, `blank_or_unknown`). Вердикт вычисляется
явными множествами в `QualityGateService._verdict`.

Следствие, которое нужно знать: статусы `low_text` и `image_dominant` **не порождают ни одной
проверки**. Страница с небольшим объёмом текста и большой площадью изображений видна только в
`page_results` и может не дать ни предупреждения, ни провала. Это зафиксированный пробел
политики 1.0, а не ошибка расчёта.

## 2. Словарь проверок

Проверки добавляются в отчёт в фиксированном порядке: страничные → структурные → текстовые →
провенанс → таблицы → парсер → (только TXT) → fallback.

| `check_id` | Когда выполняется | Статус и важность | Наблюдаемое / порог |
|---|---|---|---|
| `page_ocr_required` | есть страницы `ocr_required` | `warning` / `major` | число страниц; в `details.pages` — номера (до 20) |
| `page_garbled_text` | есть страницы `suspected_garbled` | `failed` / `major` | число страниц |
| `page_blank_or_unknown` | есть страницы `blank_or_unknown` и `warn_on_empty_pages` | `warning` / `minor` | число страниц |
| `structure_page_count_matches_source` | PDF (для TXT не выполняется) | `failed` / `critical` при расхождении, иначе `pass` / `info` | `page_count` документа / `page_count` инспекции |
| `structure_pages_complete` | PDF | всегда `pass` / `info` | число страниц в модели |
| `structure_reading_order_unique` | PDF | `failed` / `major` при дубликатах, иначе `pass` / `info` | число дубликатов |
| `structure_page_provenance` | PDF и `require_page_provenance` | `warning` / `major` при блоках без страницы, иначе `pass` / `info` | число таких блоков |
| `structure_text_source_has_no_pages` | **только TXT**; заменяет весь структурный блок | `pass` / `info` | `page_count` |
| `text_extracted` | всегда | `failed` / `critical`, если текста нет и страниц под OCR нет; `warning` / `major`, если текст отсутствует, но есть страницы под OCR; иначе `pass` / `info` | 0 / `minimum_meaningful_text_chars` |
| `text_meaningful_length` | только при непустом тексте | `warning` / `major` при коротком тексте, иначе `pass` / `info` | длина текста / 50 |
| `text_replacement_characters` | при `detect_garbled_text` | `failed` / `major` при превышении, иначе `pass` / `info` | доля `U+FFFD` / 0.02 |
| `text_control_characters` | всегда | `failed` / `major` при превышении, иначе `pass` / `info` | доля управляющих / 0.01 |
| `provenance_character_ranges` | при `verify_character_ranges`, иначе `not_applicable` / `info` | `failed` / `major` при выходе за текст или несовпадении подстроки, иначе `pass` / `info` | число блоков / длина текста |
| `provenance_bbox_bounds` | всегда | `warning` / `major` при выходе за страницу, иначе `pass` / `info` | число блоков |
| `tables_structure` | при `verify_table_structure`, иначе `not_applicable` / `info` | `failed` / `major` при таблицах без ячеек; `warning` / `minor` при частичных; иначе `pass` / `info` | число таблиц |
| `parser_status` | всегда | `failed` / `critical` при `parsing_status ∈ {failed, empty}`, иначе `pass` / `info` | значение статуса |
| `text_source_no_pagination` | только TXT | `failed` / `critical` при наличии страниц, иначе `pass` / `info` | `page_count` |
| `text_source_no_page_numbers` | только TXT | `failed` / `critical` при номерах страниц у блоков, иначе `pass` / `info` | число блоков |
| `fallback_parse_requires_review` | только если использован fallback | `failed` / `major` | имя парсера |

> `structure_text_source_has_no_pages` — единственный `check_id`, которого нет в исходном перечне
> требований: он добавлен в реализации, потому что структурные проверки PDF к TXT неприменимы, и
> молчание вместо явного `pass` выглядело бы как пропущенная проверка.

## 3. Пороговые значения (политика 1.0)

Значения по умолчанию — консервативная отправная точка, явно помеченная как эвристика. Фактически
применённые значения записываются в отчёт (`policy`), поэтому отчёт самодостаточен.

| Параметр `QualityPolicy` | По умолчанию | Где применяется |
|---|---|---|
| `policy_version` | `1.0` | записывается в отчёт |
| `minimum_meaningful_text_chars` | 50 | `text_meaningful_length` |
| `low_text_chars_per_page` | 120 | `low_text` / `image_dominant` |
| `max_replacement_char_ratio` | 0.02 | `text_replacement_characters`, `suspected_garbled` |
| `max_control_char_ratio` | 0.01 | `text_control_characters`, `suspected_garbled` |
| `image_dominant_area_ratio` | 0.85 | `ocr_required`, `image_dominant` |
| `page_count_mismatch_is_critical` | `true` | **только фиксируется в отчёте**; проверка расхождения всегда `critical` |
| `require_page_provenance` | `true` | `structure_page_provenance` |
| `verify_character_ranges` | `true` | `provenance_character_ranges` |
| `verify_table_structure` | `true` | `tables_structure` |
| `warn_on_empty_pages` | `true` | `page_blank_or_unknown` |
| `detect_garbled_text` | `true` | `suspected_garbled`, `text_replacement_characters` |
| `max_parser_warnings` | 20 | **не используется** (см. §7) |

Соответствие секции конфигурации `quality_gate` — в
[`../development/document_parser_configuration.md`](../development/document_parser_configuration.md).

## 4. Правила вердикта

Порядок фиксирован, срабатывает первое совпадение:

1. есть проваленная проверка с важностью `critical` → **`failed`**;
2. есть страницы `ocr_required` → **`requires_ocr`**;
3. есть любая проваленная проверка (`major`) → **`needs_human_review`**;
4. есть страницы `parse_failed` или `suspected_garbled` → **`needs_human_review`**;
5. есть хотя бы одно предупреждение → **`passed_with_warnings`**;
6. `parsing_status = parsed_with_fallback` → **`passed_with_warnings`** (на практике недостижимо:
   fallback всегда добавляет провал `major` и срабатывает правило 3);
7. иначе → **`passed`**.

Следствия:

* `failed` невозможен без провала `critical` — «разобрали, но непонятно» попадает в
  `needs_human_review`, а не в `failed`;
* `requires_ocr` приоритетнее любых предупреждений: документ ждёт движок, а не человека;
* `passed` несовместим ни с одним провалом и ни с предупреждением уровня `major`.

## 5. Поля отчёта

| Поле | Тип | Смысл |
|---|---|---|
| `document_id` | `Identifier` | документ |
| `run_id` | `ShortStr` | запуск |
| `source_sha256` | 64 hex | версия содержимого |
| `parsed_document_version` | `^\d+\.\d+$` | версия контракта `ParsedDocument` |
| `quality_policy_version` | `^\d+\.\d+$` | версия политики |
| `overall_status` | `QualityStatus` | итоговый вердикт |
| `page_count` | `≥ 0` \| `null` | число страниц документа (`null` для TXT) |
| `processed_pages` | `≥ 0` | сколько страниц реально оценено |
| `warnings_count` | `≥ 0` | число проверок со статусом `warning` |
| `errors_count` | `≥ 0` | число проверок со статусом `failed` |
| `ocr_required_pages` | `int[]`, ≤ 2000 | страницы под OCR; непусто **только** при `requires_ocr` |
| `page_results` | `PageQualityRecord[]`, ≤ 2000 | постраничные измерения |
| `checks` | `QualityCheckResult[]`, ≤ 500 | именованные проверки |
| `policy` | `QualityPolicy` | фактически применённые пороги |
| `parser_warnings` | `строка[]`, ≤ 200 | зарезервировано; в Итерации 2 всегда пусто |
| `summary` | `NonEmptyStr` | человекочитаемая строка итога |
| `created_at` | UTC datetime | момент отчёта |
| `extra` | словарь (≤ 16) | `{"fallback": "true" \| "false"}` |

`QualityCheckResult`: `check_id`, `check_version` (по умолчанию `1.0`), `status`, `severity`,
`message`, `observed_value`, `threshold`, `page_number`, `block_id`, `details` (≤ 16 пар строк).

`PageQualityRecord`: `page_number`, `page_status`, `text_char_count`, `text_block_count`,
`table_count`, `figure_count`, `image_area_ratio`, `replacement_char_ratio`,
`control_char_ratio`, `has_text_layer`, `scan_suspected`, `checks`.

## 6. Инварианты отчёта

1. `errors_count` равно числу проверок со статусом `failed`; `warnings_count` — числу `warning`.
2. `passed` несовместим с проваленными проверками и с предупреждениями уровня `major`.
3. `passed_with_warnings` несовместим с проваленными проверками.
4. `failed` требует хотя бы одной проваленной проверки уровня `critical`.
5. `requires_ocr` требует непустого `ocr_required_pages`.
6. `ocr_required_pages` непусто только при `requires_ocr`.
7. `page_results` уникальны и упорядочены по номеру страницы; при `page_count = null` список пуст.
8. `PageQualityRecord.checks` в Итерации 2 всегда пуст: постраничные наблюдения агрегируются в
   `checks` отчёта с номерами страниц в `details`.

## 7. Что отчёт не проверяет и не делает

| Ожидание | Фактическое состояние |
|---|---|
| оценка смысловой/научной достоверности | не делается: Gate оценивает только извлечение |
| `page_count_mismatch_is_critical = false` ослабляет вердикт | флаг записывается в `policy`, но проверка расхождения страниц всегда `critical` — параметр не влияет на результат |
| `max_parser_warnings` ограничивает шум | параметр записывается, но не читается: `parser_warnings` всегда пуст, предупреждения парсера живут в состоянии запуска и в диагностическом артефакте |
| повторный разбор по решению Gate | нет: `workflow.max_quality_gate_reparses` не применяется |
| `provenance_bbox_bounds` проверяет каждую страницу отдельно | нет: берётся первая страница с известными размерами, и все bbox сравниваются с ней (упрощение; для документов с разными размерами страниц проверка приблизительна) |
| таблицы проверяются по содержимому | только по форме: пустая матрица → провал, пустые ячейки → предупреждение; качество распознавания ячеек не измеряется |
| отчёт хранит текст документа | нет: только измерения, идентификаторы и версии |
| страницы `low_text` и `image_dominant` как-то влияют на вердикт | нет: для них нет ни проверки, ни агрегата; статус виден только в `page_results` |

## 8. Пример: `passed`

```json
{
  "document_id": "doc_7f3a91c4e28b5d60a1f49c3b",
  "run_id": "run_20261114_120000_a1b2c3",
  "source_sha256": "9b0f5c2e7d4a1b8c3f6e0d2a5c8b1f4e7a0d3c6b9e2f5a8d1c4b7e0f3a6d9c2b",
  "parsed_document_version": "1.0",
  "quality_policy_version": "1.0",
  "overall_status": "passed",
  "page_count": 1,
  "processed_pages": 1,
  "warnings_count": 0,
  "errors_count": 0,
  "ocr_required_pages": [],
  "page_results": [
    {
      "page_number": 1,
      "page_status": "ok",
      "text_char_count": 96,
      "text_block_count": 2,
      "table_count": 0,
      "figure_count": 0,
      "image_area_ratio": null,
      "replacement_char_ratio": 0.0,
      "control_char_ratio": 0.0,
      "has_text_layer": true,
      "scan_suspected": false,
      "checks": []
    }
  ],
  "checks": [
    {"check_id": "structure_page_count_matches_source", "check_version": "1.0", "status": "pass", "severity": "info", "message": "page count matches the original PDF", "observed_value": 1, "threshold": 1, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "structure_pages_complete", "check_version": "1.0", "status": "pass", "severity": "info", "message": "all physical pages are present in order without gaps", "observed_value": 1, "threshold": null, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "structure_reading_order_unique", "check_version": "1.0", "status": "pass", "severity": "info", "message": "reading order is unique across the document", "observed_value": 2, "threshold": null, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "structure_page_provenance", "check_version": "1.0", "status": "pass", "severity": "info", "message": "every element is bound to a physical page", "observed_value": 2, "threshold": null, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "text_extracted", "check_version": "1.0", "status": "pass", "severity": "info", "message": "text was extracted from the document", "observed_value": 96, "threshold": 50, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "text_meaningful_length", "check_version": "1.0", "status": "pass", "severity": "info", "message": "extracted text length is plausible", "observed_value": 96, "threshold": 50, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "text_replacement_characters", "check_version": "1.0", "status": "pass", "severity": "info", "message": "replacement character ratio is within the threshold", "observed_value": 0.0, "threshold": 0.02, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "text_control_characters", "check_version": "1.0", "status": "pass", "severity": "info", "message": "control character ratio is within the threshold", "observed_value": 0.0, "threshold": 0.01, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "provenance_character_ranges", "check_version": "1.0", "status": "pass", "severity": "info", "message": "every character range reproduces its element text", "observed_value": 2, "threshold": 96, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "provenance_bbox_bounds", "check_version": "1.0", "status": "pass", "severity": "info", "message": "all bounding boxes lie inside their pages", "observed_value": null, "threshold": null, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "tables_structure", "check_version": "1.0", "status": "pass", "severity": "info", "message": "no tables were reported by the parser", "observed_value": 0, "threshold": null, "page_number": null, "block_id": null, "details": {}},
    {"check_id": "parser_status", "check_version": "1.0", "status": "pass", "severity": "info", "message": "parser finished with status 'parsed'", "observed_value": "parsed", "threshold": null, "page_number": null, "block_id": null, "details": {}}
  ],
  "policy": {
    "policy_version": "1.0",
    "minimum_meaningful_text_chars": 50,
    "low_text_chars_per_page": 120,
    "max_replacement_char_ratio": 0.02,
    "max_control_char_ratio": 0.01,
    "image_dominant_area_ratio": 0.85,
    "page_count_mismatch_is_critical": true,
    "require_page_provenance": true,
    "verify_character_ranges": true,
    "verify_table_structure": true,
    "warn_on_empty_pages": true,
    "detect_garbled_text": true,
    "max_parser_warnings": 20
  },
  "parser_warnings": [],
  "summary": "quality gate: passed; 1 page(s) processed; 0 failed check(s), 0 warning(s)",
  "created_at": "2026-11-14T12:00:06Z",
  "extra": {"fallback": "false"}
}
```

## 9. Пример: `requires_ocr`

Скан без текстового слоя (`do_ocr = false`, движка нет). Пример показывает работу Gate; через CLI
он достижим для **смешанного** документа: если текста нет во всём документе, канонический текст пуст
и узел `build_canonical_document` завершает запуск ошибкой до вызова Gate.

```json
{
  "overall_status": "requires_ocr",
  "page_count": 1,
  "processed_pages": 1,
  "warnings_count": 2,
  "errors_count": 0,
  "ocr_required_pages": [1],
  "page_results": [
    {"page_number": 1, "page_status": "ocr_required", "text_char_count": 0,
     "scan_suspected": true, "image_area_ratio": 0.97, "has_text_layer": false}
  ],
  "checks": [
    {"check_id": "page_ocr_required", "status": "warning", "severity": "major",
     "message": "1 page(s) carry no usable text layer and need OCR", "observed_value": 1,
     "details": {"pages": "1"}},
    {"check_id": "text_extracted", "status": "warning", "severity": "major",
     "message": "no text was extracted from the document (pages require OCR)", "observed_value": 0,
     "threshold": 50}
  ]
}
```

## 10. Примеры других вердиктов (сжато)

| Сценарий | Ключевые проверки | Вердикт |
|---|---|---|
| `page_count` модели ≠ инспекции | `structure_page_count_matches_source: failed/critical` | `failed` |
| fallback PyMuPDF | `fallback_parse_requires_review: failed/major`, `parser_status: pass` | `needs_human_review` |
| таблица без ячеек | `tables_structure: failed/major` | `needs_human_review` |
| короткий, но валидный текст (30 символов) | `text_meaningful_length: warning/major` | `passed_with_warnings` |
| пустая страница без изображений | `page_blank_or_unknown: warning/minor` | `passed_with_warnings` |
| повреждённый текстовый слой | `page_garbled_text: failed/major` | `needs_human_review` |

## 11. Связанные документы

* [`parsed_document.md`](parsed_document.md) — что именно оценивается;
* [`document_provenance.md`](document_provenance.md) — проверки провенанса;
* [`../development/quality_gate.md`](../development/quality_gate.md) — реализация, пороги, примеры;
* [`../development/document_parser_configuration.md`](../development/document_parser_configuration.md) —
  секция конфигурации `quality_gate`.
