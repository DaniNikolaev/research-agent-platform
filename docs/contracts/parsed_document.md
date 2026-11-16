# Контракт `ParsedDocument` v1.0 (результат разбора)

**Модули:** `domain/documents/parsed.py`, `domain/documents/elements.py`,
`domain/documents/provenance.py` · **Схема:** `contracts/documents/v1/parsed_document.schema.json` ·
**Версия контракта:** `1.0` (`PARSED_DOCUMENT_CONTRACT_VERSION`) · **Версия канонизации:** `1.0`
(`CANONICALIZATION_VERSION`) · **Итерация:** 2

`ParsedDocument` — стабильное, независимое от парсера представление того, что извлечено из
источника. Это **не** карточка Agent 1A: здесь нет извлечённых полей, evidence-записей и
coverage trail. Есть текст, структура и происхождение — и ничего больше.

---

## 1. Верхний уровень

| Поле | Тип | Обяз. | Смысл |
|---|---|---|---|
| `document_contract_version` | `^\d+\.\d+$` | да (по умолчанию `1.0`) | версия контракта |
| `document_id` | `Identifier` | да | id содержимого (см. [`document_manifest.md`](document_manifest.md)) |
| `run_id` | `ShortStr` | да | запуск, в котором выполнено разбор |
| `source_sha256` | 64 hex | да | хеш исходных байтов; должен совпадать с манифестом |
| `source_type` | `pdf` \| `txt` | да | тип источника |
| `language` | `ru` \| `en` \| `unknown` | да | доминирующий алфавит; при неизвестном на приёме определяется по каноническому тексту |
| `parser` | `ParserProvenance` | да | чем и с какими настройками разобрано |
| `parsing_status` | `parsed` \| `parsed_with_fallback` \| `empty` \| `failed` | да | итог самого разбора |
| `page_count` | целое `≥ 1` \| `null` | условно | обязательно для PDF, обязано быть `null` для TXT |
| `pages` | `PageRecord[]`, ≤ 2000 | да (может быть пустым для TXT) | физические страницы |
| `sections` | `ParsedSection[]`, ≤ 500 | да | разделы, как их выделил парсер |
| `elements` | `DocumentElement[]`, ≤ 50 000 | да | адресуемые блоки |
| `tables` | `TableRecord[]`, ≤ 1000 | да | структурированные таблицы |
| `figures` | `FigureRecord[]`, ≤ 2000 | да | метаданные рисунков |
| `canonical_text` | `CanonicalTextInfo` | да | адрес и версия канонического текста |
| `source_artifact_uri` | `ArtifactUri` | да | ссылка на неизменяемый оригинал |
| `created_at` | UTC datetime | да | момент сборки контракта |

### 1.1. `ParserProvenance`

`parser_name`, `parser_version`, `pipeline_version`, `parser_options_hash` (16–64 hex),
`backend_name`, `table_extraction_enabled`, `ocr_enabled`, опционально
`model_assets_revision`.

Поля обязательные, а не «метаданные на всякий случай»: без них сохранённый разбор нельзя
воспроизвести или сравнить с другим. Фактические значения в Итерации 2:

| Парсер | `parser_name` | `pipeline_version` | `backend_name` | таблицы | OCR |
|---|---|---|---|---|---|
| Docling | `docling` | `standard-pdf/1` | `docling-parse` | `true` | `false` |
| TXT-адаптер | `txt-block-parser` | `txt/1` | `python` | `false` | `false` |
| PyMuPDF-fallback | `pymupdf` | `text-fallback/1` | `pymupdf` | `false` | `false` |

### 1.2. `CanonicalTextInfo`

| Поле | Смысл |
|---|---|
| `artifact_uri` | URI артефакта канонического текста (`text/plain`, кодировка UTF-8) |
| `canonicalization_version` | версия правил сборки текста (`1.0`) |
| `char_count` | `len(текста)` в Unicode code points |
| `page_separator` | разделитель страниц (`"\f"` в версии 1.0); у TXT в тексте не встречается |

## 2. `PageRecord` — физическая страница

