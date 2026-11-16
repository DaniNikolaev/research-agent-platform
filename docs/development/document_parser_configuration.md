# Конфигурация конвейера документов

**Модуль:** `config.py` · **Секции:** `document_ingestion`, `document_parsing` (`docling`,
`model_assets`), `quality_gate`, `ocr` · **Итерация:** 2

Конфигурация слоистая, побеждает более поздний источник: значения по умолчанию → `config/application.yaml`
и `config/models.yaml` → переменные окружения из `ENVIRONMENT_OVERRIDES` (их шесть, и ни одна не
относится к документам). Секретов в конфигурации нет по построению.

**Текущее состояние репозитория:** `config/application.yaml` содержит только секции Итерации 1.
Секций `document_ingestion`, `document_parsing`, `quality_gate`, `ocr` в нём **нет**, поэтому
действуют значения по умолчанию. Чтобы изменить поведение, секции нужно добавить явно.

---

## 1. `document_ingestion` — лимиты приёма

| Поле | Тип | По умолчанию | Валидация | Читается кодом |
|---|---|---|---|---|
| `allowed_extensions` | список строк `^\.[a-z]+$`, 1–8 элементов | `[".pdf", ".txt"]` | множество минус `{.pdf, .txt}` должно быть пусто | да |
| `max_pdf_size_mb` | целое 1–2000 | `100` | — | да |
| `max_txt_size_mb` | целое 1–2000 | `20` | — | да |
| `max_pdf_pages` | целое 1–20 000 | `500` | — | да |
| `calculate_sha256` | bool | `true` | — | **нет** (хеш считается всегда) |
| `verify_source_unchanged` | bool | `true` | — | да |

Правило валидации: **только `.pdf` и `.txt`**. Попытка добавить `.docx` даёт ошибку конфигурации
(`unsupported document extensions … this iteration accepts PDF and TXT only`), а не «молча
проигнорировать».

Производное свойство `max_size_bytes` = `{".pdf": max_pdf_size_mb·1MiB, ".txt": max_txt_size_mb·1MiB}`.

```yaml
document_ingestion:
  allowed_extensions: [".pdf", ".txt"]
  max_pdf_size_mb: 100
  max_txt_size_mb: 20
  max_pdf_pages: 500
  calculate_sha256: true
  verify_source_unchanged: true
```

## 2. `document_parsing` — выбор парсера

| Поле | Тип | По умолчанию | Валидация | Читается кодом |
|---|---|---|---|---|
| `default_pdf_parser` | `docling` \| `pymupdf` | `docling` | — | **частично**: используется только для проверки «fallback ≠ primary»; фактический парсер PDF всегда Docling |
| `fallback_pdf_parser` | `pymupdf` \| `none` | `pymupdf` | обязан отличаться от `default_pdf_parser` | да |
| `docling` | `DoclingSettings` | см. §2.1 | — | да |
| `model_assets` | `ModelAssetsSettings` | см. §2.2 | — | **нет** |
| `preserve_page_provenance` | bool | `true` | — | **нет** (провенанс сохраняется всегда) |
| `preserve_bbox` | bool | `true` | — | **нет** |
| `preserve_reading_order` | bool | `true` | — | **нет** |

Правило валидации: **`fallback_pdf_parser` должен отличаться от `default_pdf_parser`** — иначе
fallback повторит тот же отказ: `fallback_pdf_parser must differ from default_pdf_parser`.

Практическое следствие: `default_pdf_parser = "pymupdf"` не переключает конвейер на PyMuPDF
(он остаётся аварийным путём), но и не запрещён валидацией. Это расхождение имени поля и поведения
зафиксировано, а не спрятано.

```yaml
document_parsing:
  default_pdf_parser: docling
  fallback_pdf_parser: pymupdf
  preserve_page_provenance: true
  preserve_bbox: true
  preserve_reading_order: true
  docling:
    do_ocr: false
    do_table_structure: true
    generate_picture_images: false
    do_cell_matching: true
  model_assets:
    allow_unpinned: false
    allow_auto_download: false
    cache_dir: null
```

### 2.1. `document_parsing.docling`

