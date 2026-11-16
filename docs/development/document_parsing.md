# Разбор документа: Docling, PyMuPDF, TXT и канонический текст

**Модули:** `application/services/document_parsing.py`,
`platform/document_parsing/docling/{pdf_parser,converters,options}.py`,
`platform/document_parsing/pymupdf/pdf_inspector.py`,
`platform/document_parsing/txt/{decoder,parser}.py` · **Итерация:** 2

Разбор превращает принятые байты в `ParsedDocument`: страницы, разделы, блоки, таблицы, рисунки и
канонический текст с символьными диапазонами. Слои разделены: **что** извлекать решает
application-сервис, **как** — адаптер в `platform/`.

---

## 1. Диспетчеризация

| Тип источника | Основной адаптер | Резервный путь |
|---|---|---|
| `pdf` | `DoclingPdfParser` | `PyMuPDFInspector.extract_text_fallback` — только при техническом отказе основного |
| `txt` | `TxtBlockParser` (текст уже декодирован при приёме) | нет |

Любое исключение основного PDF-парсера перехватывается сервисом. Если fallback разрешён
конфигурацией и дал текст, результат помечается `parsed_with_fallback` и получает предупреждение
`primary parser failed (<тип исключения>); plain-text fallback used`. Если fallback невозможен или
пуст — `DocumentParsingError` (`document_parsing_failed`).

## 2. Адаптер Docling

### 2.1. Опции

`DoclingParserOptions` — `frozen dataclass`, все поля которого хешируются в
`parser_options_hash` (16 hex от JSON с сортированными ключами):

| Опция | Значение по умолчанию | Передаётся в Docling | Комментарий |
|---|---|---|---|
| `do_ocr` | `false` | да | конструктор `DoclingPdfParser` **отвергает `true`** с `ValueError` |
| `do_table_structure` | `true` | да | таблицы — часть содержимого |
| `generate_picture_images` | `false` | да | пиксели рисунков не рендерятся |
| `do_cell_matching` | `true` | **нет** | участвует в хеше, но не в поведении |
| `images_scale` | `1.0` | да | |
| `force_full_page_ocr` | `false` | **нет** | участвует в хеше, но не в поведении |
| `layout_model` | `docling-layout-heron` | **нет** | участвует в хеше; фактическая модель — по умолчанию у Docling |
| `table_model` | `tableformer` | **нет** | то же |
| `backend` | `docling-parse` | **нет** | записывается в `ParserProvenance.backend_name` |

Важное следствие: изменение `layout_model`/`table_model`/`backend`/`do_cell_matching` изменит
`parser_options_hash`, но **не** изменит фактический разбор, потому что эти поля не передаются в
`PdfPipelineOptions`. `to_pipeline_options()` выставляет только `do_ocr`,
`do_table_structure`, `generate_picture_images`, `images_scale`.

Версия пайплайна — `standard-pdf/1` (`DOCLING_PIPELINE_VERSION`). `parser_version` берётся из
установленного пакета (`2.150.0`); при отсутствии пакета — `unavailable`.

### 2.2. Как выполняется разбор

1. пустые байты → `ValueError("pdf source is empty")`;
2. `DocumentConverter` создаётся лениво, один раз на адаптер, с `PdfFormatOption`;
3. если задан `artifacts_path`, он передаётся в `PdfPipelineOptions` (в штатной сборке
   `composition/` его **не** задаёт, поэтому используется стандартный кеш Docling);
4. байты пишутся во временный каталог `docling-src-*` как `source.pdf`;
5. `converter.convert(source)`; любое исключение → `ValueError("docling could not convert …")`;
6. отсутствие `result.document` → `ValueError("docling returned no document")`.

Сеть не используется самим адаптером; загрузку весов (если их нет в кеше) выполняет Docling.

### 2.3. Построение страниц

Страницы строятся **из таблицы страниц документа**, а не из найденных элементов:

```text
page_sizes = {page_no: (width, height)} из document.pages
page_count = len(page_sizes) или document.num_pages()
для page_number in 1..page_count:
    элементы страницы = все элементы с этим page_number
    text = "\n\n".join(тексты элементов страницы)
    status = "empty", если текста нет, иначе "parsed"
```

