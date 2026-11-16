# Контракт `SourceDocumentManifest` v1.0 (манифест приёма документа)

**Модуль:** `domain/documents/models.py` · **Схема:** `contracts/documents/v1/source_manifest.schema.json` ·
**Версия контракта:** `1.0` (`SOURCE_MANIFEST_CONTRACT_VERSION`) · **Итерация:** 2

Манифест приёма фиксирует, **что именно было принято** на вход конвейера: содержимое и его хеш,
тип источника, размер, кодировку (только для TXT), язык и статус обработки на момент приёма.
Этот контракт **дополнительный**: опубликованный `DocumentManifest` пакета Agent 1A
(контракт пакета 2.1) не менялся — см. §4.

---

## 1. Назначение и границы

| Вопрос | Ответ манифеста |
|---|---|
| какой документ принят | `document_id` (выводится из байтов, §2.1) |
| какие именно байты | `source_sha256`, `source_size_bytes` |
| где лежит неизменяемый оригинал | `source_artifact_uri` (логический `artifact://` URI) |
| в каком формате пришёл файл | `source_type`, `source_media_type` |
| как файл декодирован (TXT) | `original_encoding`, `encoding_uncertain` |
| на каком языке текст | `language`, `language_detection_method` |
| кто и когда принял | `run_id`, `created_at` |
| на каком шаге конвейера документ | `processing_status` |

Манифест **не** содержит:

* количество страниц — оно появляется только в `ParsedDocument` (после инспекции PDF);
* текст, блоки, таблицы, координаты — это `ParsedDocument`;
* физические пути — только «голое» имя файла и логические URI;
* результат контроля качества — это `QualityReport`.

## 2. Поля

| Поле | Тип | Обяз. | Смысл и ограничения |
|---|---|---|---|
| `document_contract_version` | строка `^\d+\.\d+$` | да (по умолчанию `"1.0"`) | версия этого контракта |
| `document_id` | `Identifier` (`[a-z0-9][a-z0-9_\-]{2,63}`) | да | стабильный id содержимого: `doc_` + 24 hex (§2.1) |
| `run_id` | `ShortStr` | да | запуск, в котором документ принят (`run_YYYYMMDD_HHMMSS_<6 hex>`) |
| `source_file_name` | непустая строка | да | «голое» имя файла без пути |
| `source_type` | `pdf` \| `txt` | да | тип источника (расширение после allow-list) |
| `source_media_type` | `application/pdf` \| `text/plain` | да | MIME, согласованный с `source_type` |
| `source_sha256` | 64 hex | да | SHA-256 принятых байтов |
| `source_size_bytes` | целое `> 0` | да | размер принятых байтов |
| `source_artifact_uri` | `ArtifactUri` | да | документное пространство, тип `original_document`, тот же `document_id` и `run_id` |
| `original_encoding` | `ShortStr` \| `null` | условно | только для TXT: имя кодека, которым текст декодирован |
| `encoding_uncertain` | `bool` (по умолчанию `false`) | да | `true`, если строгое декодирование не удалось и применена замена символов |
| `language` | `ru` \| `en` \| `unknown` | да (по умолчанию `unknown`) | доминирующий алфавит текста, не научная гипотеза |
| `language_detection_method` | `not_detected` \| `character_ratio` \| `bom_and_ratio` | да (по умолчанию `not_detected`) | каким правилом получен `language` |
| `created_at` | UTC datetime | да | момент формирования манифеста |
| `processing_status` | `ingested` \| `parsing` \| `parsed` \| `quality_checked` \| `requires_ocr` \| `needs_human_review` \| `failed` | да (по умолчанию `ingested`) | шаг конвейера на момент записи |

### 2.1. Вывод `document_id`

`document_id_for(source_sha256, source_type)` = `"doc_"` + первые 24 hex от
`SHA-256("research-agent-platform/document/v1|<тип>|<sha256>")`.

Свойства, на которые опирается весь конвейер:

* идемпотентность: те же байты и тот же тип всегда дают тот же id, независимо от имени файла и
  каталога;
* доменное разделение: id — не префикс файлового хеша, а отдельная величина, поэтому смена схемы
  версионируется доменной строкой;
* тип входит в вывод: PDF и TXT с идентичными байтами (синтетический случай) не совпадут;
* проверка обратимости: `DocumentIdentity` отвергает id, не равный выведенному из `source_sha256`
  и `source_type`.

## 3. Инварианты (проверяются валидаторами модели)

