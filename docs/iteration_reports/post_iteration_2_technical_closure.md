# Техническое закрытие замечаний по итогам Итерации 2

**Проект:** `research-agent-platform` · **Дата:** 2026-11-16 · **Основание:** корректирующее ТЗ «закрытие замечаний Итерации 2» · **Исполнитель:** DSH

---

## 1. Executive Summary

**Итог: `PASS`.**

Все пять замечаний, вынесенных из отчёта Итерации 2, закрыты доказательствами, а не
обещаниями:

1. **Реальный Docling** используется в integration-тестах: 12 тестов на настоящем
   `docling 2.150.0` с установленными модельными assets, без fake-парсера.
2. **Таблицы** проверяются структурно (`TableRecord`: строки, столбцы, ячейки, страница,
   provenance, `empty`/`partial`, влияние на Quality Gate), а не по pipe-тексту.
3. **Многостраничные, двухколоночные, кириллические, сканированные, смешанные, повёрнутые**
   документы покрыты отдельными fixture со своими ожидаемыми свойствами и golden-инвариантами.
4. **`ty check` — 0 замечаний** (было 14): все классифицированы, реальные ошибки исправлены,
   единственное исключение — узкий `cast` в одной точке сборки LangGraph с обоснованием.
5. **LangGraph workflow** проверен 9 интеграционными сценариями, включая контролируемые
   остановки, сбой Artifact Store и повторную обработку без перезаписи.

При этом найдены и исправлены **два реальных дефекта**, которые прежние тесты не ловили:

- сбой после создания Run **не записывался в запуск** (`graph.invoke` не мутирует переданный
  словарь состояния, поэтому `run_id` в обработчике отказа был пуст) — теперь фиксируется
  через runtime-кэш и подтверждено тестом;
- фикстура с кириллицей была **невалидной**: встроенный Helvetica не кодирует кириллицу, и
  PDF содержал `?`; тест с таким файлом доказывал бы только собственную ошибку. Переведено на
  `insert_htmlbox` с корректной картой символов.

Итоговые цифры: **656 passed**, 0 failed, 0 skipped; `ruff check`, `ruff format --check`,
`ty check`, `uv lock --check`, `export_schemas --check` — все exit 0; все CLI-команды
(включая `document process` на PDF и `document report`) — exit 0.

Регрессия отсутствует: 619 тестов предыдущего состояния сохранены, публичные контракты и
замороженные схемы не изменены, Legacy-проект не тронут.

---

## 2. Результаты первоначального аудита

| Требование ТЗ Итерации 2 | Было в репозитории до закрытия | Реальный Docling / Fake | Результат | Действие |
|---|---|---|---|---|
| PDF-INT-01 англоязычный цифровой | 1 тест, PDF строился внутри теста | реальный Docling | частично (без golden-инвариантов) | выделены fixtures, добавлены проверки структуры |
| PDF-INT-02 русскоязычный | отсутствовал | — | не покрыто | создана валидная кириллическая fixture (см. §3) |
| PDF-INT-03 таблица | отсутствовал | — | не покрыто | создана fixture, проверяется структура `TableRecord` |
| PDF-INT-04 многостраничный | отсутствовал | — | не покрыто | создана fixture из 3 страниц, проверяется пагинация |
| Двухколоночный PDF | отсутствовал | — | не покрыто | fixture + проверка порядка чтения |
| Рисунок и подпись | отсутствовал | — | не покрыто | fixture + проверка `FigureRecord` |
| Пустая страница | отсутствовал | — | не покрыто | fixture + проверка статуса страницы |
| Смешанный текст+изображение | отсутствовал | — | не покрыто | fixture |
| Скан без текста | был unit-тест Gate на сконструированной странице | fake-документ | только Gate | добавлен PDF-сценарий через реальный Docling + workflow |
| Повёрнутая страница | отсутствовал | — | не покрыто | fixture с `rotation=90` |
| Некорректный текстовый слой | отсутствовал | — | не покрыто | покрыто тестом Gate (см. §3, ограничение) |
| Golden Tests | отсутствовали | — | не покрыто | введены `assert_golden` инварианты для всех fixtures |
| Проверки таблиц (строки/столбцы/ячейки) | проверялся только непустой pipe-текст | реальный Docling | слабо | структурные проверки в §6 |
| Quality Gate: сценарии §9 ТЗ | 6 unit-тестов | fake-документы | частично | 16 сценариев в §7 |
| LangGraph integration | smoke-тесты графа | — | не покрыто | 9 сценариев в §9 |
| `ty check` | 14 замечаний | — | дефект | закрыто в §8 |

