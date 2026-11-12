# Наблюдаемость: структурный журнал и редактирование секретов

**Дата:** 2026-11-12 · **Основание:** Итерация 1, ADR-010 · **Код:** `application/ports/logging.py`,
`platform/observability/logging.py`, `application/services/redaction.py`, `app.py`

Журнал — единственный канал наблюдаемости Итерации 1. Он локальный: пишется в поток (по умолчанию
`stderr`), не отправляется ни в какой коллектор, и не содержит ни текста документа, ни промптов,
ни секретов.

Порт определён в `application/ports/logging.py`, реализация — в `platform/observability/logging.py`.
Словарь событий — часть контракта: имя события, которого нет в `EVENT_NAMES`, отвергается
(`ValueError`), чтобы дашборды и тесты не расходились с кодом.

---

## 1. Формат записи

Одна запись — одна строка JSONL (либо одна строка key=value при `log_format: text`).

| Поле | Источник | Примечание |
|---|---|---|
| `timestamp` | реализация | UTC ISO 8601 с `Z`, например `2026-11-12T20:50:00.123456Z` |
| `level` | аргумент `level` (по умолчанию `INFO`) | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`; регистр входа нормализуется |
| `event` | аргумент `event` | только из `EVENT_NAMES` |
| `application` | `service_name` | из `app.name` при bootstrap |
| `run_id` | `bind(...)` | появляется после создания запуска |
| `correlation_id` | `bind(...)` | сквозной идентификатор операции |
| `document_id` | `bind(...)` | когда известен |
| `agent_id` | `bind(...)` | например `1A` |
| `stage` | поле события | `registration`, `validation`, `smoke`, … |
| `error_code` | поле события | нормализованный код ошибки |
| прочие поля | поля события | `status`, `attempt`, `retry_count`, `artifact_id`, `sha256`, `provider`, `model_id`, `input_tokens`, `output_tokens`, `latency_ms`, `fake`, `warnings`, `reason`, `detail`, `retryable`, `exception_type`, `size_bytes`, `media_type`, `schema_version`, `environment`, `profiles`, `checkpointer`, `sources`, `issue_count` |

Правила:

- значения `None` из записи удаляются — отсутствие поля означает «неизвестно/неприменимо»;
- вложенный `bind` не мутирует исходный логгер, а возвращает новый (тест `test_bound_logger_does_not_mutate_the_original`);
- в JSONL ключи сортируются, кодировка UTF-8, `ensure_ascii=False`;
- реализация не добавляет ни одного поля «от себя», кроме перечисленных выше.

## 2. Словарь событий

`EVENT_NAMES` — закрытое множество из 20 имён.

| Событие | Кто пишет | Когда |
|---|---|---|
| `application_started` | `app.py` | контекст собран; поля `environment`, `project_root` |
| `application_shutdown` | `app.py` | `ApplicationContext.close()` (идемпотентно) |
| `config_loaded` | `app.py`, `ConfigurationService` | настройки загружены; `environment`, `profiles`, `checkpointer` / `sources` |
| `config_validation_failed` | `ConfigurationService`, `cli.py` | конфигурация невалидна или найдены секреты |
| `check_completed` | — | объявлено, но в Итерации 1 не используется: health check не логирует |
| `run_created` | `RunManager` | запись создана (`document_id`, `agent_id`, `agent_version`, `contract_version`, `retrieval_mode`) |
| `run_started` | `RunManager` | переход `created → running` |
| `stage_started` | `RunManager.update_stage`, узлы графа | вход на этап |
| `stage_completed` | `RunManager.complete_stage`, узлы графа | этап завершён (`detail` — свободный текст) |
| `stage_failed` | `RunManager.fail_stage`, `record_error`, `_call_model` | ошибка этапа (`error_code`, `retryable`, `attempt`) |
| `model_request_started` | `_call_model` | перед каждой попыткой вызова модели (`provider`, `attempt`) |
| `model_request_completed` | — | зарезервировано для реального адаптера; в Итерации 1 не пишется |
| `fake_model_request_completed` | `_call_model` | успешный вызов fake-модели (`provider`, `model_id`, `input_tokens`, `output_tokens`, `latency_ms`, `fake`) |
| `artifact_created` | `ArtifactService.store_bytes` | артефакт сохранён (`size_bytes`, `media_type`, `sha256`, `schema_version`) |
| `artifact_validation_failed` | `ArtifactService` | конфликт id или расхождение хеша |
| `run_waiting_for_human` | `RunManager.request_human_review` | запуск приостановлен (`reason`, `status`) |
| `run_completed` | `RunManager.complete_run`, узел `complete_run` | терминальный успех (`status`, `stage`, `warnings`) |
| `run_failed` | `RunManager.fail_run`, `cancel_run` | терминальный отказ или отмена |
| `retry_started` | `RunManager.record_retry`, `_call_model` | повтор узла/вызова (`attempt`, `retry_count`, `error_code`) |
| `runtime_error` | раннер графа | ошибка, поднятая узлом (`error_code`, `stage`, `retryable`, `exception_type`) |

Две семантические особенности, о которых нужно знать:

- отмена запуска логируется событием `run_failed` (с `reason` и `status=cancelled`), отдельного
  `run_cancelled` нет;
- `complete_stage` только пишет `stage_completed` и не меняет запись запуска — этап двигает
  `update_stage`.

## 3. Fake-модель и реальная модель: разные события

| Событие | Смысл |
|---|---|
| `fake_model_request_completed` | ответ получен от fake-адаптера; в записи есть `fake: true` |
| `model_request_completed` | ответ получен от реального провайдера (в Итерации 1 не пишется) |

Разделение обязательно: по журналу должно быть видно, что научный результат не производился,
без чтения конфигурации. Различие закреплено тестом
`test_fake_model_event_is_distinct_from_a_real_request`. Узел `execute_smoke_step` сейчас всегда
пишет `fake_model_request_completed`, потому что единственный gateway — fake; при появлении
реального адаптера имя события нужно выбирать по `is_fake`, а не оставлять прежним.

## 4. Уровни, фильтрация и форматы

- Уровни: `DEBUG` (10), `INFO` (20), `WARNING` (30), `ERROR` (40), `CRITICAL` (50);
  порог берётся из `app.log_level` (по умолчанию `INFO`).
- Запись **строится всегда** и возвращается вызывающему коду; в поток она попадает, только если
  её уровень не ниже порога. `event()` возвращает словарь записи в обоих случаях.
- `observability.enabled: false` (значение по умолчанию) отключает вывод. Bootstrap включает
  журнал, если задан `log_stream` **или** `observability.enabled: true`. CLI не передаёт
  `log_stream`, поэтому при настройках по умолчанию журнал в CLI не пишется; тесты передают свой
  поток и видят записи.
- `log_format: jsonl` — машинно-читаемая строка JSON. `log_format: text` — строка
  `<timestamp> <level> <event> k=v k=v …`; `timestamp`, `level`, `event` в пары не дублируются.
  `text` нужен человеку у терминала, не парсеру.
- `close()` только сбрасывает поток; чужой поток не закрывается.

## 5. Редактирование секретов

Политика живёт в `application/services/redaction.py` (чистая логика, общая для приложения и
адаптера журнала); `platform/observability/redaction.py` — совместимый ре-экспорт.

| Что ищется | Регулярное выражение (суть) |
|---|---|
| ключи провайдеров с известным префиксом | `sk-…`, `gsk_…`/`pk-…`/`api-…` |
| заголовок авторизации | `Bearer <токен>` |
| пары `key=value` / `key: value` | имена `api_key`, `secret`, `password`, `passwd`, `token` |
| креденшелы внутри URI/DSN | `scheme://user:password@host` → `scheme://***REDACTED***@host` |
| длинные base64-подобные блоки | 40+ символов из `[A-Za-z0-9+/]`, необязательный `=`-паддинг |

