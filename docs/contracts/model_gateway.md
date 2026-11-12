# Контракт Model Gateway

**Модуль:** `src/research_platform/application/ports/model_gateway/` · **Дом адаптеров:** `src/research_platform/platform/model_gateway/` (пусто) · **Дата:** 2026-11-11

Model Gateway — единственная точка доступа к генеративным, embedding- и reranking-моделям.
Доменные контракты и business-логика не видят SDK провайдера: их видит только адаптер.

---

## 1. Расположение интерфейса (и почему так)

Provider-neutral типы и Protocols размещены в
`application/ports/model_gateway/`, а не в `platform/model_gateway/`. ADD §11 разрешает
оба варианта. Выбран вариант, при котором `platform` (дом адаптеров и, значит, дом
SDK) не может быть импортирован из `application` или `agents`: граница обеспечена
AST-тестом `tests/unit/test_import_boundaries.py`, а не дисциплиной.

---

## 2. Типы

### `ModelRequest`

| Поле | Тип | Смысл |
|---|---|---|
| `request_id` | строка | идентификатор запроса; повтор — новый запрос с новым id |
| `task` | строка | роль задачи, например `extraction.field_group`, `agent_1b.verify` |
| `model_profile` | строка | логический профиль из конфигурации (провайдер/модель резолвит адаптер) |
| `messages` | 1..64 | сообщения чата (`system`/`user`/`assistant`) |
| `output_schema` | `SchemaReference` \| null | `schema_id`, `version`, опционально inline JSON Schema |
| `generation` | `GenerationParameters` | `temperature`, `top_p`, `max_output_tokens`, `stop_sequences`, `seed` |
| `timeout_seconds` | 0–3600 | бюджет времени запроса |
| `metadata` | до 16 пар | служебные метки |

Гарантии:

* **секретов нет** — поля для ключа не существует, а `metadata` отвергает ключи и
  значения, похожие на креденшелы (`api_key`, `secret`, `password`, `authorization`,
  `bearer`, `sk-`, `token=`); любой отказ — ошибка валидации с именем ключа;
* `request.redacted()` возвращает JSON-безопасное представление (счётчики и ключи
  метаданных, без текста сообщений) — для логов;
* задача вида `*.text` не может объявлять `output_schema`.

### `ModelResponse`

`request_id`, `provider_id`, `model_id`, `provider_request_id`, `finish_reason`,
`structured_payload`, `raw_text`, `raw_text_artifact`, `usage`, `latency_ms`, `warnings`.

`finish_reason` ∈ `stop`, `length`, `content_filter`, `tool_calls`, `error`.
Инварианты:

* `finish_reason = length` **обязан** нести хотя бы одно предупреждение: молчаливое
  усечение запрещено (NFR-08);
* ответ обязан нести payload (`structured_payload`, `raw_text` или ссылку на артефакт).

### `TokenUsage`

`input_tokens`, `output_tokens`, `total_tokens` (может отсутствовать),
`cost_usd` (если провайдер сообщает). `total_tokens` не может быть меньше
`max(input, output)`; `effective_total` вычисляется, если провайдер итог не вернул.

### `ModelCapabilities`

`provider_id`, `model_id`, `max_context_tokens`, `max_output_tokens`,
`supports_structured_output`, `supports_json_schema`, `supports_streaming`,
`supports_tool_calls`, `supports_embeddings`, `supports_reranking`, `json_schema_dialect`.

`supports_json_schema = true` требует `supports_structured_output = true`.

---

## 3. Ошибки

| Класс | `code` | retryable | Когда |
|---|---|---|---|
| `ModelAuthError` | `auth_error` | нет | нет ключа, ключ отклонён, нет прав |
| `ModelRateLimitError` | `rate_limited` | да | 429 и аналоги |
| `ModelTimeoutError` | `timeout` | да | превышен бюджет времени |
| `ModelContextOverflowError` | `context_overflow` | нет | вход не помещается в контекст |
| `ModelUnsupportedCapabilityError` | `unsupported_capability` | нет | требуемая возможность не поддерживается |
| `ModelMalformedResponseError` | `malformed_response` | да | ответ не разобран/не прошёл схему |
| `ModelProviderUnavailableError` | `provider_unavailable` | да | транспорт, 5xx |
| `ModelUnknownError` | `unknown` | нет | неклассифицированный сбой |

`normalize_provider_error(exc, provider_id=..., model_id=..., request_id=...)` переводит
исключение провайдера/транспорта в эту иерархию; уже нормализованный `ModelError`
возвращается без изменений. Порядок сопоставления — от частного к общему, поэтому
`json.JSONDecodeError` не «съедается» своим базовым `ValueError`.

Каждый объект ошибки умеет `to_dict()` для логов и run metadata; сообщения ошибок не
содержат секретов (см. также `RunError`, который запрещает их отдельно).

---

## 4. Protocols

| Protocol | Методы |
|---|---|
| `ChatModelPort` | `capabilities`; `generate_text(request)` |
| `StructuredOutputModelPort` (наследует `ChatModelPort`) | `generate_structured(request, schema)` |
| `EmbeddingModelPort` | `capabilities`; `embed(request)` |
| `RerankerPort` (необязательный) | `rerank(request)` |
| `ModelGatewayPort` | `capabilities(profile)`; `generate_text`; `generate_structured`; `embed` |

Все Protocols помечены `@runtime_checkable`, поэтому тест может проверить
`isinstance(fake, StructuredOutputModelPort)`.

`require_structured_output(capabilities)` вызывается **до** запроса: отсутствие
structured output выявляется заранее, а не после получения испорченного текста.

---

## 5. Границы ответственности

| Вопрос | Кто отвечает |
|---|---|
| сколько раз повторять, с каким backoff, когда переключать режим | workflow / application (конфигурация `workflow.*`) |
| как именно вызывать провайдера, как кодировать схему | адаптер |
| какие capabilities у модели | адаптер (сообщает), workflow (проверяет) |
| где хранится секрет | secret reference; адаптер резолвит при вызове |
| что считать успехом ответа | доменная валидация (Pydantic + правила), а не HTTP-код |

Важно: успешное завершение вызова модели **не** означает успешную валидацию
результата (ADD §11.4). Любой ответ проходит Pydantic- и доменную проверку.

---

## 6. Что не реализовано

Реальные адаптеры (DeepSeek, OpenAI, Anthropic, агрегаторы) не реализованы и провайдер
не выбран: это открытое решение заказчика (ADR-005, статус `Deferred`).
`FakeChatModel`, `FakeEmbeddingModel`, `FakeReranker` живут только в
`tests/fixtures/fakes.py` и не входят в поставляемый пакет — чтобы тестовая заглушка не
могла быть принята за готовую функциональность.

Тесты: `tests/unit/test_model_gateway.py`.
