# Конфигурация платформы

**Дата:** 2026-11-12 · **Основание:** Итерация 1, ADR-010, ADR-006 · **Код:** `config.py`,
`application/services/configuration_service.py`, `app.py`

Конфигурация типизирована: источник истины — Pydantic-модели в `config.py`, а YAML и переменные
окружения только поставляют значения. Секретов в конфигурации нет — только **имена** переменных
окружения, в которых секрет лежит.

---

## 1. Слои

```text
1. значения по умолчанию в моделях config.py
2. config/application.yaml  →  config/models.yaml   (оба необязательны, глубокий merge)
3. переменные окружения из ENVIRONMENT_OVERRIDES    (пустая строка игнорируется)
```

- Файлы ищутся функцией `discover_settings_paths(root)`: `<root>/config/application.yaml`,
  `<root>/config/models.yaml`. Если файлов нет — действуют значения по умолчанию, и это не
  ошибка.
- `models.yaml` накатывается поверх `application.yaml`: вложенные словари сливаются
  (`_deep_merge`), скалярные значения перекрываются.
- `load_settings(application_path=…, models_path=…, environ=…)` — загрузка из явных путей;
  `load_settings_from_project(root, environ=…)` — из `config/` проекта.
- Ошибки: `SettingsError` — файл не найден (если путь задан явно), YAML не разбирается, корень
  не является mapping; `pydantic.ValidationError` — нарушение схемы.
- `ApplicationBootstrap.load_settings` превращает `ValidationError` в `SettingsError`, собирая
  диагностику из **типа** ошибки и локации, а текст прогоняя через редактор секретов: значение,
  введённое с ошибкой, в сообщение не попадает.

Файлы `config/application.example.yaml` и `config/models.example.yaml` — единственные
документированные образцы; в репозитории присутствуют и рабочие копии `config/application.yaml`
и `config/models.yaml` (комментарий в примере называет их git-ignored, но в `.gitignore` их нет).

## 2. Восемь секций

### `app`

| Поле | Тип | По умолчанию | Смысл |
|---|---|---|---|
| `name` | строка 1…256 | `research-agent-platform` | имя приложения (поле `application` в журнале) |
| `environment` | `local` \| `dev` \| `ci` \| `production` | `local` | среда развёртывания |
| `log_level` | `DEBUG`…`CRITICAL` | `INFO` | порог журнала |

### `paths`

| Поле | По умолчанию | Смысл |
|---|---|---|
| `data_root` | `data` | корень данных от корня проекта |
| `artifact_subdir` | `artifacts` | подкаталог хранилища артефактов |
| `checkpoint_subdir` | `checkpoints` | зарезервирован под durable-чекпоинты (не используется) |
| `log_dir` | `logs` | каталог журналов (проверяется health check, файлы туда не пишутся) |

Все четыре — относительные пути, проверяются валидатором (см. §4).

### `runtime`

| Поле | Тип | По умолчанию | Смысл |
|---|---|---|---|
| `checkpointer` | `in_memory` \| `none` | `in_memory` | какой чекпоинтер собирает bootstrap |
| `max_graph_steps` | 1…1000 | `50` | предел супер-шагов LangGraph (`recursion_limit = max_graph_steps × 4`) |

### `storage`

| Поле | Тип | По умолчанию | Смысл |
|---|---|---|---|
| `artifact_root_uri` | логический URI | `artifact://local/artifacts` | объявленная схема корня артефактов; физический корень локального адаптера берётся из `paths` |
| `database_dsn_reference` | имя переменной окружения | `RESEARCH_PLATFORM_DB_DSN` | **имя** переменной с DSN, не сам DSN |

### `models`

| Поле | Тип | По умолчанию | Смысл |
|---|---|---|---|
| `profiles` | словарь имя → профиль, ≤32 | `{}` | именованные профили моделей |
| `default_profile` | строка \| `null` | `null` | профиль по умолчанию; обязан существовать в `profiles` |

`ModelProfileSettings`:

| Поле | Тип / диапазон | По умолчанию |
|---|---|---|
| `provider` | строка 1…256 | обязательна |
| `model_id` | строка 1…256 | обязательна |
| `purpose` | `extraction` \| `planning` \| `reconciliation` \| `embedding` \| `reranking` \| `verification` | обязательна |
| `api_key_env` | имя переменной окружения \| `null` | `null` |
| `base_url` | `https://…`, ≤512 | `null` |
| `max_context_tokens` | > 0 | `32000` |
| `max_output_tokens` | > 0 | `8000` |
| `supports_structured_output` | bool | `false` |
| `supports_json_schema` | bool | `false` |
| `supports_tool_calls` | bool | `false` |
| `supports_streaming` | bool | `false` |
| `supports_embeddings` | bool | `false` |
| `supports_reranking` | bool | `false` |
| `timeout_seconds` | (0; 3600] | `120` |
| `max_retries` | 0…10 | `2` |
| `system_prompt_reserve_tokens` | ≥ 0 | `2000` |
| `safety_reserve_tokens` | ≥ 0 | `2000` |
| `is_fake` | bool | `false` |
| `notes` | непустая строка \| `null` | `null` |