Следствия:

* каждая физическая страница присутствует, даже без единого блока (пустая страница — `empty`);
* номера страниц идут 1..N без пропусков, что и требует контракт;
* `PageRecord.has_native_text`, `has_images`, `image_area_ratio` адаптер **не заполняет** — эти
  диагностические поля приходят из инспектора и попадают в Quality Gate оттуда;
* `rotation` не заполняется (остаётся `0`);
* ветка `status = partial` в коде недостижима: она требует непустой текст при нулевом числе
  элементов, чего не бывает.

### 2.4. Обход элементов и провенанс

`document.iterate_items()` даёт пары `(item, nesting)`; `order` — порядковый номер в обходе.

| Поле `DocumentElement` | Источник значения |
|---|---|
| `reading_order` | индекс обхода (0, 1, 2, …) |
| `page_number` | `prov[0].page_no`; нет провенанса → `null` |
| `bbox` | `prov[0].bbox`, переведённый в PDF points / top-left |
| `element_type` | по таблице меток (§2.5) |
| `section_id` | стек заголовков |
| `level` | для заголовка: `item.level` или глубина вложенности, ограниченная 1..6 |
| `text` | `item.text` |
| `metadata.docling_label` | сырая метка в нижнем регистре (у таблиц — `table:<n>`) |
| `char_start` / `char_end` | **не заполняются** — их вычисляет канонизация |
| `source` | `docling` |

Правила отбора:

* блок без текста пропускается (кроме заголовка: заголовок без текста создаёт раздел
  `Untitled section`, а его элемент затем отбрасывается канонизацией как пустой);
* рисунок без подписи создаёт `FigureRecord`, но **не** создаёт текстовый элемент;
* подпись рисунка создаёт `FigureRecord` (с `caption`) **и** элемент типа `figure_caption`;
* таблица создаёт `TableRecord` **и** элемент типа `table`.

### 2.5. Таблица меток Docling → `ElementType`

| Метка Docling | `ElementType` |
|---|---|
| `section_header`, `title` | `heading` |
| `text`, `paragraph`, `code`, `reference`, `document_index` | `paragraph` |
| `list_item`, `list` | `list_item` |
| `table` | `table` |
| `table_caption` | `table_caption` |
| `picture`, `figure` | `figure` |
| `picture_caption`, `caption` | `figure_caption` |
| `footnote` | `footnote` |
| `page_header` | `header` |
| `page_footer` | `footer` |
| `formula` | `formula` |
| любая другая метка | `unknown` (сырая метка сохраняется в `metadata.docling_label`) |

Неизвестная метка **не** превращается в догадку: новая версия Docling не сможет тихо
переклассифицировать содержимое.

### 2.6. Таблицы

1. `item.export_to_dataframe(doc=document)` → `astype(str).values.tolist()`;
2. `nan`/`None` заменяются на пустую строку;
3. `row_count = len(cells)`, `column_count = len(cells[0])`;
4. `extraction_status`: `empty` (ячеек нет), `partial` (есть пустые ячейки), `complete` иначе;
5. `caption` — `item.caption_text(document)`, обрезается до 500 символов;
6. `text_representation` — строки через ` | `, строки через `\n`;
7. `has_header_row = true`, если матрица непуста (это **не** анализ заголовка).

Любое исключение при экспорте матрицы или подписи перехватывается: таблица остаётся с пустыми
ячейками (`empty`) или без подписи, но разбор не падает.

### 2.7. Разделы

| Правило | Значение |
|---|---|
| раздел создаётся | на каждый элемент-заголовок |
| `section_id` | `sec_0001`, `sec_0002`, … |
| `title` | текст заголовка (или `Untitled section`), ≤ 300 символов |
| `level` | `item.level` или глубина вложенности, clamp 1..6 |
| `parent_section_id` | верх стека после снятия разделов уровня ≥ текущего |
| `page_start` / `page_end` | страница заголовка (оба поля) |

