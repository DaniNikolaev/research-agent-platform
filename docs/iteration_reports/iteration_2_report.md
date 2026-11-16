# Отчёт по Итерации 2 — обработка документов

**Проект:** `research-agent-platform` · **Дата:** 2026-11-14 · **Основание:** ТЗ Итерации 2, ADD
v1.0, `docs/iteration_reports/iteration_1_report.md` · **Исполнитель:** DSH ·
**Среда:** Windows 10 Pro 22H2, CPython 3.13.18, uv 0.13.7, docling 2.150.0, docling-core 2.106.0,
pymupdf 2.1, pydantic 2.14.3, langgraph 1.2.20

---

## 1. Executive Summary

**Итог: `PASS WITH CONDITIONS`.**

Конвейер обработки документа работает end-to-end на двух типах источников: цифровой PDF
(через настоящий Docling) и TXT. Приём выполняет проверки входа, выводит стабильный
`document_id`, сохраняет неизменяемый оригинал и манифест; разбор строит `ParsedDocument` со
страницами, блоками, таблицами и каноническим текстом с проверяемыми символьными диапазонами;
Quality Gate выдаёт объяснимый вердикт; всё складывается в артефакты запуска; CLI отображает
вердикт в устойчивый код возврата. Тесты: **619 passed, 0 failed, 0 skipped**; `ruff check` и
`ruff format --check` — без замечаний; `tools/export_schemas.py --check` — 10 схем актуальны.

Условия, без выполнения которых итог не может считаться полным `PASS`:

| № | Условие | Что именно не проверено/не сделано |
|---|---|---|
| C1 | **OCR недоступен** | движок не выбран (`OI-008`), реализация `OcrEnginePort` отсутствует, `do_ocr = false` принудительно; сканированные страницы только детектируются, текста не появляется |
| C2 | **качество таблиц на реальных статьях не доказано** | TableFormer включается (`do_table_structure = true`), структура сохраняется, но качество извлечения ячеек измерялось только на синтетических фикстурах; ни одной реальной статьи с таблицами не обработано |
| C3 | **многостраничные и двухколоночные документы не проверены** | кроме одной пробы реальный корпус не разбирался; порядок чтения двух колонок, сноски, формулы, поворот страниц не подтверждены экспериментом |
| C4 | **14 диагностик `ty` остаются** | все — в местах динамической сборки LangGraph и обращения к типам PyMuPDF/Docling; исполняются тестами, но статически не закрыты |

Ни одна из этих оговорок не является дефектом кода: C1 — незакрытое решение владельца, C2 и C3 —
недостаток проверки, C4 — принятый технический долг. Отчёт честно разделяет «сделано и проверено»
и «сделано, но не проверено».

Отдельно, потому что это важнее формулировки C1: **полностью сканированный PDF сейчас не доходит
до Quality Gate.** Если текстовых блоков нет ни на одной странице, канонический текст пуст, и узел
`build_canonical_document` завершает запуск ошибкой `the canonical text is empty`
(`document_pipeline_error`, код 1). Вердикт `requires_ocr` достижим для **смешанного** документа,
где часть страниц содержит текст, либо при прямом вызове Gate (как в тестах). Это не проектная
цель, а следствие правила «пустой канонический текст — ошибка»; при включении OCR поведение нужно
пересматривать.

---

## 2. Реализованные компоненты

| Компонент | Назначение | Файлы | Тесты | Статус |
|---|---|---|---|---|
| Доменные контракты документов | идентичность, манифест приёма, страницы/блоки/таблицы/рисунки, разобранный документ, отчёт качества, геометрия | `domain/documents/{identity,models,elements,parsed,quality,provenance}.py`, `domain/documents/__init__.py` | `tests/parsing/test_document_pipeline.py` (идентичность, валидаторы), `tests/contract/test_schema_export.py` | готово, схемы опубликованы |
| Порты конвейера | 5 портов на байтах: парсер, инспектор, декодер, OCR, чтение источника | `application/ports/document_pipeline.py` | косвенно (типизация фикстур) | готово; OCR-порт без реализации |
| Сервис приёма | allow-list, лимиты, сигнатура, SHA-256, `document_id`, коллизия, инспекция, декодирование, сохранение оригинала и манифеста, повторная проверка | `application/services/document_ingestion.py` | 3 теста приёма + round-trip CLI | готово |
| Сервис разбора | выбор адаптера, fallback, канонизация, диапазоны, сборка `ParsedDocument`, сохранение артефактов | `application/services/document_parsing.py` | real-Docling тест, round-trip CLI | готово |
| Quality Gate | постраничные измерения, 19 идентификаторов проверок, вердикт, отчёт | `application/services/quality_gate.py` | 7 тестов Gate | готово |
| Определение языка | доля кириллицы/латиницы по буквам, порог 200 букв и 0.80 | `application/services/language_detection.py` | 2 теста | готово |
| Ошибки конвейера | нормализованные коды, стадия, retry-политика как данные | `application/services/document_errors.py` | косвенно | готово (часть кодов не используется) |
| Id артефактов запуска | `<prefix>_<doc-tail>_<run-tail>` | `application/services/artifact_ids.py` | round-trip CLI | готово |
| Docling-адаптер | PDF → `ParsedPayload`: страницы, блоки, таблицы, рисунки, разделы, bbox | `platform/document_parsing/docling/{pdf_parser,converters,options}.py` | real-Docling тест | готово, качество на реальных статьях не проверено |
| PyMuPDF-адаптер | инспекция (страницы, шифрование, текст, изображения) и аварийный плоский текст | `platform/document_parsing/pymupdf/pdf_inspector.py` | косвенно (через Gate и real-Docling) | готово |
| TXT-адаптеры | детерминированное декодирование и блочный разбор без страниц | `platform/document_parsing/txt/{decoder,parser}.py` | 5 тестов декодера, round-trip CLI | готово |
| Чтение источника | регулярный файл, отказ ссылкам, лимит до загрузки, fingerprint | `platform/documents/file_reader.py` | косвенно | готово |
| LangGraph-конвейер | 10 узлов, маршрутизация pdf/txt, управляемая остановка, кэш рантайма | `workflows/{document_state,document_workflow}.py` | round-trip CLI | готово; smoke-граф не тронут |
| Композиция | сборка адаптеров и зависимостей конвейера | `composition/document_pipeline.py` | косвенно | готово |
| CLI | `document process`, `document report`, `--dry-run`, `--json`, коды 0–4 | `cli.py` | 2 теста CLI | готово; `--dry-run` фактически не останавливает обработку |
| Схемы | 3 новые схемы `contracts/documents/v1/` | `platform/contracts/schemas.py`, `contracts/documents/v1/*.json` | `test_schema_export.py` | 10 схем актуальны |

