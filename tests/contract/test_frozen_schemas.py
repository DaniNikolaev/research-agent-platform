"""Frozen historical schemas must not drift.

``test_schema_export.py`` proves that each committed schema equals the schema generated
from its Pydantic model *today*.  That alone would not notice a coordinated change of both
the model and the file.  This module pins the published bytes with a checksum sidecar, so
touching a historical contract fails loudly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from research_platform.platform.contracts.schemas import (
    FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH,
    FROZEN_AGENT_1B_INPUT_SCHEMA_PATH,
    FROZEN_AGENT_1B_REPORT_SCHEMA_PATH,
    exportable_schemas,
    frozen_schemas,
)

FROZEN_PATHS = (
    FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH,
    FROZEN_AGENT_1B_INPUT_SCHEMA_PATH,
    FROZEN_AGENT_1B_REPORT_SCHEMA_PATH,
)


@pytest.mark.parametrize("relative_path", FROZEN_PATHS)
def test_frozen_schema_checksum_is_preserved(repo_root: Path, relative_path: str) -> None:
    schema_path = repo_root / relative_path
    sidecar = schema_path.with_name(f"{schema_path.name}.sha256")
    assert sidecar.exists(), f"missing checksum sidecar for {relative_path}"
    recorded = sidecar.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    assert actual == recorded, (
        f"{relative_path} changed; a published contract version must be immutable. "
        "Publish a new version instead."
    )


def test_frozen_schemas_are_still_generated_from_models() -> None:
    """History is not a hand-edited file: it is still produced by the generator."""
    assert set(frozen_schemas()) == set(FROZEN_PATHS)


def test_frozen_and_current_schemas_do_not_overlap() -> None:
    frozen = set(frozen_schemas())
    current = set(exportable_schemas()) - frozen
    assert frozen.isdisjoint(current)
    assert len(current) == 4