| Поле | По умолчанию | Валидация | Передаётся в Docling |
|---|---|---|---|
| `do_ocr` | `false` | **`true` отвергается** | да |
| `do_table_structure` | `true` | — | да |
| `generate_picture_images` | `false` | — | да |
| `do_cell_matching` | `true` | — | **нет** (только в `parser_options_hash`) |

Правило валидации: **OCR обязан оставаться выключенным**. `do_ocr: true` даёт ошибку
конфигурации: `docling.do_ocr must stay false: no OCR engine is approved by the owner (OI-008)`.
Вторая линия защиты — конструктор `DoclingPdfParser`, который поднимает `ValueError` при
`do_ocr = True`. То есть включить OCR нельзя ни конфигурацией, ни кодом, ни случайной правкой
одного файла.

### 2.2. `document_parsing.model_assets`

| Поле | По умолчанию | Читается кодом | Смысл |
|---|---|---|---|
| `allow_unpinned` | `false` | **нет** | декларация: неподкреплённые ревизии весов не используются |
| `allow_auto_download` | `false` | **нет** | декларация: веса скачиваются только по явному разрешению |
| `cache_dir` | `null` | **нет** | необязательный явный каталог кеша |

Политика модельных assets:

* в Итерации 2 веса **уже** находятся в стандартном кеше Hugging Face
  (`%USERPROFILE%\.cache\huggingface`), вне репозитория;
* `composition/document_pipeline.py` не передаёт `artifacts_path`, поэтому Docling использует свой
  механизм поиска/загрузки весов; платформа этот процесс не контролирует;
* следовательно `allow_auto_download = false` — **объявление политики, а не технический
  блокиратор**: если весов в кеше не окажется, загрузку выполнит сам Docling. Для жёсткого
  запрета потребуется передавать `artifacts_path` на подготовленный офлайн-каталог и/или
  отключать сеть на уровне окружения;
* `cache_dir` не влияет ни на что: поле объявлено для будущей проводки.

## 3. `quality_gate` — пороги и переключатели

| Поле | Тип | По умолчанию | Влияет на вердикт |
|---|---|---|---|
| `policy_version` | `^\d+\.\d+$` | `1.0` | записывается в отчёт |
| `fail_on_page_count_mismatch` | bool | `true` | **нет** (расхождение всегда `critical`) |
| `require_page_provenance` | bool | `true` | да |
| `warn_on_empty_pages` | bool | `true` | да |
| `warn_on_low_text_density` | bool | `true` | **нет** (поля нет в `QualityPolicy`) |
| `detect_garbled_text` | bool | `true` | да |
| `verify_character_ranges` | bool | `true` | да |
| `verify_table_structure` | bool | `true` | да |
| `minimum_meaningful_text_chars` | 1–10 000 | `50` | да |
| `low_text_chars_per_page` | 1–10 000 | `120` | да |
| `max_replacement_char_ratio` | 0.0–1.0 | `0.02` | да |
| `max_control_char_ratio` | 0.0–1.0 | `0.01` | да |
| `image_dominant_area_ratio` | 0.0–1.0 | `0.85` | да |
| `max_parser_warnings` | 0–1000 | `20` | **нет** (не читается) |

Эти значения попадают в `QualityReport.policy`, поэтому отчёт самодостаточен: по нему видно, с
какими порогами принималось решение. Подробнее о проверках и неиспользуемых ключах —
[`quality_gate.md`](quality_gate.md).

```yaml
quality_gate:
  policy_version: "1.0"
  minimum_meaningful_text_chars: 50
  low_text_chars_per_page: 120
  max_replacement_char_ratio: 0.02
  max_control_char_ratio: 0.01
  image_dominant_area_ratio: 0.85
  require_page_provenance: true
  warn_on_empty_pages: true
  detect_garbled_text: true
  verify_character_ranges: true
  verify_table_structure: true
```

## 4. `ocr` — политика OCR

| Поле | Тип | По умолчанию | Валидация |
|---|---|---|---|
| `enabled` | bool | `false` | при `true` обязателен `engine_id` |
| `engine_id` | `ShortStr` \| `null` | `null` | — |
| `supported_languages` | список 1–8 строк | `["ru", "en"]` | — |