1. **`source_file_name` — «голое» имя.** Символы `/`, `\`, `:` запрещены: путь — деталь хранения и
   не должен попадать в публикуемый контракт.
2. **`source_media_type` соответствует `source_type`:** `pdf` ↔ `application/pdf`,
   `txt` ↔ `text/plain`.
3. **Кодировка объявляется только для TXT.**
   * для `txt`: либо `original_encoding` задан, либо `encoding_uncertain=true` (иначе нечем
     объяснить, как получен текст);
   * для `pdf`: `original_encoding` обязан быть `null`, а `encoding_uncertain` — `false`.
4. **Артефакт принадлежит документу и запуску.** `source_artifact_uri` обязан:
   * иметь тип `original_document`;
   * содержать тот же `document_id`;
   * содержать тот же `run_id`.
5. **Манифест неизменяем.** После записи артефакт не перезаписывается: повтор с другим
   содержимым под тем же `artifact_id` отвергается хранилищем (`ArtifactConflictError`).

## 4. Связь с опубликованным `DocumentManifest` (пакет 2.1)

`DocumentManifest` — часть **опубликованного** и неизменяемого пакета Agent 1A (контракт 2.1).
В Итерации 2 он **не изменялся**. Два типа сосуществуют и описывают разные представления:

| Аспект | `DocumentManifest` (пакет 2.1) | `SourceDocumentManifest` (приём, 1.0) |
|---|---|---|
| Кому принадлежит | Agent 1A → Agent 1B, публикуемый контракт | внутренний контракт конвейера документов |
| Версия | пакет 2.1 (заморожен) | `document_contract_version = "1.0"` |
| Страницы | `page_count` (для TXT обязан быть `null`) | поля нет вовсе |
| Язык | только `language` | `language` + `language_detection_method` |
| Кодировка | нет | `original_encoding`, `encoding_uncertain` |
| Размер и статус | нет | `source_size_bytes`, `processing_status` |
| Принадлежность артефакта | документ | документ **и** запуск |
| Проверка `run_id` в URI | нет | да |

Правило: **новые поля не переносятся в пакет 2.1 задним числом.** Если публикуемому контракту
когда-нибудь понадобится кодировка или статус, это будет новая версия пакета и новое ADR, а не
расширение опубликованной схемы.

Артефакт манифеста хранится с типом `handoff_package`, `media_type = "application/json"` и
`schema_version = "1.0"`; его URI — в документном пространстве:

```text
artifact://documents/<document_id>/runs/<run_id>/handoff_package/art_manifest_<doc-tail>_<run-tail>
```

## 5. Примеры JSON

### 5.1. PDF

```json
{
  "document_contract_version": "1.0",
  "document_id": "doc_7f3a91c4e28b5d60a1f49c3b",
  "run_id": "run_20261114_120000_a1b2c3",
  "source_file_name": "article_2024.pdf",
  "source_type": "pdf",
  "source_media_type": "application/pdf",
  "source_sha256": "9b0f5c2e7d4a1b8c3f6e0d2a5c8b1f4e7a0d3c6b9e2f5a8d1c4b7e0f3a6d9c2b",
  "source_size_bytes": 1843200,
  "source_artifact_uri": "artifact://documents/doc_7f3a91c4e28b5d60a1f49c3b/runs/run_20261114_120000_a1b2c3/original_document/art_source_5d60a1f49c3b_a1b2c3",
  "original_encoding": null,
  "encoding_uncertain": false,
  "language": "ru",
  "language_detection_method": "not_detected",
  "created_at": "2026-11-14T12:00:00Z",
  "processing_status": "ingested"
}
```

### 5.2. TXT (cp1251)

```json
{
  "document_contract_version": "1.0",
  "document_id": "doc_2c8d40a1b93e7f5061d2ca8b",
  "run_id": "run_20261114_120500_b4e6d1",
  "source_file_name": "note.txt",
  "source_type": "txt",
  "source_media_type": "text/plain",
  "source_sha256": "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f809",
  "source_size_bytes": 48213,
  "source_artifact_uri": "artifact://documents/doc_2c8d40a1b93e7f5061d2ca8b/runs/run_20261114_120500_b4e6d1/original_document/art_source_7f5061d2ca8b_b4e6d1",
  "original_encoding": "cp1251",
  "encoding_uncertain": false,
  "language": "ru",
  "language_detection_method": "character_ratio",
  "created_at": "2026-11-14T12:05:00Z",
  "processing_status": "ingested"
}
```

> Значения `document_id`, `source_sha256` и id артефактов в примерах иллюстративны. Реальный
> `document_id` выводится из реальных байтов по правилу §2.1 и не может быть «назначен» вручную.

## 6. Чего в контракте нет, хотя это легко предположить

| Ожидание | Фактическое состояние |
|---|---|
| «`bom_and_ratio` означает, что BOM учитывался при определении языка» | значение объявлено в перечислении, но текущий код его **не производит**: `LanguageDetector` возвращает `character_ratio` или `not_detected`. Значащих BOM-ветвей детекции языка нет |
| «`processing_status` обновляется по ходу конвейера» | записанный манифест остаётся `ingested`: последующие статусы живут в состоянии графа (`DocumentProcessingState.processing_status`) и в журнале, а манифест неизменяем |
| «`page_count` есть в манифесте» | нет; число страниц PDF появляется в `ParsedDocument` (и в `DocumentManifest` пакета 2.1) |
| «`source_size_bytes` — размер файла на диске на момент чтения» | это размер **принятых** байтов; при `source_changed_during_ingestion` приём завершается ошибкой, и манифест не пишется |

## 7. Связанные документы

* [`parsed_document.md`](parsed_document.md) — что извлекается из принятого источника;
* [`document_provenance.md`](document_provenance.md) — модель происхождения;
* [`quality_report.md`](quality_report.md) — вердикт контроля качества;
* [`../development/document_ingestion.md`](../development/document_ingestion.md) — шаги приёма,
  лимиты и коды ошибок;
* [`run_and_artifact_contracts.md`](run_and_artifact_contracts.md) — адресация артефактов.