| Поле | Тип / предел | Смысл |
|---|---|---|
| `page_number` | `≥ 1` | **физическая** страница источника, 1-based |
| `page_width`, `page_height` | `> 0` \| `null` | размеры страницы (PDF points), если известны |
| `rotation` | `0…359`, по умолчанию `0` | поворот страницы, как отрендерено |
| `status` | `parsed` \| `empty` \| `no_text_layer` \| `partial` \| `failed` | статус извлечения **этой** страницы |
| `text` | строка | текст страницы, склеенный из блоков этой страницы |
| `char_count` | `≥ 0`, **вычисляется** | длина `text`; не независимое утверждение |
| `block_ids` | `Identifier[]`, ≤ 5000 | блоки, приписанные странице |
| `has_native_text` | `bool` \| `null` | есть ли родной текстовый слой (диагностика PyMuPDF) |
| `has_images` | `bool` \| `null` | есть ли изображения |
| `image_area_ratio` | `0…1` \| `null` | доля площади под изображениями |
| `parser_page_reference` | `ShortStr` \| `null` | ссылка на страницу у парсера, если она не равна номеру |
| `metadata` | словарь (≤ 16) | прочие атрибуты страницы |

Правило: **страница печатного издания никогда не подменяет физическую.** Колонцифра «247»
остаётся текстом, а `page_number` остаётся 12-й физической страницей файла.

## 3. `DocumentElement` — адресуемый блок

| Поле | Тип / предел | Смысл |
|---|---|---|
| `block_id` | `Identifier` | уникальный в документе id блока |
| `element_type` | `heading` \| `paragraph` \| `list_item` \| `table` \| `table_caption` \| `figure` \| `figure_caption` \| `footnote` \| `header` \| `footer` \| `formula` \| `unknown` | тип блока |
| `text` | строка (может быть пустой) | текст блока |
| `page_number` | `≥ 1` \| `null` | физическая страница; `null` только для источника без пагинации |
| `section_id` | `ShortStr` \| `null` | раздел, если блок к нему отнесён |
| `reading_order` | `≥ 0` | позиция в порядке чтения, уникальная в документе |
| `bbox` | `BBox` \| `null` | прямоугольник на странице (§5) |
| `char_start`, `char_end` | `≥ 0` \| `null` | диапазон в **каноническом тексте** (§6) |
| `source` | `docling` \| `pymupdf_fallback` \| `txt_decoder` | чем получен блок |
| `level` | `1…6` | обязателен для `heading`, иначе `null` |
| `confidence` | `0…1` \| `null` | уверенность эвристики (для TXT-заголовков — `0.6`) |
| `metadata` | словарь строк (≤ 16) | например `docling_label`, `line_start`/`line_end` |

## 4. `ParsedSection`, `TableRecord`, `FigureRecord`

### 4.1. `ParsedSection`

`section_id`, `title` (непустой, обрезается парсером до 300 символов), `level` (1–6),
`parent_section_id`, `page_start`, `page_end` (если заданы, `page_end ≥ page_start`).

Раздел создаётся **по заголовку**: каждый элемент типа `heading` порождает раздел. Заголовок без
текста получает название `Untitled section`. Иерархия строится стеком по `level`: заголовок
уровня L закрывает все открытые разделы уровня ≥ L.

### 4.2. `TableRecord`

| Поле | Тип | Смысл |
|---|---|---|
| `table_id` | `Identifier` | `tbl_0001`, `tbl_0002`, … в порядке обхода |
| `page_number`, `section_id`, `reading_order`, `bbox` | как у элемента | положение таблицы |
| `row_count`, `column_count` | `≥ 0` | размеры матрицы |
| `cells` | `string[][]`, ≤ 10 000 | ячейки как строки |
| `has_header_row` | `bool` \| `null` | `true`, если матрица непуста (не анализ заголовка) |
| `caption` | `строка` \| `null` | подпись таблицы (≤ 500 символов) |
| `text_representation` | строка | детерминированное представление `a \| b \| c` по строкам |
| `extraction_status` | `complete` \| `partial` \| `empty` \| `failed` | `partial`, если есть пустые ячейки; `empty`, если ячеек нет |
| `source` | `ProvenanceSource` | `docling` |

Таблица попадает в документ **дважды**: как `TableRecord` (структура) и как `DocumentElement`
типа `table`, у которого `text` = `text_representation`. Поэтому канонический текст содержит
таблицу в виде строк с разделителем ` | `.