---

## 3. Document Ingestion

Порядок шагов намеренный: расширение → размер → сигнатура → хеш/идентичность → коллизия →
инспекция → декодирование → сохранение оригинала → сверка хеша → повторная проверка источника →
манифест.

| Механизм | Факт |
|---|---|
| allow-list | только `.pdf` и `.txt`; иное — `unsupported_format` |
| лимиты размера | 100 MB для PDF, 20 MB для TXT (проверяются после чтения, «подсказка» чтению — 100 MB) |
| лимит страниц PDF | 500 (`pdf_page_limit_exceeded`) |
| сигнатура | `.pdf` обязан начинаться с `%PDF-`; `.txt` не должен содержать NUL в первых 4096 байтах |
| `document_id` | `doc_` + 24 hex от `SHA-256("research-agent-platform/document/v1|<тип>|<sha256>")` |
| коллизия | тот же `document_id` с другим содержимым → `document_identity_collision` |
| инспекция PDF | PyMuPDF: число страниц, шифрование, повреждение |
| декодирование TXT | BOM → UTF-16 без BOM по NUL → строгий UTF-8 → cp1251 → koi8-r → cp1252 → замена с `encoding_uncertain` |
| оригинал | артефакт `original_document`, затем сверка sha256 (`source_integrity_error` при расхождении) |
| повторная проверка | файл перечитывается и хешируется; расхождение → `source_changed_during_ingestion` |
| символьные ссылки | отвергаются (`source_not_readable`) |

Коды ошибок, реально поднимаемые приёмом: `source_not_found`, `source_not_readable`,
`unsupported_format`, `file_empty`, `file_too_large`, `invalid_file_signature`,
`source_changed_during_ingestion`, `source_integrity_error`, `document_identity_collision`,
`pdf_corrupt`, `pdf_encrypted`, `pdf_empty`, `pdf_page_limit_exceeded`.

Отдельно зафиксировано: `text_encoding_error` объявлен, но **не порождается** — не декодируемый
строго файл принимается с заменой символов и `encoding_uncertain = true`. Подробности —
[`../development/document_ingestion.md`](../development/document_ingestion.md).

---

## 4. Document Manifest

Новый контракт `SourceDocumentManifest` **1.0** (`contracts/documents/v1/source_manifest.schema.json`)
— дополнение, а не изменение: опубликованный `DocumentManifest` пакета Agent 1A (контракт 2.1) не
менялся ни на поле.

| Свойство | Значение |
|---|---|
| поля | `document_contract_version`, `document_id`, `run_id`, `source_file_name`, `source_type`, `source_media_type`, `source_sha256`, `source_size_bytes`, `source_artifact_uri`, `original_encoding`, `encoding_uncertain`, `language`, `language_detection_method`, `created_at`, `processing_status` |
| инварианты | имя файла без пути; MIME ↔ тип; кодировка только у TXT; артефакт принадлежит **документу и запуску** |
| хранение | артефакт `handoff_package`, `application/json`, `schema_version = 1.0` |
| отличие от пакета 2.1 | пакет описывает представление для Agent 1B (включая `page_count`); манифест приёма дополнительно описывает, **как файл получен** (размер, кодировка, метод определения языка, статус) |

Честные детали: `processing_status` в сохранённом манифесте остаётся `ingested` (манифест
неизменяем, дальнейшие статусы живут в состоянии графа); значение `language_detection_method =
"bom_and_ratio"` объявлено в контракте, но текущий детектор его не возвращает. Полное описание —
[`../contracts/document_manifest.md`](../contracts/document_manifest.md).

---

## 5. PDF Parsing (Docling, options, provenance, PyMuPDF inspector, fallback)

### 5.1. Docling

