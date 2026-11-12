# LangGraph runtime: технический граф Agent 1A

**Дата:** 2026-11-12 · **Основание:** Итерация 1, ADR-001, ADR-010 · **Код:** `agents/agent_1a/graph/{state,nodes,workflow,factory}.py`,
`composition/runtime_factory.py`, `platform/checkpointing/in_memory.py`

Граф Итерации 1 — линейный технический каркас. Он доказывает, что состояние, зависимости,
журналирование, Run Manager и Artifact Store собраны и работают вместе. Он **не** обрабатывает
документ: ни один узел не читает PDF, не извлекает поля и не формирует карточку.

---

## 1. Форма графа

```text
START → initialize_run → validate_runtime_state → execute_smoke_step → complete_run → END
```

| Узел | Файл | Роль |
|---|---|---|
| `initialize_run` | `graph/nodes.py` | валидирует минимум входов и создаёт запуск |
| `validate_runtime_state` | `graph/nodes.py` | сверяет состояние с контрактом и с сохранённым запуском |
| `execute_smoke_step` | `graph/nodes.py` | один детерминированный технический шаг + трассировочный артефакт |
| `complete_run` | `graph/nodes.py` | завершает запуск и пишет терминальное событие |

Условных рёбер, ветвлений, параллельных ветвей и подграфов нет: маршрутизировать пока нечего.
Форма выбрана так, чтобы Итерация 2+ добавляла узлы парсинга, quality gate, retrieval и
извлечения, не переделывая рантайм (`graph/workflow.py`).

## 2. Узлы: что делает и чего не делает

### `initialize_run`

Делает:

1. пишет `stage_started` для этапа `registration`;
2. требует `document_id` и `source_sha256` (иначе `ValueError`);
3. берёт `run_id` из состояния или генерирует новый (`RunManager.new_run_id()`);
4. берёт `correlation_id` из состояния или генерирует (`corr_<12 hex>`);
5. вызывает `RunManager.start_run(...)` — запись создаётся сразу в статусе `running`,
   `current_stage=registration`, `metadata={"purpose": "smoke", "input_sha256": ...}`,
   `contract_version=None` (пакет не создаётся);
6. пишет `stage_completed` и возвращает частичное обновление состояния:
   `run_id`, `correlation_id`, `processing_status=registered`.

Не делает: не вызывает модель, не пишет артефакты, не читает документ.

### `validate_runtime_state`

Делает:

1. `stage_started` для этапа `validation`;
2. `validate_state(state)` — контракт `Agent1AState`: обязательные ключи, запрет
   bulk-полей (`pdf`, `bytes`, `full_text`, `embeddings`, …), разбор всех
   `artifact://`-ссылок, неотрицательные счётчики ретраев, допустимый
   `processing_status`, согласованность `retrieval_mode`/`retrieval_strategy`,
   JSON-сериализуемость, предел inline-строки 100 000 символов;
3. проверяет, что запуск существует и его `document_id` совпадает с состоянием;
4. если у записи есть `correlation_id`, требует совпадения с состоянием;
5. переводит запуск на этап `validation` (`update_stage`);
6. пишет `stage_completed`.

Не делает: не вызывает модель, не пишет артефакты. Это проверка согласованности
«состояние ↔ зависимости ↔ сохранённый запуск», а не научная валидация.

### `execute_smoke_step`

Делает:

1. `stage_started` для этапа `smoke`;
2. копирует `warnings` из состояния;
3. если gateway не сконфигурирован или нет ни одного профиля — добавляет предупреждение и
   **не вызывает модель**;
4. иначе выполняет один вызов модели с ограниченными повторами (см. §5) и переводит запуск
   на этап `smoke`;
5. формирует детерминированный отчёт `{smoke: true, run_id, document_id, source_sha256,
   model_call, note}` с обязательной строкой
   `SMOKE TEST ONLY - NO SCIENTIFIC DOCUMENT WAS PROCESSED`;
6. сохраняет отчёт как артефакт `trace` (`application/json`, `schema_version=1.0`,
   `artifact_id = art_smoke_<run_id>`), проверяет, что полученный URI разбирается;
