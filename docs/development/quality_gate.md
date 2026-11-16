# Quality Gate: реализация, пороги и вердикты

**Модули:** `application/services/quality_gate.py`, `domain/documents/quality.py`,
`config.py` (секция `quality_gate`) · **Итерация:** 2 · **Версия политики:** `1.0`

Quality Gate — детерминированная проверка **качества извлечения**, а не научной ценности статьи.
Он не вызывает LLM, не обращается к сети и не переразбирает документ. Каждое решение подкреплено
именованной проверкой с наблюдаемым значением и порогом, поэтому оператор видит, **почему**
документ принят, помечен или остановлен.

---

## 1. Входы и выходы

```python
report = QualityGateService(settings=settings.quality_gate, logger=logger).evaluate(
    document=parsed_document,  # ParsedDocument
    canonical_text=canonical_text,  # str, тот же текст, что в артефакте
    inspection=ingested.inspection,  # DocumentInspection | None (PyMuPDF)
    used_fallback=outcome.used_fallback,
    correlation_id=...,
)
```

Выход — `QualityReport`, который сразу сохраняется как артефакт `quality_report` с
`schema_version = "1.0"`. Политика, фактически применённая к документу, входит в отчёт полем
`policy`.

## 2. Порядок вычисления

```text
policy = settings.to_policy()
1. постраничные измерения и статусы  → page_results + проверки страниц
2. структурные проверки
3. текстовые проверки
4. проверки провенанса
5. проверка таблиц
6. проверка статуса парсера
7. (только TXT) проверки отсутствия пагинации
8. (если был fallback) fallback_parse_requires_review
9. вердикт по правилам §5
10. сборка отчёта: counts, summary, extra
```

Порядок проверок в отчёте фиксирован — это делает сравнение отчётов двух запусков осмысленным.

## 3. Постраничные измерения

Для каждой страницы `ParsedDocument.pages`:

| Величина | Как считается |
|---|---|
| `text_char_count` | `len(page.text)` |
| `text_block_count` | число элементов этой страницы с непустым текстом |
| `table_count`, `figure_count` | число таблиц/рисунков с этим `page_number` |
| `replacement_char_ratio` | доля `U+FFFD` в тексте страницы, округление до 4 знаков |
| `control_char_ratio` | доля управляющих символов, **кроме** `\n \t \r \f` и с учётом `0x7F` |
| `image_area_ratio` | из инспекции, иначе из `PageRecord` |
| `has_text_layer` | `inspection.has_native_text`, иначе `null` |
| `scan_suspected` | (нет родного текста **и** есть изображения) **или** (текста нет и площадь изображений ≥ порога) |

Статус страницы (`_page_status`), порядок проверок:

```text
если page.status == failed                       → parse_failed
если символов 0:
    если scan_suspected или area_ratio ≥ 0.85     → ocr_required
    иначе                                        → blank_or_unknown
если replacement_ratio > 0.02 (и detect_garbled)  → suspected_garbled
если control_ratio > 0.01                        → suspected_garbled
если символов < 120:
    если area_ratio ≥ 0.85                       → image_dominant
    иначе                                        → low_text
иначе                                            → ok
```

После этого добавляются агрегированные проверки страниц:

| Проверка | Условие | Статус / важность |
|---|---|---|
| `page_ocr_required` | есть `ocr_required` | `warning` / `major` |
| `page_garbled_text` | есть `suspected_garbled` | `failed` / `major` |
| `page_blank_or_unknown` | есть `blank_or_unknown` и `warn_on_empty_pages` | `warning` / `minor` |

Если `document.page_count is None` (TXT), постраничные измерения **не выполняются**: результат
пуст, страничных проверок нет.

## 4. Полный список проверок

### 4.1. Структура

| `check_id` | Условие провала | Статус / важность | Значение / порог |
|---|---|---|---|
| `structure_text_source_has_no_pages` | **только TXT**, никогда не проваливается | `pass` / `info` | `page_count` |
| `structure_page_count_matches_source` | `inspection.page_count` известно и ≠ `document.page_count` | `failed` / `critical` | `document.page_count` / `inspection.page_count`; при отсутствии инспекции — `pass` |
| `structure_pages_complete` | нет (страницы уже валидированы контрактом) | всегда `pass` / `info` | число страниц |
| `structure_reading_order_unique` | есть дубликаты `reading_order` | `failed` / `major` | число дубликатов |
| `structure_page_provenance` | есть блоки без `page_number` (при `require_page_provenance`) | `warning` / `major` | число блоков |