Правило валидации: `ocr.enabled: true` без `engine_id` даёт ошибку
`ocr.enabled=true requires ocr.engine_id; the engine is chosen by the owner (OI-008)`.

**В Итерации 2 секция не влияет ни на что, кроме валидации:** реализация `OcrEnginePort`
отсутствует, `do_ocr` принудительно `false`, страницы без текста уходят в `requires_ocr`.
Оценка движков — в [`ocr_engine_evaluation.md`](ocr_engine_evaluation.md).

```yaml
ocr:
  enabled: false
  engine_id: null
  supported_languages: ["ru", "en"]
```

## 5. Воспроизводимость разбора

Разбор воспроизводим, если совпадают: байты, версия парсера, опции, версия пайплайна и версия
канонизации. Всё это записано в артефактах.

| Что | Где лежит | Пример значения |
|---|---|---|
| хеш исходных байтов | `SourceDocumentManifest.source_sha256`, `ParsedDocument.source_sha256` | 64 hex |
| `document_id` | оба контракта | `doc_…` |
| имя парсера | `ParsedDocument.parser.parser_name` | `docling` |
| версия парсера | `ParsedDocument.parser.parser_version` | `2.150.0` (из метаданных пакета; `unavailable`, если пакета нет) |
| версия пайплайна | `ParsedDocument.parser.pipeline_version` | `standard-pdf/1` |
| хеш опций | `ParsedDocument.parser.parser_options_hash` | 16 hex |
| бэкенд | `ParsedDocument.parser.backend_name` | `docling-parse` |
| таблицы / OCR | `table_extraction_enabled`, `ocr_enabled` | `true` / `false` |
| версия канонизации | `ParsedDocument.canonical_text.canonicalization_version` | `1.0` |
| версия политики качества | `QualityReport.quality_policy_version` | `1.0` |
| предупреждения парсера и диагностика | `RUN_LOG`-артефакт запуска | JSON |

Как проверить совпадение двух разборов: сравнить `parser_version`, `pipeline_version` и
`parser_options_hash` в артефактах `parsed_document` двух запусков. Совпали — разбор выполнялся
при одинаковых настройках.

Ограничения воспроизводимости, которые нужно знать:

1. **Версия весов не фиксируется.** `model_assets_revision` в `ParsedDocument.parser` всегда
   `null`: фактические ревизии двух репозиториев в кеше Hugging Face в провенанс не попадают.
   Одинаковый `parser_options_hash` при обновлённых весах даст другой результат;
2. **`block_id` не воспроизводим между запусками** для Docling: в хеш входит `run_id` (см.
   [`../contracts/document_provenance.md`](../contracts/document_provenance.md));
3. **`default_pdf_parser` не переключает парсер**: значение `pymupdf` пройдёт валидацию (если
   fallback отличается), но фактический разбор PDF останется за Docling;
4. **Собственная недетерминированность Docling не измерялась.** Повторный разбор одного файла в
   двух запусках не сравнивался; утверждение «одинаковые опции → одинаковый текст» не проверено
   экспериментом.

## 6. Как ошибки конфигурации выглядят в CLI

| Ситуация | Поведение |
|---|---|
| неизвестный ключ в секции | `ValidationError` (`extra="forbid"`) → CLI: `error: invalid configuration`, код **2** |
| `docling.do_ocr: true` | `ValidationError` → код **2** |
| `allowed_extensions: [".docx"]` | `ValidationError` → код **2** |
| `fallback_pdf_parser == default_pdf_parser` | `ValidationError` → код **2** |
| `ocr.enabled: true` без `engine_id` | `ValidationError` → код **2** |
| отсутствующий YAML | работают значения по умолчанию (файлы опциональны) |

Проверить конфигурацию без обработки документа: `research-platform config validate` (код 0 или 2).

## 7. Связанные документы

* [`document_ingestion.md`](document_ingestion.md) — что делают лимиты;
* [`document_parsing.md`](document_parsing.md) — как опции влияют на разбор;
* [`quality_gate.md`](quality_gate.md) — как пороги влияют на вердикт;
* [`ocr_engine_evaluation.md`](ocr_engine_evaluation.md) — почему OCR выключен;
* [`configuration.md`](configuration.md) — общая модель конфигурации Итерации 1.
