# Контракты run, артефактов и конфигурации

**Модули:** `domain/runs/`, `config.py` · **Дата:** 2026-11-11 (обновлено 2026-11-12, Итерация 1)

---

## 1. Run: идентичность и жизненный цикл

| Правило | Как обеспечено |
|---|---|
| `run_id` уникален для каждой попытки обработки | `RunId` (шаблон), `RunRepositoryPort.create` запрещает переиспользование |
| `document_id` идентифицирует логический источник | `DocumentManifest.document_id` |
| `source_sha256` идентифицирует точную версию содержимого | `Sha256Hex`, обязателен в манифесте и в каждом evidence |
| новая попытка создаёт новый `run_id` и не перезаписывает успешную | `ALLOWED_TRANSITIONS` (у терминальных статусов нет исходящих переходов) + отказ адаптера переписывать терминальную запись |
| повтор/возобновление того же run идемпотентны | `RunManager` проверяет переход до записи; повтор узла увеличивает `retry_count`, а не создаёт новый run |
| статус — конечное перечисление | `RunStatus` |
| `current_stage` отражает этап | `RunStage` (добавлен `smoke` для технической самопроверки) |

`RunStatus` ∈ `created`, `running`, `waiting_for_human`, `completed`,
`completed_with_warnings`, `failed`, `cancelled`. Терминальные статусы обязаны иметь
`completed_at`, нетерминальные — не имеют; `failed` обязан нести хотя бы одну
`RunError`; время монотонно (`created_at ≤ started_at ≤ completed_at`).

**Разрешённые переходы** (Итерация 1, `ALLOWED_TRANSITIONS`):

```text
created            → running | failed | cancelled
running            → waiting_for_human | completed | completed_with_warnings | failed | cancelled
waiting_for_human  → running | failed | cancelled
completed | completed_with_warnings | failed | cancelled → (терминальные, переходов нет)
```

Недопустимый переход — `InvalidRunTransitionError` с указанием текущего и запрошенного
статуса. Подробности: [`docs/development/run_manager.md`](../development/run_manager.md).

**Поля `RunRecord` (Итерация 1):** `run_id`, `document_id`, `agent_id`, `agent_version`,
`contract_version`, `status`, `current_stage`, `created_at`, `started_at`, `completed_at`,
`retry_count`, `correlation_id`, `errors`, `warnings`, `metadata`. Запись не содержит PDF,
полного текста, prompts, embeddings и секретов.

`RunError`: `code`, `message`, `stage`, `retryable`, `correlation_id`. `stage` —
перечисление (`registration`, `parsing`, `quality_gate`, `retrieval`, `extraction`,
`evidence`, `validation`, `export`, `handoff`, `smoke`, `unknown`). Секреты и полный текст
статьи в сообщение не попадают: ключи, похожие на креденшелы, отвергаются валидацией.

`RunMetadata` (секция `run` пакета) фиксирует воспроизводимость: `run_id`, `agent_id`,
`agent_version`, `prompt_version`, `schema_version`, `model_provider`, `model_id`,
`model_configuration_fingerprint`, `parser_name`, `parser_version`, `started_at`,
`completed_at`, `retrieval_mode`, `retrieval_strategy`, `embedding_model_id`, `index_version`.

Инварианты `RunMetadata`:

* все времена — timezone-aware **UTC**; наивное или смещённое время отвергается;
* `completed_at ≥ started_at`;
* `retrieval_mode ∈ {full_context, rag, hybrid, not_applicable}` и
  `retrieval_strategy ∈ {dense, lexical, dense_lexical}` — это **разные** понятия
  (режим передачи контекста и алгоритм поиска, ADR-007);
* `rag` и `hybrid` требуют `embedding_model_id`, `index_version` и `retrieval_strategy`;
  `full_context`/`not_applicable` запрещают все три поля;
* `model_configuration_fingerprint` не может выглядеть как секрет.

---

## 2. Artifact Reference

**Публикуемый** контракт (входит в пакет 2.1) — документное пространство:

```text
artifact://documents/{document_id}/runs/{run_id}/{artifact_type}/{artifact_id}
```

Физический путь/ключ объекта задаёт адаптер Artifact Store и в контракт не входит.
Абсолютные пути Windows, UNC-пути, обратные слеши и `..` отвергаются шаблоном **и**
функцией `parse_artifact_uri` (двойная защита: JSON Schema для внешнего потребителя,
понятная ошибка для разработчика).