### 4.2. Текст

| `check_id` | Условие | Статус / важность | Значение / порог |
|---|---|---|---|
| `text_extracted` | текст пуст | `failed` / `critical`; `warning` / `major`, если есть страницы под OCR | 0 / 50 |
| `text_meaningful_length` | текст непуст и короче порога | `warning` / `major` | длина / 50 |
| `text_replacement_characters` | доля `U+FFFD` выше порога (при `detect_garbled_text`) | `failed` / `major` | доля / 0.02 |
| `text_control_characters` | доля управляющих выше порога | `failed` / `major` | доля / 0.01 |

`text_meaningful_length` не добавляется, если текст пуст: тогда есть только `text_extracted`.

### 4.3. Провенанс

| `check_id` | Условие | Статус / важность |
|---|---|---|
| `provenance_character_ranges` | `char_end > len(canonical_text)` → «вне текста»; иначе `canonical_text[start:end] != element.text` → «не воспроизводит текст» | `failed` / `major`; `not_applicable`, если `verify_character_ranges = false` |
| `provenance_bbox_bounds` | bbox выходит за границы страницы больше допуска 2.0 | `warning` / `major` |

Ограничение: `_bbox_outside` берёт **первую** страницу с известными размерами и сравнивает с ней
все bbox — для документа с разными размерами страниц проверка приблизительна.

### 4.4. Таблицы

| Условие | Статус / важность |
|---|---|
| `verify_table_structure = false` | `not_applicable` / `info` |
| таблиц нет | `pass` / `info` |
| есть таблица с `row_count == 0` | `failed` / `major` |
| есть `partial` или непустая таблица без текста | `warning` / `minor` |
| иначе | `pass` / `info` |

### 4.5. Парсер

| Условие | Статус / важность |
|---|---|
| `parsing_status = failed` | `failed` / `critical` |
| `parsing_status = empty` | `failed` / `critical` |
| иначе (включая `parsed_with_fallback`) | `pass` / `info` |

### 4.6. TXT

| `check_id` | Условие провала | Статус / важность |
|---|---|---|
| `text_source_no_pagination` | у TXT есть `page_count` или `pages` | `failed` / `critical` |
| `text_source_no_page_numbers` | у блока TXT есть `page_number` | `failed` / `critical` |

### 4.7. Fallback

| `check_id` | Условие | Статус / важность |
|---|---|---|
| `fallback_parse_requires_review` | использован plain-text fallback | `failed` / `major`, `observed = parser_name` |

## 5. Правила вердикта

```text
1. есть failed-проверка с severity = critical      → failed
2. есть страницы ocr_required                       → requires_ocr
3. есть любая failed-проверка                       → needs_human_review
4. есть страницы parse_failed / suspected_garbled   → needs_human_review
5. есть хотя бы одна warning                        → passed_with_warnings
6. parsing_status = parsed_with_fallback            → passed_with_warnings
7. иначе                                            → passed
```

`requires_ocr` стоит **перед** провалом `major`, поэтому документ, который ждёт OCR, не попадает в
«человеческую» очередь. Правило 6 практически недостижимо: fallback всегда добавляет провал
`major`, и срабатывает правило 3.

## 6. Соответствие конфигурации

Секция `quality_gate` → `QualityPolicy` (в отчёт записываются уже эти значения):