| Аспект | Реализация |
|---|---|
| опции | `do_ocr = false` (конструктор отвергает `true`), `do_table_structure = true`, `generate_picture_images = false`, `images_scale = 1.0`; `do_cell_matching`, `layout_model`, `table_model`, `backend`, `force_full_page_ocr` участвуют в хеше опций, но **не передаются** в Docling |
| версия пайплайна | `standard-pdf/1`; `parser_version` — из метаданных пакета (`2.150.0`) |
| страницы | строятся из таблицы страниц документа, поэтому присутствуют все физические страницы 1..N, включая пустые (`status = empty`) |
| bbox | Docling отдаёт BOTTOMLEFT `l/t/r/b`; адаптер переводит в PDF points с верхним левым началом по высоте и размерам страницы; при неизвестной высоте — `unit = unknown` + `system` |
| метки | явная таблица (`section_header`/`title` → heading, `text`/`paragraph`/`code`/`reference`/`document_index` → paragraph, `list_item`/`list` → list_item, `table`, `table_caption`, `picture`/`figure` → figure, `picture_caption`/`caption` → figure_caption, `footnote`, `page_header` → header, `page_footer` → footer, `formula`); неизвестная метка → `unknown`, сырая метка сохраняется в `metadata.docling_label` |
| символьные диапазоны | адаптер их **не** вычисляет — это делает канонизация |
| отказ | техническое исключение → `ValueError`; сервис решает, разрешён ли fallback |

### 5.2. PyMuPDF-инспектор

Считает число страниц, шифрование, родной текст (`has_native_text` при ≥ 20 символах), число
изображений и долю площади под ними, поворот страницы. Работает на этапе приёма и служит
**независимым эталоном** для проверки числа страниц в Quality Gate.

### 5.3. Fallback

Включается только при техническом отказе основного парсера, только если
`fallback_pdf_parser = "pymupdf"`. Даёт постраничный плоский текст: без bbox, без таблиц, без
разделов, `source = pymupdf_fallback`, `parsing_status = parsed_with_fallback`. Gate после него
всегда выносит `fallback_parse_requires_review` → `needs_human_review`.

---

## 6. TXT Parsing

| Шаг | Правило |
|---|---|
| декодирование | BOM (UTF-8/UTF-16) → UTF-16 без BOM по NUL-чётности → строгий UTF-8 → cp1251 → koi8-r → cp1252 → замена с `encoding_uncertain = true` и числом замен |
| лимит | 5 000 000 символов; больше — `ValueError` (не нормализованная ошибка) |
| страницы | **не изобретаются**: `page_number = null`, `page_count = null`, `pages = []` |
| блоки | абзацы из подряд идущих непустых строк, склеенные `\n`; граница — пустая строка |
| заголовки | слабый документированный признак: короткая строка (≤ 120), без терминальной пунктуации, за ней пустая строка или конец файла, первый символ не строчный → `heading`, `level = 1`, `confidence = 0.6` |
| метаданные блока | `line_start`, `line_end` (0-based) |
| пустой текст | `parsing_status = empty` + предупреждение |

Практическое следствие: файл, состоящий только из пробелов/переводов строк, проходит приём, но
даёт пустой канонический текст, и запуск завершается ошибкой `the canonical text is empty` (код 1) —
до Quality Gate дело не доходит.

---

## 7. Canonical Document Model

`ParsedDocument` 1.0 — независимое от парсера представление: `pages`, `sections`, `elements`,
`tables`, `figures`, `canonical_text`, `parser` (провенанс), `parsing_status`.

Канонизация версии **1.0**: элементы в порядке чтения, `strip` по краям, разделитель `\n\n`,
разделитель страниц `\f`, пустые блоки пропускаются, ничего больше не переписывается. Диапазоны
`char_start`/`char_end` (0-based, конец не включается, code points) вычисляются **при сборке**
текста, поэтому инвариант `canonical_text[start:end] == element.text` выполняется по построению и
дополнительно проверяется Gate (`provenance_character_ranges`).

Контракт накладывает инварианты: TXT без страниц и без `page_number` у блоков; PDF с полной
последовательностью `1..N`; уникальные `block_id`; ссылки только на известные страницы и разделы;
`page.block_ids` ⊆ элементов; таблицы и рисунки на реальных страницах; `empty` не притворяется
текстом; страница `parsed` обязана иметь текст.

Полное описание — [`../contracts/parsed_document.md`](../contracts/parsed_document.md) и
[`../contracts/document_provenance.md`](../contracts/document_provenance.md).

---

## 8. Tables and Figures

**Что действительно извлекается из таблиц:**

* `TableRecord` с матрицей ячеек (`cells`), `row_count`, `column_count`, `caption`,
  `text_representation` (`a | b | c` по строкам), `extraction_status`
  (`complete`/`partial`/`empty`), `bbox`, `page_number`, `section_id`, `reading_order`;
* элемент `table`, чей текст — то же табличное представление, поэтому таблица попадает в
  канонический текст и, значит, в поиск;
* Quality Gate проверяет **форму**: пустая матрица → провал `major`, частичные ячейки →
  предупреждение `minor`.

**Что извлекается из рисунков:**

* `FigureRecord` с `figure_id`, страницей, `bbox`, `reading_order`, `section_id` и подписью,
  если подпись есть в PDF как отдельный блок;
* подпись рисунка дополнительно становится элементом `figure_caption`.

**Чего нет:**