Дополнительно: имя поля, содержащее маркер (`api_key`, `apikey`, `api-key`, `secret`,
`password`, `passwd`, `token`, `authorization`, `credential`, `bearer`, `dsn`,
`private_key`), маскируется целиком независимо от значения. Редактирование рекурсивно:
вложенные словари обходятся, элементы списков обрабатываются по правилам своего типа.
Заполнитель — `***REDACTED***`.

Редактирование — защита в глубину: платформа и так не пускает секреты в контракты
(`RunError`, `RunRecord.metadata`, `ModelRequest.metadata`, `RunMetadata`), поэтому
**срабатывание редактирования означает, что вызывающий код ошибся**. Значение маскируется,
а не распространяется; сообщения об ошибках конфигурации (в том числе от Pydantic) проходят
через редактор, поэтому в диагностике не появляется подставленное значение.

## 6. Токены и стоимость

- `TokenUsage`: `input_tokens` и `output_tokens` обязательны; `total_tokens` и `cost_usd`
  необязательны и по умолчанию `None`. `effective_total` выводится как сумма, если провайдер
  не сообщил итог.
- Реального провайдера нет, поэтому **никакой синтетической статистики не производится**:
  стоимость не выдумывается, агрегатов «сэкономленных токенов», «средней latency» и подобного
  в коде нет.
- Fake-адаптер отдаёт очевидно искусственные числа (см. `fake_adapters.md`), и они попадают в
  журнал как `input_tokens`/`output_tokens` — это метка работоспособности связки, а не измерение.
- `latency_ms` fake-адаптера фиксирован (1.0 мс) и не является замером.

## 7. Границы и ограничения

- `observability.exporter` (`none`/`console`/`otlp`) и `log_retention_days` валидируются, но
  экспортёра нет: журнал не пишет в файл, не ротируется и не отправляется по OTLP.
- `paths.log_dir` используется только проверкой `data_directories` в `platform check`; файлы
  журнала туда не пишутся.
- `check_completed` и `model_request_completed` объявлены, но не используются.
- порог уровня задаётся один раз при создании логгера; изменения конфигурации на лету не
  применяются.
- В журнале нет ни текста документа, ни промптов — не потому, что их редактируют, а потому что
  они никогда не логируются. Флаги `privacy.allow_*_in_trace` валидируются, но ни один код
  путь их не читает.

## 8. Тесты

`tests/unit/test_observability.py`: реализация порта; обязательные поля записи;
слияние bound-контекста и его неизменяемость; отказ неизвестного события; фильтрация по
уровню; отключённый логгер ничего не пишет; текстовый формат; различие fake/real события;
стабильность словаря событий; маскирование значений по шаблонам; маскирование полей с
секретным именем; рекурсивность; маскирование внутри значения поля; отсутствие секрета в
записи; сохранность чистого текста.