Свойство `input_token_budget = max_context_tokens − max_output_tokens −
system_prompt_reserve_tokens − safety_reserve_tokens` — сколько токенов остаётся на контекст
документа.

### `workflow`

| Поле | Диапазон | По умолчанию |
|---|---|---|
| `max_retry_parsing` | 0…10 | `1` |
| `max_retry_extraction` | 0…10 | `2` |
| `max_retry_evidence` | 0…10 | `2` |
| `max_retry_validation` | 0…10 | `2` |
| `node_timeout_seconds` | (0; 7200] | `600` |
| `max_quality_gate_reparses` | 0…5 | `1` |

`retry_limits` возвращает словарь с ключами `parsing`, `extraction`, `evidence`, `validation` —
они совпадают с `Agent1AState.retry_counters`, и `check_retry_limits` сверяет фактические
счётчики с этими пределами. Значения — стартовые (ADD §15), подлежат калибровке на evaluation;
в Итерации 1 они не применяются ни одним узлом.

### `privacy`

| Поле | По умолчанию |
|---|---|
| `allow_prompt_content_in_trace` | `false` |
| `allow_document_content_in_trace` | `false` |

Правило: разрешение на выгрузку содержимого документа требует предварительного разрешения на
выгрузку промптов — документ чувствительнее. Флаги валидируются, но ни один код-путь их не
читает: содержимое не логируется вообще.

### `observability`

| Поле | Тип | По умолчанию |
|---|---|---|
| `enabled` | bool | `false` |
| `log_format` | `jsonl` \| `text` | `jsonl` |
| `exporter` | `none` \| `console` \| `otlp` | `none` |
| `service_name` | строка 1…256 | `research-agent-platform` |
| `log_retention_days` | 1…3650 | `30` |

`exporter` и `log_retention_days` только валидируются: экспортёра и ротации в Итерации 1 нет.

## 3. `ENVIRONMENT_OVERRIDES`

| Переменная | Куда пишется |
|---|---|
| `RESEARCH_PLATFORM_ENVIRONMENT` | `app.environment` |
| `RESEARCH_PLATFORM_LOG_LEVEL` | `app.log_level` |
| `RESEARCH_PLATFORM_ARTIFACT_ROOT` | `storage.artifact_root_uri` |
| `RESEARCH_PLATFORM_DB_DSN_REFERENCE` | `storage.database_dsn_reference` |
| `RESEARCH_PLATFORM_DATA_ROOT` | `paths.data_root` |
| `RESEARCH_PLATFORM_LOG_DIR` | `paths.log_dir` |

Все шесть — не секреты по замыслу. Значения приходят строками и приводятся Pydantic к нужному
типу; невалидное значение даёт `ValidationError`. Пустая строка означает «не переопределять».
Переопределения применяются после обоих YAML-файлов, то есть побеждают их.

`docs/contracts/run_and_artifact_contracts.md` §3 перечисляет только четыре переменные; источник
истины — словарь `ENVIRONMENT_OVERRIDES` в `config.py`, где их шесть (в том числе `DATA_ROOT` и
`LOG_DIR`).

## 4. Правила валидации

| Правило | Что отвергается | Где |
|---|---|---|
| пути только относительные | абсолютный путь, буква диска (`C:\…`) | `PathsSettings._paths_are_relative` |
| без обхода каталогов | сегменты `..` и пустые сегменты (`a//b`) | там же |
| DSN — имя переменной | значение с `://` или `@`, несоответствие `^[A-Z][A-Z0-9_]{2,63}$` | `StorageSettings._dsn_is_a_reference_not_a_dsn` |
| `base_url` только https | `http://…`, пробелы, длина > 512 | шаблон поля |
| бюджет токенов | `max_output_tokens ≥ max_context_tokens`; `input_token_budget ≤ 0` | `_token_budget_is_positive` |
| профиль по умолчанию существует | `default_profile` не из `profiles` | `ModelsSettings._default_profile_exists` |
| JSON Schema ⇒ structured output | `supports_json_schema=true` при `supports_structured_output=false` | `_json_schema_implies_structured_output` |
| `purpose` из закрытого списка | произвольная строка | шаблон поля |
| приватность | `allow_document_content_in_trace=true` без разрешения на промпты | `PrivacySettings._document_content_implies_prompt_content` |
| закрытая схема | любое неизвестное поле на любом уровне | `extra="forbid"` в `ContractModel` |
| не более 32 профилей | 33-й профиль | `Field(max_length=32)` |