| Не делается | Следствие |
|---|---|
| объединение таблиц через границу страниц | разорванная таблица остаётся двумя записями |
| проверка качества распознавания ячеек | «таблица извлеклась» ≠ «ячейки верные»; на реальных статьях не измерялось |
| анализ заголовка таблицы | `has_header_row = true` при непустой матрице |
| рендер и интерпретация изображений | `generate_picture_images = false`; `image_artifact_uri = null`; подписи не генерируются |
| элемент для рисунка без подписи | `FigureRecord` есть, текстового блока нет |

---

## 9. Quality Gate

19 идентификаторов проверок: 18 из перечня требований плюс добавленная в реализации
`structure_text_source_has_no_pages` (только TXT; структурные проверки PDF к тексту неприменимы).
Страничные проверки: `page_ocr_required`, `page_garbled_text`, `page_blank_or_unknown`;
структурные: `structure_page_count_matches_source` (`critical`), `structure_pages_complete`,
`structure_reading_order_unique`, `structure_page_provenance`; текстовые: `text_extracted`,
`text_meaningful_length`, `text_replacement_characters`, `text_control_characters`;
провенанс: `provenance_character_ranges`, `provenance_bbox_bounds`; таблицы: `tables_structure`;
парсер: `parser_status`; TXT: `text_source_no_pagination`, `text_source_no_page_numbers`;
fallback: `fallback_parse_requires_review`.

Статусы страниц: `ok`, `low_text`, `image_dominant`, `suspected_garbled`, `ocr_required`,
`blank_or_unknown`, `parse_failed`. Вердикт: провал `critical` → `failed`; страницы OCR →
`requires_ocr`; провал `major` / повреждённый текст / fallback → `needs_human_review`; только
предупреждения → `passed_with_warnings`; иначе `passed`.

Пороги (политика 1.0, записываются в отчёт): 50 символов «осмысленного» текста, 120 символов на
страницу для `low_text`, 0.02 доли символов замены, 0.01 доли управляющих символов, 0.85 площади
изображений.

Ключи конфигурации, которые валидируются и попадают в отчёт, но на вердикт **не влияют**:
`fail_on_page_count_mismatch`, `warn_on_low_text_density`, `max_parser_warnings`. Это зафиксировано
в [`../development/quality_gate.md`](../development/quality_gate.md) вместе с примерами всех пяти
вердиктов и полным JSON отчёта в
[`../contracts/quality_report.md`](../contracts/quality_report.md).

---

## 10. OCR Routing

**Что работает — детекция.** Страница без пригодного текстового слоя получает
`ocr_required`, если она похожа на скан: нет родного текста и есть изображения, либо текста нет
при 0.85+ площади изображений. Дальше возможны два исхода:

* **смешанный документ** (у части страниц текст есть, канонический текст непуст) → Gate выдаёт
  `requires_ocr`, `ocr_required_pages` содержит номера страниц, запуск переводится в
  `waiting_for_human` с причиной `ocr_required: no approved OCR engine (OI-008)`, CLI возвращает
  код 4;
* **полностью сканированный документ** (текста нет нигде, канонический текст пуст) → узел
  `build_canonical_document` завершает запуск ошибкой `the canonical text is empty`
  (`document_pipeline_error`, код 1) **до** Quality Gate. Детекция страниц в этом случае просто не
  доходит до отчёта.

Второй исход — ограничение текущего правила «пустой канонический текст = ошибка», а не решение об
OCR. Он зафиксирован как ограничение и должен быть пересмотрен при включении OCR.

**Что не работает — распознавание.** `OcrEnginePort` объявлен (свойства `engine_id`,
`engine_version`, `supported_languages()`, `is_available()`, `recognize(...)`; контракт данных
`OcrRequest`/`OcrResult`/`OcrPageResult`), но реализации нет. `document_parsing.docling.do_ocr`
принудительно `false`: конфигурация отвергает `true`, конструктор адаптера — тоже.
`ocr.enabled = false`, `ocr.engine_id = null`.

**Оценка движков** — [`../development/ocr_engine_evaluation.md`](../development/ocr_engine_evaluation.md).
Кратко: в Docling 2.150.0 доступны `rapidocr`, `easyocr`, `tesseract`/`tesseract_cli`, `ocrmac`
(только macOS), `nemotron` (в авто-выборе — Linux+CUDA), удалённый `kserve`, а также `auto`
(недетерминированный выбор «что установлено»). В окружении сейчас есть только пакет
`rapidocr 3.10.0` с встроенными ONNX-весами (~30.4 MB) — как зависимость Docling; Tesseract,
`tesserocr`, `easyocr` отсутствуют; OCR-веса не скачивались; ни один движок не запускался.

**Вопросы владельцу:** нужен ли русский OCR в Итерации 3; приоритет «минимальная установка» против
«максимальное качество на русском»; наличие GPU; допустимость автозагрузки весов; допустимые
лицензии весов; кто измеряет качество (CER/WER, время, память) и на каком наборе; как OCR-текст
получает провенанс (`block_id`, `bbox`, confidence) — это контрактное решение, а не деталь
адаптера.

---

## 11. LangGraph

Отдельный граф, не связанный с `agent1a workflow-smoke` (тот не изменялся и продолжает проходить
тесты).

```text
START → validate_input → initialize_run → ingest_source → inspect_document
      → route_by_format ─┬→ parse_pdf
                         └→ parse_txt
      → build_canonical_document → run_quality_gate
      → persist_parsed_artifacts → finalize_run → END
```

