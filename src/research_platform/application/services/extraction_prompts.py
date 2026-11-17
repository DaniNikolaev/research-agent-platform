"""Loader for the versioned extraction prompts.

Prompts live as files in ``research_platform/prompts/agent_1a/<version>/`` so that a change is
a reviewable diff, not an invisible edit inside Python code.  The loader pins the set version,
exposes the template id, and computes a content hash that the run manifest records — an
extraction result therefore states exactly which instructions produced it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Final

from research_platform.domain.extraction.models import (
    PROMPT_SET_VERSION,
    ExtractionGroup,
)

__all__ = ["PROMPT_SET_VERSION", "PromptTemplate", "load_prompt", "load_prompt_set"]

_PROMPT_DIR: Final = "agent_1a"
_DIR_BY_VERSION: Final[dict[str, str]] = {"1.0": "v1"}
"""Prompt set version → package directory. A new set gets a new directory and a new entry."""
_SCHEMA_ID: Final = "agent1a.{group}"
_PROMPT_IDS: Final[dict[ExtractionGroup, str]] = {
    ExtractionGroup.G1_BIBLIOGRAPHY: "agent1a.extract.g1_bibliography",
    ExtractionGroup.G2_RESEARCH_DESIGN: "agent1a.extract.g2_research_design",
    ExtractionGroup.G3_METHODS_DATA_VARIABLES: "agent1a.extract.g3_methods_data_variables",
    ExtractionGroup.G4_FINDINGS_IMPLICATIONS: "agent1a.extract.g4_findings_implications",
}


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One versioned instruction set."""

    prompt_id: str
    prompt_version: str
    group: ExtractionGroup
    schema_id: str
    text: str
    content_hash: str
    field_keys: tuple[str, ...]


def _read(version: str, group: ExtractionGroup) -> str:
    directory = _DIR_BY_VERSION.get(version)
    if directory is None:
        raise FileNotFoundError(
            f"unknown prompt set version {version!r}; known: {sorted(_DIR_BY_VERSION)}"
        )
    package = f"research_platform.prompts.{_PROMPT_DIR}.{directory}"
    try:
        return resources.files(package).joinpath(f"{group.value}.md").read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError) as exc:  # pragma: no cover - packaging
        raise FileNotFoundError(
            f"prompt template for {group.value} is missing in prompt set {version!r}"
        ) from exc


@lru_cache(maxsize=16)
def load_prompt(group: ExtractionGroup, *, version: str = PROMPT_SET_VERSION) -> PromptTemplate:
    """Load one prompt template (cached per version and group)."""
    text = _read(version, group)
    return PromptTemplate(
        prompt_id=_PROMPT_IDS[group],
        prompt_version=version,
        group=group,
        schema_id=_SCHEMA_ID.format(group=group.value),
        text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        field_keys=tuple(_field_keys(group)),
    )


def load_prompt_set(*, version: str = PROMPT_SET_VERSION) -> dict[ExtractionGroup, PromptTemplate]:
    """Load every prompt of one set version."""
    return {group: load_prompt(group, version=version) for group in ExtractionGroup}


def _field_keys(group: ExtractionGroup) -> tuple[str, ...]:
    from research_platform.domain.extraction.models import field_keys_of

    return field_keys_of(group)
