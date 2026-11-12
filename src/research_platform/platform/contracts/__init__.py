"""Contract registry: JSON Schema export and frozen-schema verification.

The Pydantic models are the single source of truth.  The schema files committed under
``contracts/`` are generated from them by ``tools/export_schemas.py`` and are verified
byte-for-byte in ``tests/contract/test_schema_export.py``, so two divergent schemas
cannot exist.  Frozen historical schemas additionally carry pinned checksums.
"""

from __future__ import annotations

from research_platform.platform.contracts.schemas import (
    AGENT_1A_CANDIDATES_SCHEMA_PATH,
    AGENT_1A_PACKAGE_SCHEMA_PATH,
    AGENT_1B_INPUT_SCHEMA_PATH,
    AGENT_1B_REPORT_SCHEMA_PATH,
    FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH,
    FROZEN_AGENT_1B_INPUT_SCHEMA_PATH,
    FROZEN_AGENT_1B_REPORT_SCHEMA_PATH,
    SCHEMA_DIALECT,
    agent_1a_candidates_schema,
    agent_1a_package_schema,
    agent_1b_input_schema,
    agent_1b_verification_report_schema,
    exportable_schemas,
    frozen_schemas,
    serialize_schema,
)

__all__ = [
    "AGENT_1A_CANDIDATES_SCHEMA_PATH",
    "AGENT_1A_PACKAGE_SCHEMA_PATH",
    "AGENT_1B_INPUT_SCHEMA_PATH",
    "AGENT_1B_REPORT_SCHEMA_PATH",
    "FROZEN_AGENT_1A_PACKAGE_SCHEMA_PATH",
    "FROZEN_AGENT_1B_INPUT_SCHEMA_PATH",
    "FROZEN_AGENT_1B_REPORT_SCHEMA_PATH",
    "SCHEMA_DIALECT",
    "agent_1a_candidates_schema",
    "agent_1a_package_schema",
    "agent_1b_input_schema",
    "agent_1b_verification_report_schema",
    "exportable_schemas",
    "frozen_schemas",
    "serialize_schema",
]