| Аспект | Реализация |
|---|---|
| состояние | `DocumentProcessingState` (TypedDict): идентификаторы, URI артефактов, счётчики, статусы, предупреждения; крупных объектов нет |
| крупные объекты | байты источника, `ParsedDocument`, отчёт — в `DocumentRuntimeCache`, по одному на запуск |
| маршрутизация | условное ребро по `source_type`: `pdf` → `parse_pdf`, иначе `parse_txt` |
| лимит шагов | `recursion_limit = runtime.max_graph_steps × 4` (по умолчанию 200) |
| ошибка узла | исключение останавливает граф; раннер нормализует (`DocumentPipelineError`), помечает запуск `failed` и поднимает ошибку дальше |
| управляемая остановка | **не** исключение: артефакты сохраняются, запуск завершается вердиктом Gate (`waiting_for_human` для `requires_ocr`/`needs_human_review`) |
| стадии запуска | `registration` → `parsing` → `quality_gate` через `RunManager.update_stage` |
| жизненный цикл | `start_run` (created → running) → `complete_run` / `request_human_review` / `fail_run` |
| пустой текст | `build_canonical_document` читает канонический текст из хранилища и падает при нулевой длине: whitespace-only TXT даёт ошибку запуска, а не вердикт Gate |

---

## 12. Artifact Store

Шесть артефактов на запуск; идентификаторы включают хвост `document_id` (12 символов) и хвост
`run_id`, поэтому повторная обработка того же файла в новом запуске не сталкивается с прежними
записями; URI остаются в документном пространстве.

| Артефакт | `artifact_id` | Тип | Media type | Версия схемы |
|---|---|---|---|---|
| оригинал документа | `art_source_<doc12>_<run-tail>` | `original_document` | `application/pdf` / `text/plain` | — |
| манифест приёма | `art_manifest_<doc12>_<run-tail>` | `handoff_package` | `application/json` | `1.0` |
| разобранный документ | `art_parsed_<doc12>_<run-tail>` | `parsed_document` | `application/json` | `1.0` |
| канонический текст | `art_canonical_<doc12>_<run-tail>` | `parsed_document` | `text/plain` | `1.0` (версия канонизации) |
| отчёт качества | `art_quality_<doc12>_<run-tail>` | `quality_report` | `application/json` | `1.0` |
| диагностика | `art_diag_<doc12>_<run-tail>` | `run_log` | `application/json` | — |

URI: `artifact://documents/<document_id>/runs/<run_id>/<artifact_type>/<artifact_id>`.

Свойства хранилища, на которые опирается конвейер:

* **неизменяемость**: `artifact_id` ↔ один хеш; повтор с другим содержимым — `ArtifactConflictError`,
  а не перезапись; идемпотентный повтор с тем же содержимым разрешён;
* **видимость**: артефакт читается только при наличии объекта **и** валидного sidecar метаданных;
  объект без sidecar — остаток сбоя, а не артефакт;
* **целостность**: sha256 считается хранилищем, записывается в метаданные и проверяется при каждом
  чтении (размер и URI тоже);
* **атомарность**: объект и sidecar публикуются раздельно, каждый через staging + эксклюзивную
  публикацию (`os.link`), перезапись невозможна;
* **адресация**: три пространства (документ, запуск, платформа); публикуемый `ArtifactReference` —
  только документное.

Замечание: тип `parsed_document` используется дважды — для JSON-модели и для текста; различаются
они `artifact_id` и media type. Это осознанно (тип описывает смысл артефакта, а не формат), но
требует внимания при чтении: `text/plain` + `parsed_document` = канонический текст.

---

## 13. CLI

| Команда | Параметры | Что делает |
|---|---|---|
| `document process` | `--input PATH` (обязателен), `--dry-run`, `--project-root PATH`, `--json` | приём → разбор → Gate для одного файла; печатает идентификаторы, размер/хеш, число страниц, язык, парсер, fallback, вердикт, URI артефактов, статус запуска, причину остановки |
| `document report` | `--run-id ID` (обязателен), `--project-root PATH`, `--json` | читает сохранённый артефакт отчёта качества запуска и печатает статус, политику, страницы, счётчики и список проверок |

Коды возврата: **0** — `passed`/`passed_with_warnings`; **1** — ошибка входа/приёма/разбора или
вердикт `failed`; **2** — ошибка конфигурации; **3** — инфраструктура/парсер недоступны (объявлено,
но путь практически недостижим, см. §16); **4** — управляемая остановка
(`requires_ocr`/`needs_human_review`).

Примеры (значения идентификаторов иллюстративны):

```powershell
uv run --group parsing research-platform document process --input D:\papers\article.pdf
uv run research-platform document process --input D:\notes\note.txt --json
uv run research-platform document report --run-id run_20261114_120000_a1b2c3
```

Формат текстового вывода `document process`:

```text
document process
  run_id:            run_…
  document_id:       doc_…
  source:            <имя> (<pdf|txt>)
  size / sha256:     <байты> bytes / <64 hex>
  pages:             <N> (null for TXT)
  language:          <ru|en|unknown>
  parser:            <docling|txt-block-parser> <версия>
  fallback:          <true|false>
  quality:           <passed|passed_with_warnings|requires_ocr|needs_human_review|failed>
  warnings / errors: <N> / <N>
  parsed document:   artifact://…
  quality report:    artifact://…
  run status:        <completed|completed_with_warnings|waiting_for_human|failed>
  stop reason:       <причина, если есть>
```

