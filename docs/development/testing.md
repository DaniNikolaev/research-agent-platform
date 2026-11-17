# Тестирование: команды, среда и временные файлы

**Дата:** 2026-11-12 · **Основание:** Итерация 1 + техническое закрытие после неё · **Код:** `pyproject.toml`, `tests/`

Тесты — единственное доказательство, что платформа ведёт себя так, как заявлено. Поэтому
документ фиксирует не только «чем запускать», но и **в каких условиях получен результат**:
без этого число «586 passed» ничего не значит.

---

## 1. Стандартная команда

```powershell
uv run pytest -q
```

Именно она выполнялась для результата, приведённого в отчётах: **586 passed, 0 failed,
0 skipped**, exit code `0`. Окружение: Windows 10 Pro 22H2 (`10.0.19045`) x64,
CPython `3.13.18`, `uv 0.13.7`.

Конфигурация pytest живёт в `pyproject.toml`:

```toml
[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
addopts = "-ra --strict-markers --strict-config"
pythonpath = ["src"]
tmp_path_retention_policy = "none"
```

`tmp_path_retention_policy = "none"` удаляет содержимое временных каталогов после каждого
теста: прогон не должен оставлять данные в дереве проекта.

## 2. Полный набор проверок

```powershell
uv lock --check
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run python tools/export_schemas.py --check
uv run research-platform --help
uv run research-platform --version
uv run research-platform config validate
uv run research-platform platform check
uv run research-platform agent1a workflow-smoke
```

`export_schemas --check` — не косметика: он подтверждает, что публичные JSON Schema
совпадают с Pydantic-моделями байт в байт. Если он падает, значит изменился публичный
контракт (в том числе через правку докстринга — Pydantic экспортирует докстринг как
`description`).

## 3. Временные каталоги: как есть на самом деле

Тесты используют `tmp_path`/`tmp_path_factory` и никогда не пишут в корень проекта явно.
Куда pytest кладёт `basetemp`, определяет `tempfile.gettempdir()`.

| Условие | Поведение |
|---|---|
| Обычный PowerShell владельца | `%TEMP%` (`C:\Users\<user>\AppData\Local\Temp`) доступен на запись → временные каталоги вне репозитория |
| Песочница DSH | запись в `%TEMP%` запрещена, поэтому `tempfile.gettempdir()` возвращает текущий каталог → pytest создаёт `pytest-of-<user>/` в корне проекта |

Факты, проверенные в песочнице DSH (2026-11-12):

- `TEMP` = `C:\Users\Danil\AppData\Local\Temp`, но `tempfile.gettempdir()` =
  `D:\MultiAgentPipeline\research-agent-platform` — это документированный fallback Python
  на случай, когда ни один кандидат не доступен на запись;
- `uv run pytest -q` при этом **работает** и возвращает 586 passed / exit 0;
- `pytest-of-Danil/` создаётся, но после прогона пуста (`tmp_path_retention_policy = "none"`),
  исключена в `.gitignore` и удаляется вручную.

Если нужен полностью изолированный временный корень (например, чтобы дерево проекта
оставалось нетронутым), используется **точная** команда, применявшаяся при разработке:

```powershell
uv run pytest -q --basetemp=D:\MultiAgentPipeline\.tooling\pytest-tmp
```

Абсолютный путь в конфигурацию pytest не зашит: `--basetemp` передаётся флагом, потому что
конфигурация не должна зависеть от имени пользователя и раскладки диска.

## 4. Правила для новых тестов

1. Файлы и каталоги — только через `tmp_path` / `tmp_path_factory`; никаких `data/`,
   `logs/` и «текущего каталога» в тестах.
2. Никаких сетевых вызовов, реальных API, реальных PDF и реальной БД. Внешние зависимости
   заменяются fake-адаптерами или тестовыми двойниками.
3. Тест проверяет наблюдаемое поведение, а не факт существования файла: содержимое,
   контрольная сумма, статус, код ошибки.
4. Для критических сценариев хранилища используется настоящая файловая система Windows
   (`tmp_path`), а не mock: атомарность и отсутствие перезаписи проверяются физически.
5. Сбойные сценарии моделируются контролируемо (monkeypatch приватных методов адаптера), без
   зависимости от таймингов и без «спящих» тестов.
6. Если тест печатает что-то в stdout (CLI), это проверяется через `capsys`, а не через
   реальный терминал.