`ArtifactReference`: `artifact_id`, `artifact_type`, `uri`, `media_type`, `size_bytes`,
`sha256`, `version`, `created_at`. Согласованность `uri` ↔ `artifact_id` ↔
`artifact_type` проверяется: ссылка не может указывать на другой тип или другой id.

**Дополнительные пространства адресации** (техническое закрытие после Итерации 1)
существуют на уровне хранилища и **не входят** в публикуемый контракт:

```text
artifact://runs/{run_id}/{artifact_type}/{artifact_id}        артефакт запуска
artifact://platform/{artifact_type}/{artifact_id}             артефакт платформы
```

Обоснование: не каждый артефакт платформы принадлежит документу (трассы, отчёты о запуске,
диагностика, справочники). Обязательный `document_id` создавал бы фиктивные идентификаторы.
Пакет Agent 1A содержит только документные артефакты, поэтому публичная схема не расширяется:
`ArtifactReference.uri` по-прежнему принимает лишь документный шаблон, а run-/platform-
артефакты адресуются внутренней `ArtifactMetadata` (поле `uri` — `STORE_ARTIFACT_URI_PATTERN`).
Правило «документ без запуска» отвергается: частичной документной адресации нет.
Подробности — [`docs/development/artifact_store.md`](../development/artifact_store.md).

Типы артефактов (закрытое перечисление): `original_document`, `parsed_document`,
`quality_report`, `retrieval_index_manifest`, `extraction_candidates`,
`evidence_ledger`, `validation_report`, `handoff_package`, `generated_markdown`,
`run_log`, `trace`.

Порты: `ArtifactStorePort` (`put`/`get`/`get_by_id`/`exists`/`exists_id`/`verify_checksum`/
`get_metadata`/`get_metadata_by_id`/`list_by_run`/`list_by_document`/`list_platform`/`scan`/
`check_writable`/`close`), `RunRepositoryPort` (`create`/`get_by_id`/`update`/`exists`/
`list_by_status`/`close`), `DocumentRepositoryPort` (`register`/`get`/`find_by_sha256`).
`DocumentRepositoryPort` пока без реализации — регистрация документов появится вместе с
парсингом (Итерация 2).

---

## 3. Конфигурация

Источники: `config/application.example.yaml` (секции платформы),
`config/models.example.yaml` (профили моделей), `.env.example` (переменные окружения),
несколько операционных env-override.

| Секция | Содержимое |
|---|---|
| `app` | `name`, `environment` (`local`/`dev`/`ci`/`production`), `log_level` |
| `storage` | `artifact_root_uri` (URI-схема, не путь), `database_dsn_reference` (**имя** переменной окружения) |
| `models` | профили: `provider`, `model_id`, `purpose`, `api_key_env`, `base_url`, лимиты контекста/выхода, capabilities, таймаут |
| `workflow` | `max_retry_parsing`/`extraction`/`evidence`/`validation`, `node_timeout_seconds`, `max_quality_gate_reparses` |
| `privacy` | `allow_prompt_content_in_trace`, `allow_document_content_in_trace` |
| `observability` | `enabled`, `exporter` (`none`/`console`/`otlp`), `service_name`, `log_retention_days` |

Правила безопасности:

* секретов в конфигурации нет — только имена переменных (`api_key_env`,
  `database_dsn_reference`); DSN-подобное значение отвергается с объяснением, что
  закоммиченный DSN выдаёт пароль;
* `base_url` — только `https`;
* `privacy`: разрешение на выгрузку содержимого документа требует предварительного
  разрешения на выгрузку промптов (документ чувствительнее);
* примеры содержат только плейсхолдеры (`CHANGE_ME`, `UNRESOLVED_CHOOSE_PROVIDER`).

Операционные override (не секреты): `RESEARCH_PLATFORM_ENVIRONMENT`,
`RESEARCH_PLATFORM_LOG_LEVEL`, `RESEARCH_PLATFORM_ARTIFACT_ROOT`,
`RESEARCH_PLATFORM_DB_DSN_REFERENCE`.

Ретраи: `WorkflowSettings.retry_limits` возвращает словарь с ключами, совпадающими с
`Agent1AState.retry_counters`; `check_retry_limits` сверяет фактические счётчики с
пределами. Значения — стартовые (ADD §15) и подлежат калибровке на evaluation.

Загрузка: `load_settings(application_path=..., models_path=..., environ=...)`;
отсутствующий файл → `SettingsError`, невалидный YAML или не-mapping → `SettingsError`,
нарушение схемы → `pydantic.ValidationError`.

Тесты: `tests/unit/test_run_and_artifact_contracts.py`, `tests/unit/test_config.py`.