Ключевой вывод аудита: заявленный «1 integration-тест реального Docling» существовал, но
проверял только факт извлечения; не было ни golden-инвариантов, ни проверок таблиц, ни
многостраничности, ни workflow-сценариев. То есть **отчёт Итерации 2 корректно описывал
реализацию, но покрытие было тоньше заявленных возможностей** — это и закрыто.

---

## 3. PDF Fixtures

Все фикстуры генерируются детерминированно из кода (`tests/parsing/pdf_fixtures.py`), поэтому
в репозитории нет бинарных PDF. Для каждой зафиксированы назначение и ожидаемые свойства.

| Fixture | Назначение | Ожидаемые свойства |
|---|---|---|
| `single_page_en` | PDF-INT-01 | 1 страница; заголовки; текст с «quality gate»; bbox внутри страницы |
| `multi_page_en` | PDF-INT-04 | 3 страницы 1..3; три заголовка; два разделителя страниц `\f`; текст на каждой |
| `single_page_ru` | PDF-INT-02 | кириллица извлекается без U+FFFD |
| `two_column_en` | порядок чтения | оба столбца присутствуют; `reading_order` строго возрастает и уникален |
| `table_simple` | PDF-INT-03 | ≥1 таблица; ≥2 строк; ≥3 столбцов; ячейки «Transformer»/«BLEU»; страница 1 |
| `table_two` | несколько таблиц | ≥2 независимых `TableRecord` на одной странице |
| `figure_with_caption` | рисунки | ≥1 `FigureRecord` со страницей; подпись «Figure 1» в тексте |
| `empty_page` | пустая страница | 2 страницы; вторая пустая и не `parsed`; вердикт не `passed` |
| `mixed_text_image` | смешанное содержимое | текст страницы сохранён; вердикт готов |
| `scanned_only` | скан | текста нет; `page_status=ocr_required`; вердикт `requires_ocr` |
| `rotated_page` | поворот | `rotation=90`; текст сохранён; bbox внутри страницы |

**Некорректный текстовый слой не синтезируется как PDF.** PyMuPDF всегда пишет корректную
карту `ToUnicode`, поэтому детерминированно создать PDF с «сломанным» текстовым слоем нельзя.
Сценарий покрыт тестом Quality Gate на сконструированном документе (documented limitation, не
silent skip). Первая версия кириллической фикстуры использовала встроенный Helvetica: текст
записывался как `?`, и тест проверял бы сам себя — дефект найден при первом прогоне и
исправлен переходом на `insert_htmlbox`.

---

## 4. Реальные Docling Integration Tests

`tests/parsing/test_pdf_docling_integration.py` — 12 тестов, маркер `parsing`, запуск
`uv run --group parsing pytest tests/parsing -q`. Fake-парсер не используется: каждый тест
строит `DoclingPdfParser` и проходит полный путь «оригинал → артефакт → парсинг →
канонический текст → Quality Gate».

| Тест | Что доказано |
|---|---|
| `test_pdf_int_01_english_digital` | 1 страница, статус `parsed`, заголовок с секцией, текст, вердикт готов |
| `test_pdf_int_02_russian_digital` | кириллица без замен, «Детерминированное» в тексте |
| `test_pdf_int_03_table_structure` | таблица: строки/столбцы/ячейки/страница/текст-представление/`complete|partial` |
| `test_two_tables_are_reported_separately` | две таблицы на странице не слиты в одну |
| `test_pdf_int_04_multipage_pagination` | 3 страницы, полная нумерация, элементы на всех страницах, два `\f` |
| `test_two_column_layout_keeps_a_readable_order` | оба столбца извлечены, порядок уникален и монотонен |
| `test_blank_page_is_kept_and_flagged` | пустая страница сохранена и помечена |
| `test_scanned_page_requires_ocr` | скан: текста нет, `parsing_status=empty`, вердикт `requires_ocr`, страницы `[1]` |
| `test_rotated_page_keeps_geometry` | `rotation=90`, текст сохранён, элементы на странице 1 |
| `test_figure_metadata_and_caption` | фигура с страницей и подписью, OCR не включён |
| `test_mixed_text_and_image_page` | текст и изображение на одной странице, вердикт готов |
| `test_parser_payload_reports_docling_provenance` | provenance адаптера: docling, таблицы включены, OCR выключен |