## 5. Маркеры и организация

| Каталог | Что проверяет |
|---|---|
| `tests/unit/` | доменные правила, сервисы, адаптеры, конфигурация, журналирование |
| `tests/contract/` | JSON Schema, версии контрактов, CLI-контракт |
| `tests/integration/` | сквозные сценарии через реальную сборку приложения |

Маркеры объявлены строго (`--strict-markers`): `contract`, `architecture`. Незнакомый маркер
— ошибка конфигурации, а не молчаливое предупреждение.

## 6. Ограничения среды (не скрывать)

- В песочнице DSH дочерние процессы не могут писать в `%TEMP%`; отсюда пустой
  `pytest-of-<user>/` в корне проекта. Это ограничение среды, а не дефект тестов.
- ACL, настройки Windows Security и антивирус для запуска тестов **не менялись**.
- Тесты не проверяют качество парсинга, OCR, retrieval и реального провайдера — этих
  компонентов в Итерации 1 нет (см. `limitations.md`).

## 7. Parsing-тесты (закрытие Итерации 2, 2026-11-16)

Parsing-набор требует группы `parsing` и запускает настоящий Docling:

```powershell
uv run --group parsing pytest tests/parsing -q
```

| Файл | Что проверяет | Runtime |
|---|---|---|
| `test_pdf_docling_integration.py` | 12 тестов реального Docling 2.150.0 + golden-инварианты | ~46 s |
| `test_quality_gate_scenarios.py` | 16 сценариев вердиктов Gate на сконструированных документах | < 1 s |
| `test_document_workflow_integration.py` | 9 сценариев LangGraph workflow (включая сбой Store и повтор) | ~17 s |
| `test_document_pipeline.py` | unit-тесты identity/decoder/ingestion/CLI | < 1 s |

Фикстуры (`tests/parsing/pdf_fixtures.py`) генерируются из кода, бинарных PDF в репозитории
нет. Golden-инварианты: страницы 1..N, уникальный монотонный `reading_order`, привязка к
странице, `canonical_text[start:end] == element.text`, bbox в пределах своей страницы.

Реальные прогоны Docling требуют установленных model assets; при их отсутствии тесты падают, а
не пропускаются. Кириллическая фикстура строится через `insert_htmlbox` (встроенный Helvetica
не кодирует кириллицу — иначе PDF содержал бы `?`).- **Scratch-каталоги сторонних библиотек.** При реальном прогоне Docling torch может создать в
  корне проекта `torchinductor_<user>/` (когда `%TEMP%` недоступен на запись, как в песочнице
  DSH). Каталог исключён в `.gitignore` и удаляется после прогона; к коду проекта он не
  относится. Тестовые артефакты ingestion всегда создаются в `tmp_path`.
## 8. Agent 1A extraction tests (iteration 3)

```powershell
uv run --group parsing pytest tests/agent1a -q                # 63 tests, ~55 s
uv run --group parsing pytest tests/parsing/test_agent1a_e2e_parsing.py -q   # 4 tests, ~28 s (real Docling)
```

| File | Covers |
|---|---|
| `tests/agent1a/test_extraction_unit.py` | group definitions, 27 fields, state validation, payload schema, prompt versions, all eight fake scenarios |
| `tests/agent1a/test_agent1a_workflow.py` | success path, four-group order, artifact publication, immutability, compact state, quality routing (passed/warnings/ocr/review/failed), retries, preservation of Quality Gate warnings |
| `tests/agent1a/test_agent1a_sqlite_resume.py` | SQLite file and LangGraph tables on disk, restore in a new Application, resume without repeating groups, run independence, unusable path, **resume in a separate OS process** |
| `tests/agent1a/test_agent1a_cli.py` | `agent1a extract/report/resume` exit codes and JSON payloads, unchanged document commands |
| `tests/parsing/test_agent1a_e2e_parsing.py` | digital PDF → extraction; scanned PDF → `requires_ocr` with zero model calls; multi-page PDF; TXT |

Fixtures (`tests/agent1a/conftest.py`): a temporary project with `config/application.yaml`
(SQLite checkpointer, `agent_1a` section) and `config/models.yaml` (`fake-extraction` profile),
plus `seed_document_run` which publishes the four document artefacts with a chosen Quality Gate
verdict so every routing branch is testable without Docling.

The fake adapter never touches the network: one test patches `socket.connect` to fail and still
runs a successful extraction.