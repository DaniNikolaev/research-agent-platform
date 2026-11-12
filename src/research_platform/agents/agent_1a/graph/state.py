"""Agent 1A graph state.

The state contract lives in ``contracts/graph_state.py`` (iteration 0) and is reused here
verbatim — this module only adds the *request* model and the builder for the synthetic
smoke state.  Nothing scientific is placed in a state: identifiers, artefact URIs and small
counters only.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from pydantic import StringConstraints

from research_platform.agents.agent_1a.contracts.graph_state import (
    Agent1AState,
    SourceMetadata,
    initial_state,
    new_retry_counters,
    validate_state,
)
from research_platform.domain.base import ContractModel, Identifier, ShortStr
from research_platform.domain.runs.models import generate_correlation_id

__all__ = [
    "SMOKE_DOCUMENT_ID",
    "SMOKE_INPUT_BYTES",
    "SMOKE_INPUT_SHA256",
    "Agent1AState",
    "SmokeRunRequest",
    "build_smoke_state",
    "new_retry_counters",
    "validate_state",
]

SMOKE_INPUT_BYTES: bytes = b"SMOKE TEST INPUT - no scientific document\n"
"""The synthetic input of the smoke workflow.

It is a real byte sequence with a real SHA-256, so the run describes something that
exists.  It is not a scientific document and no card is produced from it.
"""

SMOKE_INPUT_SHA256: str = hashlib.sha256(SMOKE_INPUT_BYTES).hexdigest()
SMOKE_DOCUMENT_ID = "doc_smoke_0001"
SMOKE_AGENT_VERSION = "0.1.0"


class SmokeRunRequest(ContractModel):
    """Inputs of one technical smoke run."""

    document_id: Identifier = SMOKE_DOCUMENT_ID
    agent_version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")] = (
        SMOKE_AGENT_VERSION
    )
    correlation_id: ShortStr | None = None
    note: Annotated[str | None, StringConstraints(max_length=200)] = None
    """Free-form operator note; it is logged, never interpreted."""


def build_smoke_state(request: SmokeRunRequest, *, run_id: str = "") -> Agent1AState:
    """Build the synthetic, JSON-serialisable initial state of a smoke run.

    ``run_id`` is normally supplied by the runner, because LangGraph needs the
    ``thread_id`` (derived from the run id) before the graph starts.  When it is empty the
    ``initialize_run`` node assigns one instead.
    """
    source_metadata = SourceMetadata(
        file_name="smoke_input.txt",
        media_type="text/plain",
        language="unknown",
        page_count=None,
    )
    correlation_id = request.correlation_id or generate_correlation_id()
    state = initial_state(
        run_id=run_id,
        correlation_id=correlation_id,
        document_id=str(request.document_id),
        agent_version=request.agent_version,
        source_ref=None,
        source_sha256=SMOKE_INPUT_SHA256,
        source_metadata=source_metadata,
    )
    state["warnings"] = ["SMOKE TEST ONLY - no scientific document was processed"]
    return state