## 3. Инспектор и fallback PyMuPDF

`PyMuPDFInspector` — диагностический механизм, не «второй парсер на равных».

### 3.1. Инспекция (`inspect_pdf`)

| Что | Как |
|---|---|
| открытие | `pymupdf.open(stream=data, filetype="pdf")`; ошибка → `ValueError` |
| шифрование | `needs_pass or is_encrypted` → `DocumentInspection(is_encrypted=True)` **без** постраничных данных |
| страницы | `page.rect.width/height`, `page.rotation` |
| родной текст | `page.get_text("text")`; `has_native_text = len(strip) ≥ 20` (`MIN_NATIVE_TEXT_CHARS`) |
| изображения | `page.get_images(full=True)`, `image_count` |
| площадь изображений | сумма площадей `get_image_rects(image_id)` / площадь страницы, ограничение 1.0; `None`, если изображений нет |

Тонкость: если изображения есть, но их прямоугольники недоступны, возвращается константа
`_IMAGE_COVERAGE_TOLERANCE = 0.90` — «изображения, вероятно, покрывают страницу». Это
приближение, а не измерение.

### 3.2. Fallback (`extract_text_fallback`)

* выполняется только если `document_parsing.fallback_pdf_parser == "pymupdf"` и инспектор есть;
* только для PDF; пустой результат (`total_chars == 0`) → fallback считается неудавшимся;
* на каждую страницу — один элемент `paragraph` с текстом `strip()`, `bbox = null`,
  `section_id = null`, `source = pymupdf_fallback`;
* `block_id` = `blk_` + 16 hex от `document_id|page_number|text[:200]`;
* `PageRecord.status` = `parsed` или `empty`;
* `ParserProvenance`: `pymupdf`, версия пакета, `text-fallback/1`, хеш
  `sha256("pymupdf-text-fallback-v1")[:16]`, таблицы выключены, OCR выключен;
* предупреждение: «fallback extraction provides plain text only: no layout, no tables, no section
  structure».

Quality Gate после fallback всегда выносит `fallback_parse_requires_review` (`failed`/`major`) →
`needs_human_review`.

## 4. TXT: декодирование

`TxtDecoder` — детерминированный порядок, **строгое** декодирование, никаких эвристических оценок.

| Шаг | Кодек | Условие |
|---|---|---|
| 1 | `utf-8-sig` (BOM UTF-8) | файл начинается с `EF BB BF` |
| 2 | `utf-16` (BOM UTF-16 LE/BE) | файл начинается с `FF FE` или `FE FF`; метка записывается как `utf-16-le`/`utf-16-be` |
| 3 | `utf-16-le`, затем `utf-16-be` | в файле есть NUL-байты (BOM отсутствует); результат отвергается, если в тексте остались NUL |
| 4 | `utf-8` | строгое декодирование |
| 5 | `cp1251` | строгое декодирование |
| 6 | `koi8-r` | строгое декодирование |
| 7 | `cp1252` | строгое декодирование |
| 8 | `utf-8` с `errors="replace"` | только если ничего не подошло: `encoding_uncertain = true`, `replacement_count`, два предупреждения |

Правила:

* BOM — объявление, а не догадка: при наличии BOM файл обязан декодироваться этим кодеком строго;
* «декодировать с заменой» как обычная стратегия запрещено: это тихо уничтожает символы;
* при нескольких успешных кандидатах побеждает порядок, а не счёт: детерминированное правило можно
  объяснить и воспроизвести;
* пустые байты → `ValueError("text source is empty")`;
* больше 5 000 000 символов → `ValueError` (≈10 MB памяти на текст);
* исходные байты всегда остаются источником истины и хранятся без изменений.

Класс объявляет атрибут `order = ("utf-8-sig", "utf-16", "utf-8", "cp1251", "koi8-r", "cp1252")`
для отчёта; фактический порядок попыток для UTF-16 без BOM — `utf-16-le`, затем `utf-16-be`
(см. таблицу выше).

## 5. TXT: структурный разбор