Известные особенности: `--dry-run` принимается, но **не останавливает** обработку (флаг лишь
помечает начальное состояние; все узлы выполняются и артефакты сохраняются); поле JSON
`parsing_status` содержит `processing_status` графа (`completed`, `requires_ocr`, …), а не
`ParsingStatus` контракта; `document report` возвращает 0, если отчёт найден, независимо от
вердикта, и ищет отчёт только в хранилище текущего `--project-root`.

---

## 14. Тестирование

### 14.1. Полный прогон

```powershell
uv run --group parsing pytest -q
```

Результат: **619 passed, 0 failed, 0 skipped** (0.5–25 с в зависимости от прогонов Docling).

```powershell
uv run ruff check .
uv run ruff format --check .
```

Результат: без замечаний.

```powershell
uv run ty check
```

Результат: **14 диагностик** (см. §16).

```powershell
uv run python tools/export_schemas.py --check
```

Результат: все **10 схем** актуальны (7 прежних + 3 новые в `contracts/documents/v1/`).

### 14.2. Тесты на поддельных зависимостях и юнит-тесты

Файл `tests/parsing/test_document_pipeline.py` — 21 тестовая функция (24 случая с учётом
параметризации). Покрывают: стабильность и вывод `document_id`; отказ при чужом id; декодирование
UTF-8/UTF-8-BOM/cp1251/UTF-16 без BOM; определение языка ru/en и `unknown` на коротком тексте;
отказ приёма на неподдерживаемом расширении и на переименованном бинарнике; сохранение оригинала и
манифеста; семь сценариев Gate (чистый документ, расхождение страниц, страница под OCR, отсутствие
текста, сломанная таблица, fallback, пагинация у TXT); наличие CLI-команд; round-trip
`document process` → `document report` на временном проекте.

**Важная оговорка о терминологии:** отдельного fake-адаптера `DocumentParserPort` в тестах нет.
Подделка — это `SourceReaderPort` (класс `_Reader`), а документы для Gate собираются прямой
конструкцией доменных моделей (`ParsedDocument`, `PageRecord`, `DocumentElement`). То есть
«fake-parser/unit» здесь означает «без Docling», а не «через подменённый порт парсера».

### 14.3. Интеграционные тесты с настоящим Docling

Один тест с маркером `parsing`: генерирует одностраничный цифровой PDF через PyMuPDF, разбирает его
настоящим `DoclingPdfParser` и проверяет `page_count = 1`, наличие элементов, привязку всех
элементов к странице 1, `parser_name = docling`, `ocr_enabled = false`, `bbox.unit = pt`.

```powershell
uv run --group parsing pytest -q -m parsing
```

### 14.4. Тесты детекции OCR

`test_gate_requires_ocr_for_an_image_only_page`: синтетическая инспекция страницы без родного
текста, с изображением и площадью 0.95 → ожидается `ocr_required_pages = [1]` и вердикт из
множества `{requires_ocr, needs_human_review}` (утверждение намеренно допускает оба исхода).
Реальный сканированный PDF не подавался.

### 14.5. Тесты с реальным OCR

**Не выполнялись.** Ни один OCR-движок не запускался, качество распознавания не измерялось,
сравнение движков по CER/WER/времени/памяти отсутствует.

### 14.6. Чего тесты не покрывают

* многостраничные и двухколоночные PDF; поворот страниц; разные размеры страниц;
* качество извлечения таблиц на реальных статьях;
* сканированные PDF (нет движка);
* `document report` для запуска, созданного в другом корне проекта;
* `--dry-run` как режим «без разбора» (поведение не соответствует справке и тестами не
  зафиксировано);
* коды `text_encoding_error`, `parser_unavailable`, `parser_timeout`, `provenance_error`,
  `ocr_required` (как исключение) — не поднимаются и не тестируются.

---

## 15. Модельные assets

| Свойство | Значение |
|---|---|
| репозитории | `docling-project/docling-layout-heron` (layout), `docling-project/docling-models` (table structure) |
| размер по данным владельца | 171.8 MB для layout; всего в кеше ≈520 MB |
| размер по замеру каталогов кеша | `models--docling-project--docling-layout-heron` 163.8 MB; `models--docling-project--docling-models` 341.6 MB; итого ≈505 MB |
| источник | Hugging Face, скачано один раз с одобрения владельца |
| расположение | `%USERPROFILE%\.cache\huggingface`, **вне репозитория** (в git не попадает) |
| когда скачивается | при первом разборе PDF, если весов нет в кеше; платформа этот процесс не контролирует |
| OCR-веса | не скачивались |
| прочее скачанное | ничего: ни Tesseract, ни языковые пакеты, ни EasyOCR-модели |
| лицензии весов | **не проверены**; требуют проверки владельцем до распространения |
| фиксация ревизии | отсутствует: `ParsedDocument.parser.model_assets_revision = null` |

Расхождение 505 MB против ≈520 MB объясняется оценкой владельца (включая служебные файлы кеша) и
замером по файлам каталогов; принципиальным является порядок величины и расположение вне
репозитория. Пакет `rapidocr` с весами ~30.4 MB присутствует как зависимость Docling и **не**
является выбранным OCR-движком.

