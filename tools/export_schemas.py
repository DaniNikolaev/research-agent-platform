"""Regenerate the committed JSON Schema files from the Pydantic contract models.

Usage (from the repository root)::

    uv run python tools/export_schemas.py          # rewrite the schema files
    uv run python tools/export_schemas.py --check  # fail if they are out of date

The Pydantic models are the single source of truth; this script is the only writer of
the ``contracts/**/*.schema.json`` files, and
``tests/contract/test_schema_export.py`` enforces that the committed files match the
generated ones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from research_platform.platform.contracts.schemas import (  # noqa: E402
    exportable_schemas,
    serialize_schema,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if a committed schema is out of date",
    )
    args = parser.parse_args(argv)

    stale: list[str] = []
    for relative_path, schema in exportable_schemas().items():
        target = REPO_ROOT / relative_path
        text = serialize_schema(schema)
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            print(f"up-to-date: {relative_path}")
            continue
        if args.check:
            stale.append(relative_path)
            print(f"STALE: {relative_path}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote: {relative_path}")

    if stale:
        print("schema files are out of date; run without --check to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