| Ключ конфигурации | Поле политики | По умолчанию | Используется |
|---|---|---|---|
| `policy_version` | `policy_version` | `1.0` | записывается |
| `fail_on_page_count_mismatch` | `page_count_mismatch_is_critical` | `true` | **нет** — расхождение всегда `critical` |
| `require_page_provenance` | `require_page_provenance` | `true` | да |
| `warn_on_empty_pages` | `warn_on_empty_pages` | `true` | да |
| `warn_on_low_text_density` | — | `true` | **нет** — поля в политике нет; низкая плотность влияет только на статус страницы через `low_text_chars_per_page` |
| `detect_garbled_text` | `detect_garbled_text` | `true` | да |
| `verify_character_ranges` | `verify_character_ranges` | `true` | да |
| `verify_table_structure` | `verify_table_structure` | `true` | да |
| `minimum_meaningful_text_chars` | `minimum_meaningful_text_chars` | 50 | да |
| `low_text_chars_per_page` | `low_text_chars_per_page` | 120 | да |
| `max_replacement_char_ratio` | `max_replacement_char_ratio` | 0.02 | да |
| `max_control_char_ratio` | `max_control_char_ratio` | 0.01 | да |
| `image_dominant_area_ratio` | `image_dominant_area_ratio` | 0.85 | да |
| `max_parser_warnings` | `max_parser_warnings` | 20 | **нет** — `parser_warnings` всегда пуст |

Ключи `fail_on_page_count_mismatch`, `warn_on_low_text_density` и `max_parser_warnings` валидируются,
попадают в политику (первые два — частично) и в отчёт, но на вердикт не влияют. Это осознанно
зафиксированный разрыв между конфигурацией и поведением, а не скрытая настройка.

## 7. Примеры вердиктов

Ниже — сжатые примеры; полный JSON отчёта — в
[`../contracts/quality_report.md`](../contracts/quality_report.md).

### 7.1. `passed` — цифровой PDF, одна страница

| Проверка | Статус |
|---|---|
| `structure_page_count_matches_source` | `pass` (1 / 1) |
| `structure_pages_complete`, `structure_reading_order_unique`, `structure_page_provenance` | `pass` |
| `text_extracted`, `text_meaningful_length` | `pass` (96 символов / 50) |
| `text_replacement_characters`, `text_control_characters` | `pass` (0.0) |
| `provenance_character_ranges`, `provenance_bbox_bounds` | `pass` |
| `tables_structure` | `pass` («no tables were reported») |
| `parser_status` | `pass` |

Итог: `passed`, `errors_count = 0`, `warnings_count = 0`, CLI → код 0.

### 7.2. `passed_with_warnings` — короткий, но корректный текст

| Проверка | Статус |
|---|---|
| `text_meaningful_length` | `warning` / `major` (30 / 50) |
| остальные | `pass` |

Итог: `passed_with_warnings`, `warnings_count = 1`, `is_ready = true`, CLI → код 0.
Замечание: предупреждение `major` при `passed` недопустимо, поэтому вердикт именно
`passed_with_warnings`, а не `passed`.

### 7.3. `requires_ocr` — скан без текстового слоя

Синтетический пример: `parsing_status = parsed`, страница определена как скан. Реальный
сканированный PDF в Итерации 2 **не проверялся** (нет OCR-движка). Важно: через CLI этот вердикт
достижим только для **смешанного** документа — если текста нет во всём документе, канонический
текст пуст и узел `build_canonical_document` падает раньше (§9).

| Проверка | Статус |
|---|---|
| `page_ocr_required` | `warning` / `major` (1 страница) |
| `text_extracted` | `warning` / `major` («pages require OCR») |
| `structure_page_count_matches_source` | `pass` |
| `parser_status` | `pass` (`parsed`) |

Итог: `requires_ocr`, `ocr_required_pages = [1]`, `errors_count = 0`, `warnings_count = 2`,
CLI → код 4 (управляемая остановка), запуск переводится в `waiting_for_human` с причиной
`ocr_required: no approved OCR engine (OI-008)`.

Замечание: если Docling вернул **ноль** элементов, `parsing_status = empty` → `parser_status`
становится `failed`/`critical` → вердикт `failed`, а не `requires_ocr`. То есть «скан,
распознанный как пустой документ» и «скан, где страница определена как скан» дают разные вердикты.

### 7.4. `needs_human_review` — fallback PyMuPDF

| Проверка | Статус |
|---|---|
| `fallback_parse_requires_review` | `failed` / `major` |
| `text_extracted`, `text_meaningful_length` | `pass` |
| `parser_status` | `pass` (`parsed_with_fallback`) |

Итог: `needs_human_review`, `errors_count = 1`, CLI → код 4, причина
`needs_human_review: the quality gate flagged the extraction`.

