# Контракт состояния LangGraph `Agent1AState`

**Модуль:** `src/research_platform/agents/agent_1a/contracts/graph_state.py` · **Дата:** 2026-11-11 · **Статус:** контракт; сам граф — Итерация 3

---

## 1. Решения

1. Состояние — `TypedDict`, а не Pydantic-модель: LangGraph вливает частичные
   обновления узлов, и модель сопротивлялась бы частичной записи.
2. Состояние хранит **ссылки, а не данные**: ни байтов PDF, ни полного текста статьи,
   ни массива embeddings. Это сохраняет checkpoints маленькими и делает resume дешёвым
   (ADD §8.4).
3. Ссылки на артефакты хранятся как логические URI `artifact://...` (строки), а не как
   объекты `ArtifactReference`: checkpoint обязан переживать JSON-сериализацию без
   schema-aware десериализации. При этом формат URI проверяется функцией
   `validate_state`, то есть гарантия проверяется, а не предполагается.
4. Счётчики повторов раздельные по классу стадии (parsing, extraction, evidence,
   validation) и ограничены конфигурацией. Один общий счётчик позволил бы одной
   падающей стадии израсходовать бюджет другой.
5. `processing_status` — единственное однозначное значение состояния запуска.
   Пауза для человека выражается значением `waiting_for_human`.

Checkpointing и `interrupt` в Итерации 0 **не реализуются**; форма состояния лишь
оставляет для них место.

---

## 2. Поля

| Группа | Поле | Тип | Смысл |
|---|---|---|---|
| идентичность | `run_id` | str | идентификатор попытки |
| | `document_id` | str | логический документ |
| | `agent_id` | str | `1A` |
| | `agent_version` | str | версия агента |
| источник | `source_ref` | str \| None | URI оригинала |
| | `source_sha256` | str | точная версия содержимого |
| | `source_metadata` | `SourceMetadata` | `file_name`, `media_type`, `language`, `page_count` |
| парсинг | `parse_artifact_ref` | str \| None | URI parsed-документа |
| | `parse_status` | str | `not_started` \| `completed` \| `failed` \| `blocked` |
| | `quality_report_ref` | str \| None | URI отчёта качества |
| retrieval | `retrieval_mode` | str | `full_context` \| `rag` \| `hybrid` \| `not_applicable` |
| | `retrieval_strategy` | str \| None | `dense` \| `lexical` \| `dense_lexical`; обязательна для `rag`/`hybrid` |
| | `chunk_ids` | list[str] | идентификаторы извлечённых фрагментов |
| | `index_version` | str \| None | версия индекса |
| извлечение | `extraction_plan_ref` | str \| None | URI плана извлечения |
| | `field_group_statuses` | dict[str, str] | статус каждой группы полей |
| | `extraction_candidates_ref` | str \| None | URI кандидатов от LLM |
| evidence | `evidence_ledger_ref` | str \| None | URI ledger после resolver |
| валидация | `validation_report_ref` | str \| None | URI отчёта валидации |
| | `validation_errors` | list[str] | коды/сообщения ошибок |
| управление | `retry_counters` | `RetryCounters` | раздельные счётчики |
| | `warnings` | list[str] | предупреждения |
| | `processing_status` | str | см. перечисление ниже |
| handoff | `handoff_package_ref` | str \| None | URI неизменяемого пакета |

`processing_status` ∈ `created`, `registered`, `parsing`, `parsed`, `quality_blocked`,
`extracting`, `validating`, `waiting_for_human`, `completed`,
`completed_with_warnings`, `failed`.

`field_group_statuses` использует `FieldGroupStatus`: `pending`, `in_progress`,
`completed`, `completed_with_warnings`, `failed`, `skipped`.

---

## 3. Начальное состояние

```python
from research_platform.agents.agent_1a.contracts.graph_state import initial_state

state = initial_state(
    run_id="run_20261111_120000_ab12",
    document_id="doc_en_article_01",
    agent_version="0.1.0",
    source_ref="artifact://documents/doc_en_article_01/runs/run_20261111_120000_ab12/original_document/art_original",
    source_sha256="0f1e...e1f0",
    source_metadata={
        "file_name": "en_article.pdf",
        "media_type": "application/pdf",
        "language": "en",
        "page_count": 7,
    },
)
```

Сериализуемый вид (все ключи присутствуют с первого checkpoint):

```json
{
  "agent_id": "1A",
  "agent_version": "0.1.0",
  "chunk_ids": [],
  "document_id": "doc_en_article_01",
  "evidence_ledger_ref": null,
  "extraction_candidates_ref": null,
  "extraction_plan_ref": null,
  "field_group_statuses": {},
  "handoff_package_ref": null,
  "index_version": null,
  "parse_artifact_ref": null,
  "parse_status": "not_started",
  "processing_status": "created",
  "quality_report_ref": null,
  "retrieval_mode": "not_applicable",
  "retrieval_strategy": null,
  "retry_counters": {"evidence": 0, "extraction": 0, "parsing": 0, "validation": 0},
  "run_id": "run_20261111_120000_ab12",
  "source_metadata": {"file_name": "en_article.pdf", "language": "en", "media_type": "application/pdf", "page_count": 7},
  "source_ref": "artifact://documents/doc_en_article_01/runs/run_20261111_120000_ab12/original_document/art_original",
  "source_sha256": "0f1e...e1f0",
  "validation_errors": [],
  "validation_report_ref": null,
  "warnings": []
}
```

---

## 4. Правила и их проверка

`validate_state(state)` проверяет:

1. наличие обязательных ключей (`REQUIRED_STATE_KEYS`);
2. отсутствие запрещённых ключей — подстроки `pdf`, `bytes`, `blob`, `binary`,
   `base64`, `full_text`, `document_text`, `raw_text`, `embeddings`, `vectors`;
3. отсутствие `bytes`/`bytearray` в любом вложенном значении;
4. ограничение на размер встроенной строки (`MAX_INLINE_STATE_STRING = 100 000`):
   полный текст статьи в состояние не помещается;
5. корректность всех URI в полях-ссылках (`ARTIFACT_REFERENCE_FIELDS`);
6. неотрицательность счётчиков;
7. допустимость `processing_status`;
8. JSON-сериализуемость целиком.

`check_retry_limits(counters, WorkflowSettings().retry_limits)` проверяет пределы,
заданные конфигурацией: `max_retry_parsing`, `max_retry_extraction`,
`max_retry_evidence`, `max_retry_validation`.

---

## 5. Требования к будущей реализации графа

1. Маршрутизация, повторы и остановки определяются кодом, а не моделью.
2. Повторное выполнение узла не должно создавать дублирующие package/artifact записи
   (idempotency по `run_id` + типу артефакта).
3. `waiting_for_human` — единственная пауза; способ утверждения quality gate пока
   открыт (ADR-010).
4. Крупные данные — только через Artifact Store, в состоянии — ссылки.
5. Счётчики увеличиваются и проверяются до выполнения следующей попытки.

Тесты: `tests/unit/test_graph_state.py`.
