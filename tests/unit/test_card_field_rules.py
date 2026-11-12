"""Card field contract: the 27 legacy fields and the four status rules."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.builders import empty_card, field, valid_card

from research_platform.domain.cards.models import (
    CARD_FIELD_PATHS,
    CARD_SECTIONS,
    Agent1ACardV2,
    FieldStatus,
    StringListField,
    TextField,
    YearField,
    iter_card_fields,
)


def validate(card: dict[str, Any]) -> Agent1ACardV2:
    return Agent1ACardV2.model_validate(card)


def test_card_has_twenty_seven_content_fields_and_nine_sections() -> None:
    card = validate(empty_card())
    assert len(CARD_FIELD_PATHS) == 27
    assert len(CARD_SECTIONS) == 9
    assert len(list(iter_card_fields(card))) == 27


def test_valid_card_is_accepted() -> None:
    card = validate(valid_card())
    assert card.bibliography.year.value == 2021
    assert card.limitations.status is FieldStatus.NOT_APPLICABLE


def test_field_types_match_legacy_kinds() -> None:
    card = validate(empty_card())
    kinds = {path: type(item) for path, item in iter_card_fields(card)}
    assert kinds["bibliography.title"] is TextField
    assert kinds["bibliography.authors"] is StringListField
    assert kinds["bibliography.year"] is YearField
    assert kinds["methodology"] is StringListField
    assert kinds["limitations"] is StringListField


def test_unknown_field_is_rejected() -> None:
    card = empty_card()
    card["bibliography"]["issn"] = field("1234-5678", "found", [{"evidence_id": "ev_000001"}])
    with pytest.raises(ValidationError) as excinfo:
        validate(card)
    assert "issn" in str(excinfo.value)


def test_missing_field_is_rejected() -> None:
    card = empty_card()
    del card["contribution"]
    with pytest.raises(ValidationError):
        validate(card)


def test_unknown_status_is_rejected_with_a_diagnostic() -> None:
    card = empty_card()
    card["limitations"] = field("x", "probably_found", [])
    with pytest.raises(ValidationError) as excinfo:
        validate(card)
    message = str(excinfo.value)
    assert "limitations" in message
    assert "found" in message


@pytest.mark.parametrize(
    ("status", "value", "refs"),
    [
        ("found", None, []),
        ("found", "text", []),
        ("found", "", [{"evidence_id": "ev_000001"}]),
        ("not_found", "text", []),
        ("not_found", None, [{"evidence_id": "ev_000001"}]),
        ("not_applicable", ["item"], []),
        ("not_applicable", None, [{"evidence_id": "ev_000001"}]),
        ("unclear", None, []),
    ],
)
def test_status_rules_are_enforced(status: str, value: Any, refs: list[dict[str, Any]]) -> None:
    """Every status has a machine-checked rule, not only a documented one."""
    with pytest.raises(ValidationError):
        TextField.model_validate(field(value, status, refs))


@pytest.mark.parametrize(
    ("status", "value", "refs"),
    [
        ("unclear", "partial value", []),
        ("unclear", None, [{"evidence_id": "ev_000001"}]),
        ("not_found", None, []),
        ("not_applicable", None, []),
        ("found", "value", [{"evidence_id": "ev_000001"}]),
    ],
)
def test_permitted_status_combinations(status: str, value: Any, refs: list[dict[str, Any]]) -> None:
    item = TextField.model_validate(field(value, status, refs))
    assert item.status.value == status


def test_found_requires_at_least_one_evidence_reference() -> None:
    with pytest.raises(ValidationError) as excinfo:
        TextField.model_validate(field("something", "found", []))
    assert "at least one evidence" in str(excinfo.value)


def test_duplicate_evidence_reference_in_one_field_is_rejected() -> None:
    refs = [{"evidence_id": "ev_000001"}, {"evidence_id": "ev_000001"}]
    with pytest.raises(ValidationError) as excinfo:
        TextField.model_validate(field("value", "found", refs))
    assert "duplicate evidence_id" in str(excinfo.value)


def test_empty_string_list_with_found_is_rejected() -> None:
    with pytest.raises(ValidationError):
        StringListField.model_validate(field([], "found", [{"evidence_id": "ev_000001"}]))


def test_duplicate_list_items_are_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        StringListField.model_validate(field(["a", "a"], "found", [{"evidence_id": "ev_000001"}]))
    assert "duplicate list items" in str(excinfo.value)


def test_year_out_of_range_is_rejected() -> None:
    for bad_year in (1200, 2500):
        with pytest.raises(ValidationError):
            YearField.model_validate(field(bad_year, "found", [{"evidence_id": "ev_000001"}]))


def test_card_is_immutable() -> None:
    card = validate(valid_card())
    with pytest.raises(ValidationError):
        card.bibliography = copy.deepcopy(card.bibliography)  # type: ignore[misc]
