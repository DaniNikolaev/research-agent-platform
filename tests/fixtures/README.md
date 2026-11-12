# Contract fixtures

Each file is a complete, valid document of one published contract version.  They are
committed so that a reviewer, the CLI and the contract suite all work against the same
bytes.

## Current contracts

| File | Contract | Model | Schema |
|---|---|---|---|
| `agent_1a_package_v2_1_valid.json` | Agent 1A package 2.1 | `Agent1AExtractionPackageV21` | `contracts/agent_1a/v2.1/extraction_package.schema.json` |
| `agent_1a_candidates_v2_1_valid.json` | Evidence candidates 2.1 | `EvidenceCandidateList` | `contracts/agent_1a/v2.1/evidence_candidates.schema.json` |
| `agent_1b_input_v1_1_valid.json` | Agent 1B input 1.1 | `Agent1BInputPackageV11` | `contracts/agent_1b/v1.1/input_package.schema.json` |
| `agent_1b_report_v1_1_valid.json` | Agent 1B report 1.1 | `Agent1BVerificationReportV11` | `contracts/agent_1b/v1.1/verification_report.schema.json` |

## Frozen history — do not regenerate

| File | Contract | Model |
|---|---|---|
| `agent_1a_package_v2_valid.json` | Agent 1A package **2.0** | `Agent1AExtractionPackageV2` |
| `agent_1b_input_v1_valid.json` | Agent 1B input **1.0** | `Agent1BInputPackageV1` |
| `agent_1b_report_v1_valid.json` | Agent 1B report **1.0** | `Agent1BVerificationReportV1` |
| `legacy/card_v1_en_article.json` | legacy card v1.0 (real artefact copy) | — (validated by the legacy JSON Schema) |

The frozen files are validated in place by `tools/generate_fixtures.py` and by
`tests/contract/test_legacy_compat.py` / `test_schema_export.py`.  Rewriting them would
destroy the only proof that an already published contract still works.

## Regenerating

```powershell
uv run python tools/generate_fixtures.py
```

This rewrites the four current fixtures and *verifies* (never rewrites) the frozen ones.
The builders in `tests/fixtures/builders.py` remain the source of truth.