Используемые assets (проверено в окружении): `docling-project/docling-layout-heron` (layout) и
`docling-project/docling-models` (структура таблиц), кэш `%USERPROFILE%\.cache\huggingface`.
Дополнительные модели не загружались, OCR-веса не скачивались.

Все тесты требуют настоящих assets: при их отсутствии Docling упадёт, и тест упадёт —
автоматического `skip` нет.

---

## 5. Golden Tests

Функция `assert_golden` применяется к каждому разобранному PDF и фиксирует смысл «разобрано
корректно» для этой итерации:

- `parser_name == "docling"`, версия `2.x`, `ocr_enabled is False`, непустой `parser_options_hash`;
- `page_count` документа совпадает с числом страниц независимой инспекции PyMuPDF;
- страницы идут 1..N без пропусков;
- `reading_order` строго возрастает и не повторяется;
- каждый элемент привязан к существующей странице;
- `char_start`/`char_end` присутствуют, и `canonical_text[start:end] == element.text`;
- bbox: единица `pt`, начало координат `top_left`, границы внутри объёма своей страницы
  (допуск 3 pt на округление парсера);
- при наличии текста: статус `parsed`/`parsed_with_fallback`, текст непуст, `block_id` уникальны.

Проверки выполняются на доменной модели и каноническом тексте, а не на внутреннем JSON Docling,
поэтому они не ломаются от нестабильных служебных метаданных библиотеки.

---

## 6. Tables Validation

| Сценарий | Ожидаемая структура | Фактическая структура (реальный Docling) | Результат | Замечания |
|---|---|---|---|---|
| Простая таблица с шапкой | 3 строки × 3 столбца, ячейки непустые | структура извлечена, ячейки содержат «Model/BLEU/Latency», `extraction_status=complete|partial` | PASS | качество ячеек проверено на синтетике |
| Две таблицы на странице | 2 независимых `TableRecord` | 2 записи, обе на странице 1 | PASS | слияние между страницами не выполняется (сохранённое ограничение) |
| Пустая таблица | `row_count=0`, `extraction_status=empty` | конструктор записи; Gate → `failed` (major) | PASS | пустая таблица не выдаётся за извлечённую |
| Частичная таблица | пустые ячейки при `row_count>0` | `extraction_status=partial`; Gate → `warning` | PASS | частичность видна |
| Таблица с длинными значениями | текст ячеек не обрезан | не проверялось отдельной fixture | PARTIAL | ограничение зафиксировано (см. §13) |
| Многострочная шапка | поддержка парсером | отдельной fixture нет | NOT COVERED | ограничение сохраняется |
| Таблица на отдельной странице | `page_number` = номер страницы | проверено на `table_simple`/`table_two` (стр. 1) | PASS | отдельная страница как таковая не выделена |

Проверяется именно `TableRecord` (строки, столбцы, ячейки, страница, `text_representation`,
`extraction_status`), а не только pipe-текст. Границы функциональности: структура извлекается
для таблиц с явными линиями; объединение ячеек и многостраничные таблицы не поддерживаются;
качество на реальных научных публикациях не измерялось — это Evaluation (Итерация 6).

---

## 7. Quality Gate

`tests/parsing/test_quality_gate_scenarios.py` — 16 сценариев, покрывающих таблицу §9 ТЗ:

| Сценарий | Ожидание | Результат |
|---|---|---|
| Корректный цифровой PDF | `passed` | PASS |
| Некритичные проблемы (пустая страница) | `passed_with_warnings` | PASS |
| Скан без текста | `requires_ocr` | PASS (и в Gate, и через реальный Docling, и в workflow) |
| Повреждённый текстовый слой | `needs_human_review`/`failed` | PASS (U+FFFD выше порога) |
| Отсутствие обязательной страницы | `failed` | PASS (critical-проверка `structure_page_count_matches_source`) |
| Некорректный provenance (нет страницы) | warning + `passed_with_warnings` | PASS |
| Некорректный bbox | warning `provenance_bbox_bounds` | PASS |
| Некорректные char offsets (вне текста и не воспроизводят текст) | `failed` major | PASS |
| Пустая таблица | `failed` + диагностика | PASS |
| Частичная таблица | warning | PASS |
| Parsing fallback | `needs_human_review` | PASS |
| Документ без текста | `failed` | PASS |
| TXT с фиктивной пагинацией | контракт отвергает документ | PASS |
| Пороги из Quality Policy | пороги записаны в отчёте | PASS |
| Контролируемая остановка сохраняет артефакты | отчёт и диагностика на месте | PASS |

Дефектов в самом Quality Gate не обнаружено; политика и пороги **не менялись**. Найденный и
исправленный в ходе этой работы дефект касался не Gate, а фиксации сбоя в Run (см. §11).

---

## 8. ty check

Было 14 замечаний, стало **0**. Полная классификация:

| Файл | Строка | Диагностика | Категория | Исправление / обоснование | Результат |
|---|---|---|---|---|---|
| `application/services/document_ingestion.py` | 340 | invalid-return-type | A (реальная) | `fingerprint()` возвращает кортеж, `if not current` было бессмысленной проверкой; убрано, обработка исключений объединена | исправлено |
| `application/services/document_parsing.py` | 372 | invalid-return-type | A | `_canonicalize` возвращал `list` вместо `tuple[ParsedSection, ...]`; аннотация уточнена | исправлено |
| `application/services/quality_gate.py` | 100 | invalid-return-type | A | `to_policy()` возвращал `object`; введён типизированный возврат с `TYPE_CHECKING`-импортом | исправлено |
| `application/services/quality_gate.py` | 803 | missing-attribute | A | `_bbox_outside(bbox: object)` → `bbox: BBox`; проверка `None` вынесена в comprehension | исправлено |
| `application/services/quality_gate.py` | 804–806 | missing-attribute | A | то же | исправлено |
| `domain/documents/parsed.py` | 192 | missing-union-attribute | A | `getattr(...) or record.figure_id` на union → явная ветвь `isinstance(record, TableRecord)` | исправлено |
| `platform/.../pymupdf/pdf_inspector.py` | 115, 120, 126 | missing-attribute | C (стабы) | параметр `page` типизирован как `pymupdf.Page`; лишние `type: ignore` удалены | исправлено |
| `workflows/document_workflow.py` | 255 | too-many-positional-arguments | A | `DocumentPipelineError(code, msg)` → `DocumentIngestionError(code, msg)` | исправлено |
| `workflows/document_workflow.py` | 486 | invalid-argument-type | B (LangGraph) | `StateGraph` не принимает `total=False` TypedDict в границах `StateT`; **один** узкий `cast(Any, ...)` в точке сборки с комментарием, остальной код типизирован | обоснованное исключение |

Итог: `uv run --group parsing ty check` → `All checks passed!`, exit code **0**. Массового
`Any`, отключения модулей и подавления диагностик нет; единственное исключение — одна строка
сборки графа, где сторонняя библиотека сужает тип состояния.

---

## 9. LangGraph Integration

`tests/parsing/test_document_workflow_integration.py` — 9 сценариев через реальный
`bootstrap_application` → `composition` → workflow:

| Сценарий | Результат |
|---|---|
| TXT: полный цикл, 6 артефактов, документные URI, SHA-256 | PASS |
| PDF с реальным Docling: `completed`, `page_count=1`, `parser=docling`, без fallback | PASS |
| Скан: `waiting_for_human`, `requires_ocr`, страницы `[1]`, отчёт и диагностика сохранены | PASS |
| Отсутствующий вход: `source_not_found`, **Run не создаётся** | PASS |
| Неподдерживаемое расширение: `unsupported_format`, Run не создаётся | PASS |
| Сбой Artifact Store: Run помечен `failed`, ошибка с `correlation_id`, нет ложного `completed` | PASS (после исправления дефекта) |
| Повторная обработка: два Run, одинаковый `document_id`, разные URI, прежние артефакты читаемы | PASS |
| Компактность GraphState: < 4 КБ, нет текста/элементов, только ссылки | PASS |
| `agent1a workflow-smoke` не затронут | PASS |