### 4.3. `FigureRecord`

`figure_id` (`fig_0001`, …), `page_number`, `section_id`, `reading_order`, `bbox`,
`caption` (только у элемента-подписи), `image_artifact_uri` (в Итерации 2 всегда `null`),
`source`. Пиксели не рендерятся и не интерпретируются: `generate_picture_images=false`.

## 5. Контракт `bbox`

`BBox`: `x0`, `y0`, `x1`, `y1`, `unit`, `origin`, `page_width`, `page_height`, `system`.

Правила:

1. `x0 ≤ x1`, `y0 ≤ y1` — проверяется.
2. Если `unit ≠ unknown`, обязаны быть заданы `page_width`/`page_height`: координаты без размера
   страницы нельзя ни отрисовать, ни сравнить.
3. Допуск выхода за страницу — 2.0 единицы (погрешность парсера); большее смещение отвергается.
4. Нормализованная система Итерации 2: `unit = pt` (1/72 дюйма), `origin = top_left`, начало
   координат — левый верхний угол страницы, размеры страницы в тех же единицах.
5. Если парсер не может выразить свою систему, он объявляет `unit = unknown`, `origin = unknown`
   и имя системы в `system` — вместо того чтобы выдавать неизвестный фрейм за PDF points.

Docling отдаёт координаты с нижним левым началом (`l/t/r/b`); адаптер переводит их в верхнее
левое начало по высоте страницы (§ `document_provenance.md`).

## 6. Семантика `char_start`/`char_end` и версия канонизации

Диапазоны адресуют **канонический текст этого документа**, а не байты PDF, не текст парсера и не
файл на диске:

* 0-based, конец **не включается**;
* единица измерения — Unicode code points (Python-строка), не байты;
* диапазон вычисляется **в момент сборки** текста, а не восстанавливается после;
* инвариант: `canonical_text[char_start:char_end] == element.text` — его проверяет Quality Gate
  (`provenance_character_ranges`);
* диапазоны действительны только для той версии канонизации, в которой они вычислены. Любое
  изменение нормализации (пробелы, разделитель страниц, представление таблиц) обязано поднять
  `CANONICALIZATION_VERSION`, иначе сохранённые диапазоны станут ложью;
* у блока с пустым текстом диапазона нет: `char_start` и `char_end` задаются вместе или не
  задаются вовсе (проверяется валидатором).

### 6.1. Правила канонического текста, версия 1.0

1. Элементы выдаются в порядке `reading_order`.
2. Текст элемента обрезается по краям (`strip`).
3. Пустые после обрезки элементы в текст не попадают.
4. Элементы разделяются `"\n\n"`.
5. При смене физической страницы вставляется разделитель страницы `"\f"` (один символ).
6. Завершающий `"\n\n"` у последнего элемента срезается.
7. У источника без пагинации (TXT) границ страниц нет вовсе.
8. Ничего больше не переписывается: нет приведения регистра, замены пунктуации, склейки переносов.
9. Текст таблицы попадает в канонический текст в том виде, в каком его сформировал адаптер
   (`a | b | c`); сама канонизация таблицу не перерисовывает.

## 7. Инварианты (валидаторы `ParsedDocument`)

1. **Пагинация по типу источника.** Для TXT: `page_count = null`, `pages = []`, у всех элементов
   `page_number = null`. Для PDF: `page_count` обязан быть задан.
2. **Полная последовательность страниц.** Если `pages` непуст, номера обязаны быть ровно
   `1..N` без пропусков и перестановок.
3. **Уникальность блоков.** `block_id` не повторяется.
4. **Ссылки существуют.** `element.page_number` входит в последовательность страниц;
   `element.section_id` входит в `sections`.
5. **Согласованность страниц и блоков.** Каждый `page.block_ids` ссылается на существующий
   элемент.
6. **Таблицы и рисунки на реальных страницах.** Их `page_number` входит в последовательность.
7. **Пустой разбор не притворяется текстом.** При `parsing_status = empty` суммарный текст
   элементов равен нулю.
8. **Страница `parsed` обязана иметь текст.** Пустая страница объявляется `empty`,
   `no_text_layer` или `partial`, но не `parsed`.