7. пишет `stage_completed` с `artifact_id`, `sha256`, `model_invoked`;
8. возвращает `technical_trace_ref`, `warnings`, `processing_status=registered`.

Не делает: не формирует ни одного поля карточки, не создаёт evidence, не выносит научных
суждений, не помещает в состояние текст документа. `model_call` — технические метаданные
вызова (provider, model_id, task, schema_id, attempts), а не ответ модели.

Замечание: при работе без модели этап остаётся `validation` — переход на `smoke`
происходит только в ветке с вызовом модели.

### `complete_run`

Делает: вызывает `RunManager.complete_run(run_id, warnings=...)` — статус становится
`completed` либо `completed_with_warnings` (если предупреждения есть), пишет
`run_completed` с этапом `smoke` и `technical_trace_ref`, возвращает
`processing_status=completed`.

Не делает: не «дополняет» запуск задним числом, не переводит упавший запуск в успех.

Так как `build_smoke_state` кладёт в состояние предупреждение
`SMOKE TEST ONLY - no scientific document was processed`, обычный результат smoke-запуска —
`completed_with_warnings`. `completed` возможен, если предупреждений нет (например, состояние
собрано вручную без этой строки). CLI принимает оба статуса.

## 3. Состояние: переиспользование `Agent1AState`

`graph/state.py` не вводит второй контракт: он импортирует `Agent1AState`,
`SourceMetadata`, `initial_state`, `new_retry_counters`, `validate_state` из
`agents/agent_1a/contracts/graph_state.py`. Добавлены только модель запроса
`SmokeRunRequest` и сборщик синтетического состояния `build_smoke_state`.

В Итерации 1 в контракт состояния добавлены два поля:

| Поле | Тип | Зачем |
|---|---|---|
| `correlation_id` | `str` (обязательный ключ) | сквозная склейка журналов запуска; проверяется на совпадение с сохранённым запуском |
| `technical_trace_ref` | `str \| None` (поле ссылки на артефакт) | логический `artifact://` URI технической трассы; разбирается `validate_state` |

Синтетический вход: `SMOKE_INPUT_BYTES = b"SMOKE TEST INPUT - no scientific document\n"`,
`SMOKE_INPUT_SHA256` — его реальный SHA-256, `document_id` по умолчанию `doc_smoke_0001`.
Это настоящая последовательность байт с настоящим хешем: запуск описывает то, что существует,
но не научный документ.

Состояние остаётся компактным и JSON-сериализуемым: только идентификаторы, ссылки и счётчики.
`run_id` генерируется **до** старта графа, потому что `thread_id` чекпоинтера выводится из него.

## 4. Внедрение зависимостей

```text
ApplicationBootstrap
   └── RuntimeFactory(settings, run_manager, artifact_service, logger, model_gateway, checkpoint_provider)
          ├── dependencies(agent_version) → GraphDependencies
          └── build_agent_1a() → create_agent_1a_graph(deps, checkpoint_provider) → Agent1ASmokeGraph
```

`GraphDependencies` (frozen dataclass): `run_manager`, `artifact_service`, `logger`,
`settings`, `model_gateway` (может быть `None`), `clock`. Свойство `contract_version`
возвращает `None`: smoke-workflow не создаёт handoff-пакет, и запуск честно не записывает
версию контракта.

Узел — чистая функция `(state, deps) -> partial update`. Узел не создаёт соединений, не читает
файлы по пути и не ищет глобальные объекты; это закреплено тестом
`test_no_global_service_registry_is_used`. Обёртки `_initialize/_validate/_smoke/_complete` в
`workflow.py` лишь замыкают `dependencies` при компиляции графа.

## 5. Отказ и повтор

### Политика отказа

- Узел бросает исключение → LangGraph останавливает выполнение.
- Раннер `Agent1ASmokeGraph.run` перехватывает его, вызывает `_record_failure` и
  **пробрасывает исключение дальше**. Успех после критической ошибки не объявляется.
- `_record_failure` нормализует ошибку в `RunError` (этап всегда `smoke`):

| Исключение | `code` | `retryable` |
|---|---|---|
| `ModelError` | собственный код (`timeout`, `rate_limited`, `malformed_response`, `auth_error`, `unsupported_capability`, …) | из исключения |
| `ValueError` (нарушение состояния/контракта) | `invalid_state` | `false` |
| любое другое | `unexpected_error` | `false` |