---

## 16. Ограничения

Полный список с деталями — [`../development/limitations.md`](../development/limitations.md)
(раздел «Итерация 2»). Здесь — сводка.

**Функциональные:**

1. нет OCR-движка; сканированные PDF поддерживаются только детекцией;
2. нет объединения таблиц через границу страниц; качество ячеек на реальных статьях не измерено;
3. нет интерпретации изображений; рисунки — метаданные и подпись;
4. нет пакетной обработки: один запуск — один файл; очереди и worker-ов нет;
5. нет кеша разборов и дедупликации: повторная обработка разбирает файл заново;
6. нет переразбора по решению Gate (`max_quality_gate_reparses` не применяется);
7. `ty check` — **14 диагностик** в `platform/document_parsing/pymupdf/pdf_inspector.py`,
   `workflows/document_workflow.py`, `application/services/quality_gate.py`,
   `domain/documents/parsed.py`, `application/services/document_parsing.py`; все места исполняются
   тестами.

**Непроверенные сценарии:** многостраничные и двухколоночные документы (кроме одной пробы),
таблицы на реальных статьях, сканированный PDF, поворот страниц, сноски/формулы/колонтитулы,
воспроизводимость повторного разбора, `document report` для «чужого» корня проекта, реальные
статьи целиком.

**Известные расхождения кода и документации:**

| Расхождение | Деталь |
|---|---|
| `--dry-run` | не останавливает обработку, хотя справка обещает «exit without parsing» |
| код возврата 3 | практически недостижим: `parser_unavailable` не поднимается, `ModuleNotFoundError` из Docling оборачивается в `document_parsing_failed` (код 1) |
| `parsing_status` в JSON CLI | содержит `processing_status` графа, а не `ParsingStatus` контракта |
| `text_encoding_error` | объявлен, но не порождается |
| `language_detection_method = "bom_and_ratio"` | объявлено, но не возвращается |
| `processing_status` манифеста | всегда `ingested` (манифест неизменяем) |
| `QualityReport.parser_warnings`, `PageQualityRecord.checks` | всегда пусты |
| `provenance_bbox_bounds` | сравнивает bbox всех страниц с первой страницей с известными размерами |
| неиспользуемые ключи конфигурации | `calculate_sha256`, `preserve_*`, `allow_auto_download`, `allow_unpinned`, `cache_dir`, `warn_on_low_text_density`, `max_parser_warnings`, `fail_on_page_count_mismatch` |
| `block_id` Docling | зависит от `run_id`, поэтому не воспроизводим между запусками |
| отказ от ссылок | покрывает символьные ссылки; жёсткие ссылки неотличимы от обычного файла |
| приём TXT | читает до 100 MB, и только затем отвергает по лимиту 20 MB |
| пустой канонический текст | узел `build_canonical_document` падает на пустом тексте, поэтому полностью сканированный документ и whitespace-only TXT не доходят до Quality Gate и не получают вердикт |
| статусы `low_text` / `image_dominant` | не порождают ни одной проверки; `PageQualityStatus.is_ocr_candidate` и `is_blocking` объявлены, но не используются |
| тест границ | список запрещённых импортов приложения не содержит `pymupdf` (в отличие от `docling`), хотя фактически `pymupdf` импортируется только в `platform/` |
| лицензия PyMuPDF | 2.1 распространяется под AGPL-3.0 или коммерческой лицензией Artifex — решение о распространении за владельцем |

**Среда:** `uv run` без `--group parsing` может удалить пакеты группы `parsing`; для PDF эта группа
обязательна. В корне проекта после тестов и прогонов Docling остаются каталоги `ing-*` (временные
каталоги тестового хелпера) и `torchinductor_*` (кэш torch), не покрытые `.gitignore`.

---

## 17. Изменённые файлы

### Создано — домен и порты

```text
src/research_platform/domain/documents/identity.py
src/research_platform/domain/documents/provenance.py
src/research_platform/domain/documents/elements.py
src/research_platform/domain/documents/parsed.py
src/research_platform/domain/documents/quality.py
src/research_platform/application/ports/document_pipeline.py
```

### Создано — сервисы, платформа, workflow, композиция

```text
src/research_platform/application/services/document_ingestion.py
src/research_platform/application/services/document_parsing.py
src/research_platform/application/services/quality_gate.py
src/research_platform/application/services/language_detection.py
src/research_platform/application/services/document_errors.py
src/research_platform/application/services/artifact_ids.py
src/research_platform/platform/document_parsing/__init__.py
src/research_platform/platform/document_parsing/docling/{__init__,pdf_parser,converters,options}.py
src/research_platform/platform/document_parsing/pymupdf/{__init__,pdf_inspector}.py
src/research_platform/platform/document_parsing/txt/{__init__,decoder,parser}.py
src/research_platform/platform/documents/{__init__,file_reader}.py
src/research_platform/workflows/{__init__,document_state,document_workflow}.py
src/research_platform/composition/document_pipeline.py
```

### Изменено — код

```text
src/research_platform/app.py                        (+13: сборка document_pipeline в контексте)
src/research_platform/cli.py                        (+215: команды document process / report)
src/research_platform/config.py                     (+152: 4 секции конфигурации)
src/research_platform/domain/documents/models.py    (+106: SourceDocumentManifest)
src/research_platform/domain/documents/__init__.py  (+82: экспорт новых моделей)
src/research_platform/platform/contracts/schemas.py (+56: 3 новые схемы)
pyproject.toml                                      (+1: маркер parsing)
```

