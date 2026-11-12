# Legacy contract: `card_schema.json` v1.0 (frozen)

This directory is a **frozen historical copy** of the experimental Agent 1A card schema.
It must never be edited: it is the compatibility baseline for old cards and for
comparative tests (see `docs/migration/v1_to_v2_mapping.md`).

| Item | Value |
|---|---|
| Source file | `D:\MultiAgentPipeline\Agent1\Agent1A\config\card_schema.json` |
| Local copy | `contracts/legacy/agent_1a_v1/card.schema.json` |
| Copy semantics | byte-identical (`Copy-Item`, no reformatting, no edits) |
| SHA-256 | `0c7075d2adba9ea6f3e148f1110f995f833b87028ced9d7145c8d1e2ace8bbf1` |
| `$id` | `https://agent-1a.local/schemas/card_schema-1.0.json` |
| JSON Schema dialect | Draft 2020-12 |
| Content fields | 27 (verified by `tests/contract/test_legacy_compat.py`) |
| Status vocabulary | `found`, `not_found`, `unclear`, `not_applicable` |

The checksum is also machine-readable in `card.schema.json.sha256`.

The legacy project itself (`D:\MultiAgentPipeline\Agent1\Agent1A`) remains read-only for
this iteration: nothing here is written back to it.