- `message` обрезается до 500 символов. Затем вызывается `RunManager.fail_run(run_id, error)` и
  пишется событие `runtime_error`. Если запуск ещё не создан (падение до `initialize_run`) или
  уже терминален, `fail_run` не сработает — тогда пишется `runtime_error` с
  `detail="run could not be marked failed"`, а исходное исключение всё равно пробрасывается.
- Сохранённое состояние может остаться частичным: чекпоинтер фиксирует то, что успело
  выполниться. Повторный запуск — это новый `run_id`, а не «продолжение» упавшего.

### Ограниченные повторы

- Попыток на вызов модели: `attempts = profile.max_retries + 1` (значение профиля из
  `settings.models.profiles[profile_name]`).
- Перед каждой попыткой пишется `model_request_started` с `attempt`.
- Повторяемая ошибка (`ModelError.retryable=True`) и не последняя попытка → `record_retry`
  (увеличивает `RunRecord.retry_count`) + событие `retry_started`, затем следующая попытка.
- Неповторяемая ошибка или последняя попытка → `stage_failed` с `error_code` и `attempt`,
  исключение уходит наверх.
- Успех → `fake_model_request_completed` (единственный gateway сегодня — fake; поле
  `fake` отражает `is_fake`).

`ModelRequest` строится один раз до цикла, поэтому у всех попыток одинаковый `request_id`
(`req_smoke_<run_id>`). Это расходится с формулировкой `application/ports/model_gateway/protocols.py`
(«retry — это новый запрос с новым `request_id`»); практического эффекта в Итерации 1 нет, но
при появлении реального адаптера идентификатор запроса нужно будет различать по попыткам.

## 6. Предел рекурсии и изоляция запусков

- `config["recursion_limit"] = settings.runtime.max_graph_steps * 4`. `max_graph_steps`
  (по умолчанию 50, диапазон 1…1000) — верхняя граница супер-шагов, защита от случайного
  цикла; множитель 4 совпадает с числом узлов линейного графа.
- `thread_id` выводится из `run_id` (`InMemoryCheckpointProvider.thread_id_for_run` →
  `thread_<run_id>`; без провайдера — та же схема локальной лямбдой). Отображение
  один-к-одному: два запуска не делят пространство чекпоинтов
  (`test_checkpoints_are_isolated_per_run`).
- `state_for_run(run_id)` читает последнее сохранённое состояние; без чекпоинтера возвращает
  `None`.

## 7. Что в графе не реализовано

| Отсутствует | Где появится |
|---|---|
| парсинг PDF/TXT, OCR | Итерация 2 |
| quality gate и повторный парсинг | Итерация 2–3 |
| chunking, embeddings, retrieval, reranking | Итерация 4 |
| извлечение полей карточки | Итерация 3 |
| Evidence Resolver и ledger | Итерация 4 |
| экспорт пакета для Agent 1B | Итерация 5 |
| условные переходы, ветвления, interrupts, human-in-the-loop | по мере появления узлов; канал HITL — `OI-007` |
| durable checkpointing, resume между процессами | storage-подэтап, `OI-006` |
| проверка бюджета токенов и валидация ответа модели по JSON Schema | вместе с реальным адаптером |

## 8. Тесты

| Файл | Покрытие |
|---|---|
| `tests/unit/test_runtime_graph.py` (19 тестов) | компиляция 4 узлов; работа без чекпоинтера и без модели; завершение и трасса; компактность состояния; `run_id`/`correlation_id` в логах; метка fake; неповторяемая ошибка валит запуск; повторяемая — ретраится и завершается; отсутствие capability валит быстро; упавший запуск не помечается завершённым; невалидное состояние отвергается до вызова модели; изоляция двух запусков и чекпоинтов; `thread_id` из `run_id`; отсутствие глобального реестра; новая фабрика — новый граф |
| `tests/integration/test_scenarios.py` | сценарий A (успешный smoke), B (невалидная конфигурация до графа), C (ошибка узла нормализуется и запуск падает), D (конфликт артефактов не перезаписывает оригинал), E (запуски не делят состояние), F (запуск без модели и health без провайдера) |