### Создано — контракты (JSON Schema)

```text
contracts/documents/v1/source_manifest.schema.json
contracts/documents/v1/parsed_document.schema.json
contracts/documents/v1/quality_report.schema.json
```

### Создано — тесты

```text
tests/parsing/test_document_pipeline.py
```

### Изменено — тесты

```text
tests/contract/test_cli.py            (ожидаемый состав команд)
tests/contract/test_frozen_schemas.py (состав схем)
tests/contract/test_schema_export.py  (+6: пути новых схем)
```

### Создано — документация

```text
docs/contracts/document_manifest.md
docs/contracts/parsed_document.md
docs/contracts/document_provenance.md
docs/contracts/quality_report.md
docs/development/document_ingestion.md
docs/development/document_parsing.md
docs/development/quality_gate.md
docs/development/ocr_engine_evaluation.md
docs/development/document_parser_configuration.md
docs/iteration_reports/iteration_2_report.md
```

### Изменено — документация

```text
docs/development/limitations.md            (добавлен раздел «Итерация 2»)
docs/architecture/architecture_overview.md (раздел 11 «Document pipeline (Итерация 2)»)
README.md                                  (раздел «Итерация 2 — обработка документов»)
```

Документация написана по коду и метаданным установленных пакетов; при её подготовке сборка и тесты
**не запускались**, код, тесты, конфигурация и контракты **не изменялись**.

---

## 18. Открытые вопросы

| ID | Вопрос | Что блокирует | Что должен решить владелец для Итерации 3 |
|---|---|---|---|
| `OI-008` | OCR-движки и языковые пакеты для RU/EN, потребность в GPU | OCR-ветка разбора, поддержку сканированных PDF, требования к целевой машине | выбрать движок (краткий список — в [`../development/ocr_engine_evaluation.md`](../development/ocr_engine_evaluation.md)), разрешить установку и загрузку весов, задать критерии качества и допустимые лицензии |
| `OI-006` | политика локального PostgreSQL и backup | durable-хранилище запусков, миграции, восстановление | выбрать способ эксплуатации; от этого зависит, переживут ли запуски перезапуск процесса (сейчас всё в памяти, артефакты — на диске) |
| `OI-001` | имя и протокол API провайдера LLM | реальный адаптер Model Gateway | подтвердить endpoint и протокол — без этого Итерация 3 не сможет делать extraction |
| `OI-002` | подтверждённые capabilities модели (structured output, JSON Schema, tool calling, streaming) | структурированный путь извлечения | подтвердить измерениями, а не типами DTO |
| `OI-003` | реальные лимиты контекста/вывода и rate limits | политика retrieval и бюджет контекста | подтвердить фактические числа |
| `OI-004` | эффективный `model_id` провайдера | воспроизводимые `RunMetadata` | зафиксировать |
| `OI-007` | канал human-in-the-loop | использование `waiting_for_human`: сейчас вердикты `requires_ocr`/`needs_human_review` создают статус, но интерфейса подтверждения нет | выбрать CLI-механизм или UI и протокол подтверждения |
| `OI-011` | состав gold-набора статей | измерение качества парсинга и таблиц | сформировать набор (включая русские статьи и сложные PDF), иначе C2/C3 останутся недоказанными |

Отдельно, как вопросы, которые нельзя закрыть кодом: судьба `document report` при переносе
артефактов между корнями проекта; необходимость лицензионного решения по PyMuPDF (AGPL-3.0);
допустимость присутствия `rapidocr` в окружении как транзитивной зависимости Docling.

---

## 19. Готовность к Итерации 3

| Тип документа | Готовность | Обоснование |
|---|---|---|
| **цифровой PDF** (текстовый слой, одна колонка) | **готов** | проверено end-to-end на одностраничном PDF через настоящий Docling: страницы, блоки, bbox, канонический текст, вердикт `passed`, код 0 |
| **TXT** | **готов** | проверено end-to-end: декодирование (включая cp1251 и UTF-16 без BOM), блоки, заголовки с `confidence = 0.6`, отсутствие выдуманных страниц, вердикт `passed`, код 0 |
| **сканированный PDF** | **НЕ готов** | OCR заблокирован (`OI-008`). Практически: если текста нет ни на одной странице, запуск завершается ошибкой `the canonical text is empty` (код 1) ещё до Quality Gate; вердикт `requires_ocr` на полностью сканированном документе через CLI недостижим |
| **смешанный PDF** (текст + сканы) | **только детекция** | текстовые страницы разбираются, страницы-сканы получают `ocr_required`; канонический текст непуст, поэтому Gate доходит до вердикта `requires_ocr`; полного текста документа не будет |
| **документы с таблицами** | **частично готов** | структура извлекается и попадает в канонический текст, Gate проверяет форму; качество ячеек на реальных статьях **не доказано** |

Итог для Итерации 3: извлечение полей карточки можно начинать на цифровых PDF и TXT, приняв
условия C1–C4. Для статей со сканами и для подтверждения качества таблиц сначала нужны решения
владельца (`OI-008`, `OI-011`) и замер на реальном корпусе.