**Найденный дефект:** при сбое узла после создания Run обработчик отказа читал `run_id` из
исходного словаря состояния, который `graph.invoke` не мутирует, поэтому Run не помечался
`failed`. Исправлено: `run_id` пишется в runtime-кэш узлом `initialize_run`, обработчик берёт
его оттуда. Покрыто тестом `test_artifact_store_failure_marks_the_run_failed`.

---

## 10. Regression Testing

Все команды выполнены в `D:\MultiAgentPipeline\research-agent-platform`; окружение: Windows
10 Pro 22H2, CPython 3.13.18, uv 0.13.7, docling 2.150.0, pymupdf 2.1.

| Команда | Exit | Результат |
|---|---|---|
| `uv lock --check` | 0 | lock актуален |
| `uv run --group parsing pytest -q` | 0 | **656 passed**, 0 failed, 0 skipped (~70 s) |
| `uv run --group parsing ruff check .` | 0 | All checks passed |
| `uv run --group parsing ruff format --check .` | 0 | 198 файлов отформатированы |
| `uv run --group parsing ty check` | 0 | All checks passed (было 14) |
| `uv run --group parsing python tools/export_schemas.py --check` | 0 | 10 схем актуальны |
| `uv run --group parsing research-platform --help` | 0 | 5 команд |
| `... config validate` | 0 | конфигурация валидна |
| `... platform check` | 0 | PASS WITH WARNINGS (fake-профиль) |
| `... agent1a workflow-smoke` | 0 | `completed_with_warnings` |
| `... document process --input <pdf> --json` | 0 | `passed`, run создан |
| `... document report --run-id <run>` | 0 | отчёт из артефакта, 12 проверок |

Прирост тестов: 619 → **656** (+37): 12 Docling-интеграционных, 16 сценариев Quality Gate,
9 интеграционных сценариев workflow. Ни один прежний тест не удалён и не ослаблен.

---

## 11. Изменённые файлы

**Созданы:**

| Файл | Назначение |
|---|---|
| `tests/parsing/pdf_fixtures.py` | 11 синтетических PDF + реестр ожидаемых свойств |
| `tests/parsing/test_pdf_docling_integration.py` | 12 тестов реального Docling и golden-инварианты |
| `tests/parsing/test_quality_gate_scenarios.py` | 16 сценариев Quality Gate |
| `tests/parsing/test_document_workflow_integration.py` | 9 интеграционных сценариев workflow |
| `tests/parsing/__init__.py` | пакет тестов parsing |
| `docs/iteration_reports/post_iteration_2_technical_closure.md` | этот отчёт |

**Изменены:**

| Файл | Изменение |
|---|---|
| `application/services/document_ingestion.py` | убрана бессмысленная проверка кортежа в `_verify_source` |
| `application/services/document_parsing.py` | точная аннотация `_canonicalize` |
| `application/services/quality_gate.py` | `bbox: BBox`, ветвь `None` в comprehension |
| `config.py` | `to_policy() -> QualityPolicy` с `TYPE_CHECKING` |
| `domain/documents/parsed.py` | явная ветвь `isinstance` для таблиц/рисунков |
| `platform/document_parsing/pymupdf/pdf_inspector.py` | тип `pymupdf.Page` вместо `object` |
| `workflows/document_workflow.py` | `run_id` в runtime-кэше для фиксации сбоя; `DocumentIngestionError`; узкий `cast` для `StateGraph` |
| `docs/development/testing.md`, `limitations.md`, `document_parsing.md`, `quality_gate.md` | отражены новые тесты, фикстуры и ограничения |

**Удалено:** нет.

---

## 12. Совместимость

| Проверка | Результат |
|---|---|
| Публичные контракты Agent 1A v2.1 / Agent 1B v1.1 | не изменялись |
| Замороженные схемы 2.0 / 1.0 (SHA-256) | не изменялись |
| `export_schemas --check` | 10 схем актуальны (3 документных добавлены в Итерации 2) |
| Artifact Store: неизменяемость | подтверждено тестами повторной обработки (новые id, старые артефакты читаемы) |
| Адресация артефактов | документное пространство `artifact://documents/<doc>/runs/<run>/...` |
| `workflow-smoke` | работает |
| CLI предыдущих итераций | работает (`--help`, `--version`, `config`, `platform`, `contracts`, `document`) |
| Legacy `D:\MultiAgentPipeline\Agent1\Agent1A` | не изменялся |
| ACL / антивирус / `.dsh-acl-recovery` / `.trash-it0` | не трогались |

