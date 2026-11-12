"""Regenerate the *current* contract fixtures; verify the frozen ones.

Usage (from the repository root)::

    uv run python tools/generate_fixtures.py

Fixtures for frozen contract versions (``2.0`` package, ``1.0`` Agent 1B input/report) are
historical artefacts and are **not** rewritten: they are validated in place instead, which
is the whole point of keeping them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import BaseModel  # noqa: E402
from tests.fixtures.builders import (  # noqa: E402
    evidence_candidate_list,
    valid_agent_1b_input,
    valid_package,
    valid_verification_report,
)

from research_platform.agents.agent_1a.contracts.package import (  # noqa: E402
    Agent1AExtractionPackageV2,
)
from research_platform.agents.agent_1b.contracts.input_package import (  # noqa: E402
    Agent1BInputPackageV1,
)
from research_platform.agents.agent_1b.contracts.verification_report import (  # noqa: E402
    Agent1BVerificationReportV1,
)

CURRENT: dict[str, Any] = {
    "agent_1a_package_v2_1_valid.json": valid_package,
    "agent_1a_candidates_v2_1_valid.json": evidence_candidate_list,
    "agent_1b_input_v1_1_valid.json": valid_agent_1b_input,
    "agent_1b_report_v1_1_valid.json": valid_verification_report,
}

FROZEN: dict[str, type[BaseModel]] = {
    "agent_1a_package_v2_valid.json": Agent1AExtractionPackageV2,
    "agent_1b_input_v1_valid.json": Agent1BInputPackageV1,
    "agent_1b_report_v1_valid.json": Agent1BVerificationReportV1,
}


def main() -> int:
    base = REPO_ROOT / "tests" / "fixtures"
    for name, builder in CURRENT.items():
        path = base / name
        path.write_text(
            json.dumps(builder(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"wrote: {path.relative_to(REPO_ROOT)}")

    for name, model in FROZEN.items():
        path = base / name
        if not path.exists():
            print(f"MISSING frozen fixture: {path.relative_to(REPO_ROOT)}")
            return 1
        model.model_validate(json.loads(path.read_text(encoding="utf-8")))
        print(f"verified frozen: {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
