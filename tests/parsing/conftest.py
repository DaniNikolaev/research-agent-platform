"""Shared fixtures for the parsing tests.

The Agent 1A end-to-end tests live here (they need the real Docling fixtures) and reuse the
project/context fixtures of the extraction test package by importing the fixture objects.
"""

from __future__ import annotations

from tests.agent1a.conftest import (  # noqa: F401 - re-exported as pytest fixtures
    Project,
    context,
    document_run,
    project,
    seed_document_run,
    write_txt,
)