Формула бюджета дословно: `max_context_tokens − max_output_tokens −
system_prompt_reserve_tokens − safety_reserve_tokens > 0`.

## 5. Политика секретов

- В конфигурации допустимы только **имена** переменных: `api_key_env`, `database_dsn_reference`.
  Значение DSN-подобного вида отвергается с объяснением, что закоммиченный DSN выдаёт пароль.
- `ConfigurationService._scan_for_secrets` обходит распарсенные YAML-деревья обоих файлов и
  ищет словарные ключи, чьё имя содержит маркер секрета, при непустом строковом значении.
  Ключи `api_key_env`, `database_dsn_reference`, `dsn_reference` исключены: они по определению
  хранят имя переменной. Находка формулируется как путь плюс пояснение — **значение не
  печатается**.
- Сканирование выполняется только после успешной валидации и смотрит **только имена ключей**.
  Настоящий литеральный секрет в отдельном поле (`api_key: …`) до сканирования не доходит:
  неизвестное поле отвергает `extra="forbid"`. В тесте
  `test_config_validate_rejects_a_secret_in_yaml` срабатывает именно ошибка валидации; отдельного
  теста на непустой `secret_findings` нет.
- Возможны ложные срабатывания: маркер `token` входит в список, поэтому закавыченное число
  (`max_output_tokens: "4000"`, `system_prompt_reserve_tokens: "2000"`) проходит валидацию
  (Pydantic приводит строку к `int`), но сканирование помечает его как секрет → `ok=false`,
  `config validate` возвращает 2.
- Секрет **в значении** разрешённого строкового поля (например, в `notes`) не обнаруживается ни
  валидатором, ни сканированием: содержимое строк не проверяется.
- Остаточный риск: `base_url` с креденшелами в URL (`https://user:pass@host`) проходит шаблон
  `^https://[^\s]+$`; такой URL маскируется только если попадёт в журнал (редактор заменяет
  `user:pass@` на `***REDACTED***@`).
- Редактирование журнала и диагностики — вторая линия защиты; его правила описаны в
  `observability.md`.

## 6. Fake-профиль и будущий реальный профиль

`config/models.example.yaml` содержит три профиля:

| Профиль | Состояние | Смысл |
|---|---|---|
| `fake-primary` | рабочий | `provider: fake_provider`, `is_fake: true`, structured output + json schema, 32k/4k, `max_retries: 2`; обслуживается fake-адаптером, сети не требует |
| `extraction-primary` | плейсхолдер | `provider`/`model_id` = `UNRESOLVED_CHOOSE_PROVIDER`/`UNRESOLVED_CHOOSE_MODEL`, `api_key_env` указан, лимиты 128k/8k |
| `embedding-primary` | плейсхолдер | то же; `supports_embeddings: true`, 8k/512 |

Ограничения fake-профиля: это **не production-конфигурация**, он не может производить научное
содержимое, и `platform check` показывает по нему `WARNING` с явной пометкой `FAKE`. Реальные
лимиты и capabilities не выдуманы: числа `fake-primary` — контрактные значения, а не измерения;
подтверждение параметров провайдера — открытые решения `OI-001…OI-004`.

## 7. Загрузка в коде

```python
from research_platform.config import load_settings_from_project

settings = load_settings_from_project(project_root, environ=os.environ)
```

`ApplicationBootstrap` делает это сам и передаёт настройки в `ConfigurationService`,
`LocalFileArtifactStore`, `InMemoryRunRepository`, `FakeModelGateway`, `RuntimeFactory` и
`HealthCheckService`. `ConfigurationService` дополнительно умеет `load()` (из `config/` проекта),
`load_from(application_path=…, models_path=…)` и `require_valid()`; результат — `ConfigValidationResult`
с полями `settings`, `issues`, `secret_findings`, `sources` и свойством `ok`.

Загрузка не создаёт каталогов, не подключается к БД и не проверяет доступность провайдера:
это делает `platform check`, и только для локальных каталогов и хранилища.

## 8. Тесты

`tests/unit/test_config.py`, `tests/unit/test_platform_foundation.py`
(`test_settings_defaults_are_valid`, `test_environment_overrides_yaml`,
`test_absolute_data_root_is_rejected`, `test_model_profile_budget_is_validated`,
`test_default_profile_must_exist`, `test_privacy_rules_are_enforced`),
`tests/contract/test_cli_platform.py` (валидный конфиг, неизвестный ключ, секрет в YAML,
JSON-вывод, работа без файлов конфигурации, невалидная конфигурация в `workflow-smoke`).
