"""Graph construction entry points.

``create_agent_1a_graph`` is the single place that combines dependencies, the checkpoint
provider and the graph builder.  The application layer calls it through
:class:`~research_platform.application.runtime.runtime_factory.RuntimeFactory`; nothing else
should construct a graph directly.
"""

from __future__ import annotations

from typing import Any

from research_platform.agents.agent_1a.graph.nodes import GraphDependencies
from research_platform.agents.agent_1a.graph.workflow import Agent1ASmokeGraph, build_smoke_graph
from research_platform.application.ports.checkpoint import CheckpointProviderPort

__all__ = ["create_agent_1a_graph"]


def create_agent_1a_graph(
    dependencies: GraphDependencies,
    *,
    checkpoint_provider: CheckpointProviderPort | None = None,
) -> Agent1ASmokeGraph:
    """Build the technical Agent 1A graph with an optional checkpointer."""
    saver: object | None = None
    thread_id_for_run: Any | None = None
    if checkpoint_provider is not None:
        saver = checkpoint_provider.create_saver()
        thread_id_for_run = checkpoint_provider.thread_id_for_run
    return build_smoke_graph(dependencies, checkpointer=saver, thread_id_for_run=thread_id_for_run)
