# CLI: единственная локальная точка входа

**Дата:** 2026-11-12 · **Основание:** Итерация 1, ADR-010 · **Код:** `cli.py`, `app.py`

`research-platform` — тонкий слой: разобрать аргументы, вызвать сервис, отобразить результат и
вернуть документированный код. Бизнес-логики в CLI нет: жизненный цикл запуска принадлежит
`RunManager`, проверки — `HealthCheckService`, валидация контрактов — Pydantic-моделям.
Запуск процесса: `research-platform = research_platform.cli:main` (`pyproject.toml`).

---

## 1. Команды

| Команда | Что делает | Коды возврата |
|---|---|---|
| `research-platform --help` | печатает список команд | 0 |
| `research-platform --version` | имя, версия пакета, Python, ключевые пакеты | 0 |
| `contracts validate --input <path>` | валидирует JSON-пакет Agent 1A по `contract_version` (2.0 или 2.1) | 0 — валиден; 1 — нарушение/нечитаемый файл/неизвестная версия |
| `config validate` | загружает YAML, применяет env-override, проверяет типы, значения, пути и секреты | 0 — валидна; 2 — ошибка конфигурации |
| `platform check` | само-проверки платформы (8 проверок) | 0 — нет FAILED; 2 — ошибка сборки контекста; 3 — провал проверок |
| `agent1a workflow-smoke` | один прогон технического графа Agent 1A | 0 — `completed`/`completed_with_warnings`; 1 — исключение или иной статус; 2 — ошибка конфигурации |

Примеры:

```powershell
uv run research-platform --version
uv run research-platform --help
uv run research-platform contracts validate --input tests\fixtures\agent_1a_package_v2_1_valid.json
uv run research-platform config validate
uv run research-platform platform check
uv run research-platform platform check --json
uv run research-platform agent1a workflow-smoke --document-id doc_smoke_0001
uv run research-platform agent1a workflow-smoke --no-model
uv run research-platform --project-root D:\work\project config validate --json
```

Отсутствующая подкоманда — ошибка, а не «сделать по умолчанию»:

```text
research-platform config        → error: 'config' requires a subcommand; run: research-platform config validate  (код 1)
research-platform platform      → error: 'platform' requires a subcommand; run: research-platform platform check (код 1)
research-platform agent1a       → error: 'agent1a' requires a subcommand; run: research-platform agent1a workflow-smoke (код 1)
```

Запуск без аргументов печатает справку и возвращает 0.

## 2. Коды возврата

| Код | Константа | Значение |
|---|---|---|
| 0 | `EXIT_OK` | операция успешна |
| 1 | `EXIT_CONTRACT_VIOLATION` / `EXIT_INPUT_ERROR` | нарушение контракта или нечитаемый/невалидный вход |
| 2 | `EXIT_CONFIG_ERROR` | ошибка конфигурации |
| 3 | `EXIT_INFRASTRUCTURE_ERROR` | инфраструктура недоступна или не инициализирована |
| 4 | `EXIT_CONTROLLED_STOP` | управляемая остановка / ожидание внешнего действия |

`EXIT_INPUT_ERROR` — псевдоним `EXIT_CONTRACT_VIOLATION` (`1`): в Итерации 0 нечитаемый вход
имел отдельный код 2, который теперь занят ошибкой конфигурации, поэтому два смысла разведены.

Код 4 объявлен и документирован, но **ни одна команда его не возвращает**: управляемой
остановки (human-in-the-loop) в Итерации 1 нет — канал ещё не выбран (`OI-007`).

Синтаксические ошибки аргументов (неизвестная команда, отсутствующий обязательный `--input`)
обрабатывает `argparse`: usage-сообщение и завершение с кодом 2. CLI этот код не переопределяет,
поэтому цифра 2 в таком случае приходит от `argparse`, а не от платформы.

## 3. Флаги `--project-root` и `--json`

- `--project-root PATH` — корень проекта, относительно которого ищутся `config/` и `data/`.
  По умолчанию — текущий каталог (`Path.cwd()`), путь разрешается (`resolve()`).
- `--json` — машинно-читаемый вывод вместо текста.

Оба флага объявлены и на корневом парсере, и на каждой подкоманде (с `default=argparse.SUPPRESS`),
поэтому принимаются **до и после** подкоманды: `research-platform --json config validate` и
`research-platform config validate --json` эквивалентны.

Исключение: `contracts validate` принимает `--json`, но не использует его — вывод остаётся
текстовым (валидатор пакета JSON-режима не имеет).

## 4. Текстовый вывод

| Команда | Формат |
|---|---|
| `--version` | `research-platform 0.1.0`, строка Python, затем `langgraph`, `langchain-core`, `pydantic` |
| `config validate` | `config: <path>` на каждый источник, `issue: …`, `secret: …`, затем `valid: environment=…, profiles=…` при успехе |
| `platform check` | заголовок `platform check (<root>)` и строки `{name:<24} {status:<15} {detail}`; итог `result: PASS` / `PASS WITH WARNINGS` / `FAILED - fix the failed checks above` |
| `agent1a workflow-smoke` | блок `run_id`, `correlation_id`, `status`, `trace artifact`, предупреждения, затем пустая строка и обязательная строка `SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED` |

