"""Legacy compatibility: the v1 schema is frozen, the 27 fields are preserved."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError
from tests.fixtures.builders import empty_card

from research_platform.agents.agent_1a.contracts.adapter import (
    REQUIRED_V1_PROVENANCE,
    AdapterOutcome,
    AdapterResult,
    missing_required_provenance,
)
from research_platform.domain.cards.models import (
    CARD_FIELD_PATHS,
    Agent1ACardV2,
    StringListField,
    TextField,
    YearField,
    iter_card_fields,
)

LEGACY_SECTIONS = (
    "bibliography",
    "research_design",
    "methodology",
    "data",
    "variables",
    "findings",
    "limitations",
    "future_research",
    "contribution",
)

_KIND_OF_MODEL = {
    TextField: "textField",
    StringListField: "stringListField",
    YearField: "yearField",
}


def legacy_schema_path(repo_root: Path) -> Path:
    return repo_root / "contracts" / "legacy" / "agent_1a_v1" / "card.schema.json"


def test_legacy_schema_checksum_is_preserved(repo_root: Path) -> None:
    path = legacy_schema_path(repo_root)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    recorded = (
        (repo_root / "contracts" / "legacy" / "agent_1a_v1" / "card.schema.json.sha256")
        .read_text(encoding="utf-8")
        .split()[0]
    )
    assert digest == recorded


def test_legacy_example_card_still_validates_against_v1_schema(repo_root: Path) -> None:
    schema = json.loads(legacy_schema_path(repo_root).read_text(encoding="utf-8"))
    card = json.loads(
        (repo_root / "tests" / "fixtures" / "legacy" / "card_v1_en_article.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(card)


def test_legacy_schema_rejects_unknown_field(repo_root: Path) -> None:
    schema = json.loads(legacy_schema_path(repo_root).read_text(encoding="utf-8"))
    card = json.loads(
        (repo_root / "tests" / "fixtures" / "legacy" / "card_v1_en_article.json").read_text(
            encoding="utf-8"
        )
    )
    card["bibliography"]["issn"] = {"value": "1234-5678", "status": "found", "evidence": []}
    with pytest.raises(JsonSchemaError):
        Draft202012Validator(schema).validate(card)


def test_legacy_schema_rejects_unknown_status(repo_root: Path) -> None:
    schema = json.loads(legacy_schema_path(repo_root).read_text(encoding="utf-8"))
    card = json.loads(
        (repo_root / "tests" / "fixtures" / "legacy" / "card_v1_en_article.json").read_text(
            encoding="utf-8"
        )
    )
    card["limitations"]["status"] = "probably_found"
    with pytest.raises(JsonSchemaError):
        Draft202012Validator(schema).validate(card)


def _legacy_field_kinds(schema: dict[str, Any]) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for section in LEGACY_SECTIONS:
        spec = schema["properties"][section]
        if "$ref" in spec:
            kinds[section] = spec["$ref"].rsplit("/", 1)[-1]
            continue
        for name, field_spec in spec["properties"].items():
            kinds[f"{section}.{name}"] = field_spec["$ref"].rsplit("/", 1)[-1]
    return kinds


def test_all_twenty_seven_legacy_fields_are_preserved(repo_root: Path) -> None:
    schema = json.loads(legacy_schema_path(repo_root).read_text(encoding="utf-8"))
    legacy_kinds = _legacy_field_kinds(schema)
    assert len(legacy_kinds) == 27
    assert set(legacy_kinds) == set(CARD_FIELD_PATHS)

    card = Agent1ACardV2.model_validate(empty_card())
    v2_kinds = {
        path: _KIND_OF_MODEL[type(item)]
        for path, item in iter_card_fields(card)  # type: ignore[index]
    }
    assert v2_kinds == legacy_kinds


def test_legacy_status_vocabulary_is_preserved(repo_root: Path) -> None:
    schema = json.loads(legacy_schema_path(repo_root).read_text(encoding="utf-8"))
    legacy_statuses = set(schema["$defs"]["status"]["enum"])
    assert legacy_statuses == {"found", "not_found", "unclear", "not_applicable"}


def test_adapter_reports_every_missing_provenance_path() -> None:
    missing = missing_required_provenance({})
    assert missing == list(REQUIRED_V1_PROVENANCE)
    assert "source_sha256" in missing
    assert "run.run_id" in missing


def _nested_provenance() -> dict[str, Any]:
    """Build a provenance mapping shaped exactly like the dotted requirement list."""
    root: dict[str, Any] = {}
    for dotted in REQUIRED_V1_PROVENANCE:
        node = root
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = "placeholder"
    return root


def test_adapter_does_not_accept_empty_string_placeholders() -> None:
    provenance = _nested_provenance()
    provenance["run"]["run_id"] = ""
    missing = missing_required_provenance(provenance)
    assert missing == ["run.run_id"]


def test_adapter_accepts_complete_provenance() -> None:
    assert missing_required_provenance(_nested_provenance()) == []


def test_adapter_result_cannot_invent_a_package() -> None:
    with pytest.raises(ValueError):
        AdapterResult(outcome=AdapterOutcome.MISSING_METADATA, missing_required=())
    with pytest.raises(ValueError):
        AdapterResult(outcome=AdapterOutcome.CONVERTIBLE)


def test_adapter_result_missing_metadata_is_explicit() -> None:
    result = AdapterResult(
        outcome=AdapterOutcome.MISSING_METADATA,
        missing_required=("source_sha256", "run.run_id"),
        notes=("legacy card v1.0 does not record source provenance",),
    )
    assert result.package is None


def test_legacy_card_payload_is_not_mutated_by_provenance_scan(repo_root: Path) -> None:
    card = json.loads(
        (repo_root / "tests" / "fixtures" / "legacy" / "card_v1_en_article.json").read_text(
            encoding="utf-8"
        )
    )
    original = copy.deepcopy(card)
    missing_required_provenance({"card": card})
    assert card == original
