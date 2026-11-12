"""Agent 1A LangGraph runtime (Iteration 1: technical skeleton only).

Implemented here:

* :mod:`state` — the request model and the synthetic smoke state builder;
* :mod:`nodes` — four technical nodes with injected dependencies;
* :mod:`workflow` — graph assembly plus a runner that owns failure bookkeeping;
* :mod:`factory` — the single entry point that combines dependencies and a checkpointer.

Not implemented here (and deliberately so): parsing, quality gate, retrieval, chunking,
evidence resolution, field extraction and package export.  Those are Iterations 2–5.
"""

from __future__ import annotations

from research_platform.agents.agent_1a.graph.factory import create_agent_1a_graph
from research_platform.agents.agent_1a.graph.nodes import GraphDependencies
from research_platform.agents.agent_1a.graph.state import (
    SMOKE_DOCUMENT_ID,
    SMOKE_INPUT_SHA256,
    SmokeRunRequest,
    build_smoke_state,
)
from research_platform.agents.agent_1a.graph.workflow import (
    Agent1ASmokeGraph,
    SmokeRunResult,
    build_smoke_graph,
)

__all__ = [
    "SMOKE_DOCUMENT_ID",
    "SMOKE_INPUT_SHA256",
    "Agent1ASmokeGraph",
    "GraphDependencies",
    "SmokeRunRequest",
    "SmokeRunResult",
    "build_smoke_graph",
    "build_smoke_state",
    "create_agent_1a_graph",
]