Статусы проверок `platform check`: `PASS`, `WARNING`, `FAILED`, `NOT_CONFIGURED`. Восемь проверок:
`python_version`, `package_version`, `configuration`, `data_directories`, `artifact_store`,
`model_gateway`, `langgraph_runtime`, `imports`. Fake-профиль даёт `WARNING`; отсутствие
конфигурации, профилей или gateway — `NOT_CONFIGURED` (это не провал); `FAILED` дают каталог,
который нельзя создать, нерабочее хранилище, исключение при сборке графа и неимпортируемый
модуль.

Провал любой проверки возвращает код 3. Ветка «провалилась только проверка `configuration`»
(код 2) в коде есть, но сама проверка `configuration` возвращает только `PASS` или
`NOT_CONFIGURED`, поэтому на практике ошибка конфигурации приходит кодом 2 из сборки контекста,
то есть до запуска проверок.

## 5. Машинно-читаемые полезные нагрузки (`--json`)

### `--version`

```json
{
  "implementation": "CPython",
  "jsonschema": "4.26.0",
  "langchain-core": "1.8.0",
  "langgraph": "1.2.20",
  "name": "research-agent-platform",
  "platform": "Windows-10-10.0.19045-SP0",
  "pydantic": "2.14.3",
  "python": "3.13.18",
  "version": "0.1.0"
}
```

Пакеты, которых нет в окружении (например, `jsonschema` без группы `dev`), получают значение
`not installed`; текстовая форма печатает только `langgraph`, `langchain-core` и `pydantic`.

### `config validate`

```json
{
  "environment": "local",
  "issues": [],
  "ok": true,
  "profiles": ["embedding-primary", "extraction-primary", "fake-primary"],
  "secret_findings": [],
  "sources": ["...\\config\\application.yaml", "...\\config\\models.yaml"]
}
```

`issues` — уже отрендеренные строки `путь: сообщение`; `environment` равен `null`, если
настройки не загрузились.

### `platform check`

```json
{
  "ok": true,
  "checks": [
    {"name": "python_version", "status": "PASS", "detail": "3.13.18"},
    {"name": "model_gateway", "status": "WARNING", "detail": "profile 'fake-primary' is a FAKE adapter (fake_provider/fake_model_1); no real provider"}
  ]
}
```

Показаны две проверки из восьми; порядок в массиве — порядок выполнения проверок.

### `agent1a workflow-smoke`

```json
{
  "correlation_id": "corr_0123456789ab",
  "note": "SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED",
  "run_id": "run_20261112_205000_a1b2c3",
  "smoke": true,
  "status": "completed_with_warnings",
  "technical_trace_ref": "artifact://documents/doc_smoke_0001/runs/run_20261112_205000_a1b2c3/trace/art_smoke_run_20261112_205000_a1b2c3",
  "warnings": ["SMOKE TEST ONLY - no scientific document was processed"]
}
```

Ключа `card` в нагрузке нет и не будет, пока нет извлечения: smoke-прогон не производит карточку.

## 6. Чего CLI не делает

| Не делает | Почему |
|---|---|
| не подключается к PostgreSQL | хранилища метаданных в Итерации 1 нет (`OI-006`) |
| не обращается к LLM-провайдеру | реальный адаптер отложен (`OI-001…OI-004`); работает только fake |
| не читает и не парсит научные документы | парсинга нет; smoke использует синтетические байты |
| не запускает веб-сервер и не открывает порт | FastAPI/UI — не эта итерация |
| не требует Docker | Windows-native, ADR-010 |
| не создаёт запуск в `platform check` | health check проверяет готовность, а не имитирует работу |

Побочные эффекты, которые нужно знать:

- `platform check` создаёт каталоги `data/` и `logs/`, пишет и удаляет пробные файлы,
  собирает граф и импортирует обязательные модули; запусков и артефактов не появляется.
- `agent1a workflow-smoke` создаёт запуск в памяти процесса и артефакт
  `data/artifacts/...` (`metadata/<id>.json` + объект) — это единственная команда, которая
  пишет артефакты на диск.
- по умолчанию CLI **не пишет JSONL-журнал**: `observability.enabled: false`, а `log_stream` из
  CLI не передаётся. Чтобы получить записи, включите `observability.enabled: true` в конфигурации.
- `config validate` только читает YAML и переменные окружения.

## 7. Тесты

| Файл | Покрытие |
|---|---|
| `tests/contract/test_cli.py` | справка, версия, коды возврата 0/1/2/3/4 объявлены и стабильны, `EXIT_INPUT_ERROR == EXIT_CONTRACT_VIOLATION`, валидация пакетов 2.0/2.1, отсутствие файла/битый JSON, отсутствующая подкоманда |
| `tests/contract/test_cli_platform.py` | `config validate` (валидно, неизвестный ключ, секрет в YAML, `--json`, без файлов, отсутствие подкоманды); `platform check` (PASS + WARNING на fake, `--json` со всеми проверками, отсутствие подкоманды); `workflow-smoke` (обязательная строка, `--json`, `--no-model`, артефакт под проектом, невалидная конфигурация без утечки значения) |