## 8. Пример JSON (скелет)

Сокращённо; многоточия — не часть контракта.

```json
{
  "document_contract_version": "1.0",
  "document_id": "doc_7f3a91c4e28b5d60a1f49c3b",
  "run_id": "run_20261114_120000_a1b2c3",
  "source_sha256": "9b0f5c2e7d4a1b8c3f6e0d2a5c8b1f4e7a0d3c6b9e2f5a8d1c4b7e0f3a6d9c2b",
  "source_type": "pdf",
  "language": "en",
  "parser": {
    "parser_name": "docling",
    "parser_version": "2.150.0",
    "pipeline_version": "standard-pdf/1",
    "parser_options_hash": "3f6a1c9d0b7e4258",
    "backend_name": "docling-parse",
    "table_extraction_enabled": true,
    "ocr_enabled": false,
    "model_assets_revision": null
  },
  "parsing_status": "parsed",
  "page_count": 1,
  "pages": [
    {
      "page_number": 1,
      "page_width": 595.0,
      "page_height": 842.0,
      "rotation": 0,
      "status": "parsed",
      "text": "A Synthetic Research Article\n\nAbstract\n\nThis article describes …",
      "char_count": 96,
      "block_ids": ["blk_1a2b3c4d5e6f7a8b", "blk_2b3c4d5e6f7a8b9c"],
      "has_native_text": true,
      "has_images": null,
      "image_area_ratio": null,
      "parser_page_reference": null,
      "metadata": {}
    }
  ],
  "sections": [
    {
      "section_id": "sec_0001",
      "title": "A Synthetic Research Article",
      "level": 1,
      "parent_section_id": null,
      "page_start": 1,
      "page_end": 1
    }
  ],
  "elements": [
    {
      "block_id": "blk_1a2b3c4d5e6f7a8b",
      "element_type": "heading",
      "text": "A Synthetic Research Article",
      "page_number": 1,
      "section_id": "sec_0001",
      "reading_order": 0,
      "bbox": {
        "x0": 72.0, "y0": 76.0, "x1": 390.0, "y1": 94.0,
        "unit": "pt", "origin": "top_left",
        "page_width": 595.0, "page_height": 842.0, "system": null
      },
      "char_start": 0,
      "char_end": 28,
      "source": "docling",
      "level": 1,
      "confidence": null,
      "metadata": {"docling_label": "section_header"}
    }
  ],
  "tables": [],
  "figures": [],
  "canonical_text": {
    "artifact_uri": "artifact://documents/doc_7f3a91c4e28b5d60a1f49c3b/runs/run_20261114_120000_a1b2c3/parsed_document/art_canonical_5d60a1f49c3b_a1b2c3",
    "canonicalization_version": "1.0",
    "char_count": 96,
    "page_separator": "\f"
  },
  "source_artifact_uri": "artifact://documents/doc_7f3a91c4e28b5d60a1f49c3b/runs/run_20261114_120000_a1b2c3/original_document/art_source_5d60a1f49c3b_a1b2c3",
  "created_at": "2026-11-14T12:00:05Z"
}
```

## 9. Известные ограничения контракта в Итерации 2

| Ограничение | Деталь |
|---|---|
| `page_ocr_required`-страницы не несут текста | у страницы, ушедшей в `requires_ocr`, `text` пуст; текста для OCR-движка в контракте нет, только номер страницы |
| `image_artifact_uri` не заполняется | рисунки не рендерятся |
| `PageRecord.has_images` не заполняется Docling-адаптером | заполняется только там, где есть данные инспекции; в `PageRecord` от Docling остаётся `null` |
| `TableRecord.has_header_row` — не анализ заголовка | `true` при непустой матрице |
| `figure` без подписи не создаёт элемент | `FigureRecord` есть, текстового блока нет |
| `confidence` заполняется только TXT-адаптером | у Docling всегда `null` |

## 10. Связанные документы

* [`document_provenance.md`](document_provenance.md) — система координат и происхождение;
* [`document_manifest.md`](document_manifest.md) — откуда берутся `source_sha256` и `document_id`;
* [`quality_report.md`](quality_report.md) — как проверяется этот документ;
* [`../development/document_parsing.md`](../development/document_parsing.md) — как он собирается.