`TxtBlockParser` не изобретает страниц: `page_number = null`, `page_count = null`, `pages = []`.

| Правило | Значение |
|---|---|
| абзац | последовательность непустых строк, склеенная `"\n"` и обрезанная |
| граница абзаца | пустая строка (или конец файла) |
| `element_type` | `paragraph`, `confidence = 1.0` |
| заголовок | эвристика: следующая строка пустая **или** конец файла; длина ≤ 120; не заканчивается на `. ! ? : ; , » " '`; первый символ не строчная буква |
| `element_type` заголовка | `heading`, `level = 1`, `confidence = 0.6` |
| `metadata` | `line_start`, `line_end` (0-based номера строк) |
| `bbox`, `section_id` | `null` |
| `source` | `txt_decoder` |
| `block_id` | `blk_` + 16 hex от `document_id|order|element_type|line_start|text[:200]` |
| пустой текст | `ParsingStatus.EMPTY` и предупреждение «contains no non-empty lines» |

Слабость сигнала заголовка зафиксирована числом: `confidence = 0.6`, а не «заголовок точно».
Проверенных типографических признаков (кегль, полужирность, отступ) у текстового файла нет.

## 6. Канонический текст (версия 1.0)

```text
для элемента в порядке reading_order:
    text = element.text.strip()
    если text пуст — пропустить
    если страница изменилась — добавить "\f"
    запомнить char_start = текущая позиция
    добавить text; char_end = позиция после text
    добавить "\n\n"
в конце срезать завершающий "\n\n"
```

| Правило | Деталь |
|---|---|
| `PAGE_SEPARATOR` | `"\f"` (один символ) |
| `ELEMENT_SEPARATOR` | `"\n\n"` |
| нормализация | только `strip` по краям; регистр, пунктуация и переносы не меняются |
| пустые блоки | в текст не попадают, диапазона не получают |
| TXT | границ страниц нет, `\f` не встречается |
| таблицы | попадают текстом `a \| b \| c` (рендер адаптера), а не перерисовкой канонизатора |
| диапазоны | 0-based, конец не включается, code points |
| артефакт | `text/plain`, UTF-8, `schema_version = "1.0"` |

`CANONICALIZATION_VERSION` обязан меняться при любом изменении этих правил: сохранённые диапазоны
действительны только для своей версии.

## 7. Чего разбор не делает

| Не делается | Следствие |
|---|---|
| OCR | сканированные страницы дают пустой текст; полностью сканированный документ падает на пустом каноническом тексте (код 1), смешанный — получает `requires_ocr` |
| интерпретация изображений, подписи к рисункам не генерируются | `FigureRecord` содержит только метаданные и, если повезло, подпись из PDF |
| объединение таблиц через границу страниц | таблица, разорванная на две страницы, останется двумя таблицами |
| проверка смысла ячеек таблицы | ячейки как строки; качество распознавания не измеряется |
| переупорядочивание колонок «по-своему» | порядок чтения — тот, что дал Docling |
| удаление колонтитулов | `header`/`footer` остаются элементами и попадают в текст |
| склейка переносов, исправление дефисов | текст остаётся как извлечён |
| нормализация поворота страницы | `rotation` у Docling-страниц `0`; поворот не проверялся |
| извлечение метаданных PDF (автор, заголовок, DOI) | не извлекаются |
| вложения, аннотации, закладки | игнорируются |
| определение языка по страницам | язык определяется один раз на документ |
| разметка формул и ссылок | `formula` — текст, `reference` — абзац |

## 8. Связанные документы

* [`../contracts/parsed_document.md`](../contracts/parsed_document.md) — контракт результата;
* [`../contracts/document_provenance.md`](../contracts/document_provenance.md) — координаты и
  диапазоны;
* [`quality_gate.md`](quality_gate.md) — как проверяется результат;
* [`document_parser_configuration.md`](document_parser_configuration.md) — секции конфигурации
  `document_parsing`, `docling`, `ocr`;
* [`ocr_engine_evaluation.md`](ocr_engine_evaluation.md) — почему OCR выключен.