---

## 13. Оставшиеся ограничения

1. **OCR не реализован.** Движок не выбран (OI-008). Сканы детектируются и останавливаются в
   `requires_ocr`; реального распознавания нет.
2. **Синтез повреждённого текстового слоя невозможен** средставми PyMuPDF (корректная
   `ToUnicode`); сценарий покрыт тестом Gate на сконструированном документе.
3. **Таблицы.** Структура извлекается для таблиц с явными линиями; объединение ячеек,
   многострочные шапки и таблицы между страницами не поддерживаются. Качество на реальных
   научных публикациях не измерялось (Evaluation, Итерация 6).
4. **Выборка fixture мала.** 11 документов покрывают свойства, но не распределение реальных
   вёрсток (сложные колонтитулы, сноски, вложенные таблицы).
5. **`ty check`**: одно документированное исключение (`cast` в сборке LangGraph) из-за
   ограничения типов сторонней библиотеки.
6. **Время тестов**: parsing-набор ~57–70 с из-за реальных прогонов Docling.
7. `low_text`/`image_dominant` в Gate не порождают отдельных проверок; `parser_warnings` пуст —
   задокументировано, не влияет на вердикты.

---

## 14. Готовность к Итерации 3

| Направление | Заключение |
|---|---|
| Document Ingestion | готово: проверки, идентичность, целостность, ошибки покрыты тестами |
| PDF/TXT Parsing | готово для цифровых PDF и TXT; сканы — только детекция |
| Provenance | готово: страница, порядок, bbox, char-диапазоны проверяются golden-инвариантами |
| Quality Gate | готово: все статусы и маршруты подтверждены 16 сценариями |
| Таблицы | частично: структура и диагностика готовы, качество на реальных статьях не измерено |
| LangGraph Runtime | готово: 9 интеграционных сценариев, контролируемые остановки, фиксация сбоев |
| Type Safety | готово: `ty check` = 0, одно обоснованное исключение |
| Model Gateway / LLM | интерфейс, fake-адаптер и конфигурация готовы; реальный адаптер требует решения владельца (OI-001…OI-004: агрегатор, протокол, capabilities, лимиты, фактический model id) и capability-тестов после выбора |
| Checkpointing | только in-memory (`InMemorySaver`, `InMemoryRunRepository`); durable требует OI-006 (политика PostgreSQL) и решения о хранилище checkpoint |
| Прочие зависимости | OI-007 (HITL-канал), OI-008 (OCR), OI-009/010 (embedding/reranker) — на Итерацию 3 не влияют |

Для Итерации 3 владельцу нужно закрыть `OI-001…OI-004` (провайдер и его параметры) и
подтвердить, допустима ли работа на fake-адаптере до выбора провайдера.

---

## 15. Финальное заключение

1. **Полностью закрыты:** покрытие реального Docling (12 тестов), golden-инварианты,
   структурная проверка таблиц, многостраничные/двухколоночные/кириллические/сканированные/
   смешанные/повёрнутые документы, сценарии Quality Gate, интеграционные сценарии workflow,
   все 14 замечаний `ty check`, два найденных дефекта (фиксация сбоя Run; невалидная
   кириллическая фикстура).
2. **Закрыты частично:** качество таблиц (структура — да, качество на реальных статьях — нет),
   повреждённый текстовый слой (Gate-тест вместо PDF-фикстуры), многострочные шапки таблиц.
3. **Остались проблемы:** отсутствие OCR (согласованное ограничение OI-008), малая выборка
   fixture, in-memory checkpointing.
4. **Блокирующих технических проблем нет.**
5. **К Итерации 3 переходить можно** при условии, что владелец закроет OI-001…OI-004 либо
   разрешит разработку на fake-адаптере.
6. **Решения владельца до старта:** провайдер и лимиты модели (OI-001…OI-004); судьба OCR для
   сканов (OI-008) — если в корпусе есть сканы, Итерация 3 упрётся в них; политика
   PostgreSQL/checkpointing (OI-006) — нужна к моменту перехода к устойчивому исполнению.
