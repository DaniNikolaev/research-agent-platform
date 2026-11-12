# Run lifecycle: Run Manager, record and repository

**Дата:** 2026-11-12 · **Основание:** Итерация 1, `docs/decisions/ADR-010-runtime-deployment.md` · **Код:** `application/services/run_manager.py`, `platform/persistence/in_memory_run_repository.py`

Run Manager — единственный компонент, который меняет состояние запуска. Адаптеры только
хранят записи и ничего не знают о переходах; допустимость перехода объявлена доменной
машиной состояний (`ALLOWED_TRANSITIONS`), а не адаптером.

---

## 1. `RunRecord`

| Поле | Тип | Назначение |
|---|---|---|
| `run_id` | `RunId` | идентификатор попытки; уникален, никогда не переиспользуется |
| `document_id` | `Identifier` | документ, к которому относится запуск (не равен `run_id`) |
| `agent_id` | `AgentId` | агент, выполняющий работу |
| `agent_version` | `ShortStr` | версия агента |
| `contract_version` | `ShortStr \| None` | версия контракта пакета, если workflow его создаёт |
| `status` | `RunStatus` | текущее состояние |
| `current_stage` | `RunStage \| None` | этап, на котором находится запуск |
| `created_at` | `UtcDatetime` | время создания (UTC) |
| `started_at` | `UtcDatetime \| None` | время перехода в `running` |
| `completed_at` | `UtcDatetime \| None` | терминальное время (в контракте — `completed_at`; свойство `finished_at` даёт то же значение) |
| `retry_count` | `int` | число повторных попыток внутри запуска |
| `correlation_id` | `ShortStr \| None` | идентификатор для склейки журналов |
| `errors` | `list[RunError]` | нормализованные ошибки |
| `warnings` | `list[NonEmptyStr]` | некритичные замечания |
| `metadata` | `dict[str, str]` | компактные технические пометки (не секреты) |

Запись намеренно маленькая: идентификаторы, жизненный цикл, этап, счётчики и ошибки. PDF,
извлечённый текст, prompts, ответы модели и embeddings здесь не хранятся — для них есть
Artifact Store и логические `artifact://` URI. Валидация отвергает `metadata`, похожую на
секрет.

## 2. Переходы состояний

```text
created ──► running ──► completed
   │            │  ▲     completed_with_warnings
   │            │  │
   │            ▼  │
   │      waiting_for_human
   │            │
   ├────────────┴──────► failed
   └───────────────────► cancelled
```

| Из | Разрешено |
|---|---|
| `created` | `running`, `failed`, `cancelled` |
| `running` | `waiting_for_human`, `completed`, `completed_with_warnings`, `failed`, `cancelled` |
| `waiting_for_human` | `running`, `failed`, `cancelled` |
| `completed`, `completed_with_warnings`, `failed`, `cancelled` | ничего (терминальные) |

`completed` — это «успех» платформы; `completed_with_warnings` отличается только наличием
`warnings`. Из терминального статуса выхода нет: повторная обработка — это новый `run_id`.

## 3. Операции

`start_run`, `get_run`, `list_runs`, `update_stage`, `complete_stage`, `fail_stage`,
`record_error`, `record_retry`, `request_human_review`, `resume_run`, `complete_run`,
`fail_run`, `cancel_run`.

Каждая операция выполняет одни и те же шесть шагов:

1. загрузить запись (и явно упасть, если её нет);
2. проверить допустимость перехода;
3. собрать новую неизменяемую `RunRecord`;
4. сохранить через `RunRepositoryPort`;
5. записать событие в журнал (`run_created`, `run_started`, `stage_started`, …);
6. вернуть новую запись.

`record_retry` увеличивает `retry_count` и не создаёт новый запуск: повтор узла — это
внутренняя попытка, а не пользовательский запуск.

## 4. Идентификаторы

`generate_run_id()` → `run_YYYYMMDD_HHMMSS_<6 hex>` (сортируемо, соответствует `RunId`).
`generate_correlation_id()` → `corr_<12 hex>`. Обе функции принимают `now`/`suffix` для
детерминированных тестов. Новый документ или новая попытка — всегда новый `run_id`.

## 5. `RunRepositoryPort`

| Метод | Контракт |
|---|---|
| `create` | создаёт запись; дубликат `run_id` — ошибка (никогда не перезапись) |
| `get_by_id` | читает запись; отсутствие — `RunRepositoryError` |
| `update` | заменяет запись; отсутствие — ошибка; терминальная запись не может стать нетерминальной |
| `exists` | проверка наличия |
| `list_by_status` | список с фильтром по статусу, порядок по времени создания |
| `close` | освобождение ресурсов |

### InMemoryRunRepository

Для тестов, smoke-запуска и локальной демонстрации. Особенности:

- данные живут только в процессе; после завершения процесса запусков нет;
- `is_durable = False`, `backend_name = "in_memory"` — это не production-хранилище;
- потокобезопасен (`threading.RLock`);
- отдаёт копии записей, поэтому вызывающий код не может случайно изменить сохранённое
  состояние;
- отказывает в дубликате `run_id`, в `update` несуществующего запуска и в попытке
  переписать терминальную запись.

Хранилище на PostgreSQL — отдельный storage-подэтап, он требует решения владельца по
эксплуатации локальной БД (`OI-006`). JSON-хранилище как «временная замена» не создавалось.

## 6. Ошибки

`RunError` требует `code`, `message`, `stage`, `retryable`, `correlation_id`. Валидатор
отвергает сообщение, похожее на секрет. Ошибки классифицируются по источнику: `model_*`
(из Model Gateway), `invalid_state` (нарушение контракта состояния), `unexpected_error`
(всё остальное). Коды не выдумываются на месте: они берутся из нормализованной иерархии
ошибок соответствующего порта.