### 7.5. `failed` — расхождение числа страниц

| Проверка | Статус |
|---|---|
| `structure_page_count_matches_source` | `failed` / `critical` (1 / 3) |
| остальные | как правило `pass` |

Итог: `failed`, `errors_count = 1`, CLI → код 1, запуск завершается `failed` с ошибкой
`document_quality_failed`.

## 8. Сводная таблица «симптом → вердикт»

| Симптом | Ключевая проверка | Вердикт | Код CLI |
|---|---|---|---|
| документ извлечён чисто | — | `passed` | 0 |
| текст короче 50 символов | `text_meaningful_length` | `passed_with_warnings` | 0 |
| пустая страница без изображений | `page_blank_or_unknown` | `passed_with_warnings` | 0 |
| страница-скан | `page_ocr_required` | `requires_ocr` | 4 |
| повреждённый текстовый слой | `page_garbled_text` | `needs_human_review` | 4 |
| таблица без ячеек | `tables_structure` | `needs_human_review` | 4 |
| fallback вместо структурного разбора | `fallback_parse_requires_review` | `needs_human_review` | 4 |
| блок без номера страницы | `structure_page_provenance` (`major`) | `passed_with_warnings` | 0 |
| bbox вне страницы | `provenance_bbox_bounds` (`major`) | `passed_with_warnings` | 0 |
| число страниц не совпало | `structure_page_count_matches_source` | `failed` | 1 |
| текста нет вовсе | `text_extracted` (`critical`) | `failed` | 1 |
| у TXT появились страницы | `text_source_no_pagination` | `failed` | 1 |

## 9. Ограничения

* постраничные проверки в `PageQualityRecord.checks` не записываются (поле всегда пусто): те же
  факты лежат в `report.checks` с номерами страниц в `details.pages`;
* статусы `low_text` и `image_dominant` **не порождают проверок**: страница с малым объёмом текста
  и большой площадью изображений не даёт ни предупреждения, ни провала — она видна только в
  `page_results`. Свойства `PageQualityStatus.is_ocr_candidate` и `is_blocking` объявлены, но
  вердикт их не использует; `blank_or_unknown` блокирующим по факту **не** является (даёт
  предупреждение `minor`);
* Gate не переразбирает документ и не предлагает повторить разбор: `max_quality_gate_reparses`
  не применяется;
* Gate не измеряет качество распознавания таблиц и не сравнивает текст с изображением страницы;
* Gate не проверяет соответствие `document_id` байтам — это делает контракт при приёме и разборе;
* при `inspection = None` проверка числа страниц становится `pass` с `threshold = null`: отсутствие
  независимого эталона не считается провалом, но и не подтверждает ничего;
* пустой канонический текст до Gate не доходит: узел `build_canonical_document` падает раньше с
  `the canonical text is empty`, поэтому вердикт `failed` из-за `text_extracted` достижим только
  при прямом вызове Gate (как в тестах), а не через CLI на whitespace-only TXT.

## 10. Связанные документы

* [`../contracts/quality_report.md`](../contracts/quality_report.md) — контракт отчёта и полный
  пример JSON;
* [`document_parsing.md`](document_parsing.md) — откуда берутся страницы, текст и таблицы;
* [`document_parser_configuration.md`](document_parser_configuration.md) — секция `quality_gate`;
* [`../development/limitations.md`](limitations.md) — общий список ограничений.

## Покрытие сценариев (техническое закрытие, 2026-11-16)

`tests/parsing/test_quality_gate_scenarios.py` — 16 сценариев: `passed`, `passed_with_warnings`
(пустая страница), `requires_ocr` (скан, в том числе через реальный Docling), повреждённый
текстовый слой (`needs_human_review`), несоответствие числа страниц (`failed`), отсутствие
страницы у элемента (warning), bbox вне страницы (warning), char-диапазоны вне текста и не
воспроизводящие текст (`failed`), пустая таблица (`failed`), частичная таблица (warning),
fallback (`needs_human_review`), текст без текста (`failed`), запись порогов политики в отчёт и
сохранение артефактов при контролируемой остановке.

Политика и пороги не изменялись.