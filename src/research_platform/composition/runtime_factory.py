"""Runtime factory: the only place that assembles a LangGraph runtime (composition root).

The factory receives already-built services (port implementations) and returns a compiled
graph wrapper.  It knows about LangGraph; the application services do not, and the CLI only
sees the result.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from research_platform.agents.agent_1a.graph.factory import create_agent_1a_graph
from research_platform.agents.agent_1a.graph.nodes import GraphDependencies
from research_platform.agents.agent_1a.graph.workflow import Agent1ASmokeGraph
from research_platform.application.ports.checkpoint import CheckpointProviderPort
from research_platform.application.ports.model_gateway import ModelGatewayPort
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.run_manager import RunManager
from research_platform.config import PlatformSettings
from research_platform.platform.observability.logging import StructuredLogger

__all__ = ["RuntimeFactory", "RuntimeSettingsView"]


@dataclass(frozen=True, slots=True)
class RuntimeSettingsView:
    """The subset of settings a runtime needs, passed explicitly."""

    agent_version: str = "0.1.0"


class RuntimeFactory:
    """Build agent runtimes from injected dependencies."""

    def __init__(
        self,
        *,
        settings: PlatformSettings,
        run_manager: RunManager,
        artifact_service: ArtifactService,
        logger: StructuredLogger,
        model_gateway: ModelGatewayPort | None,
        checkpoint_provider: CheckpointProviderPort | None,
        clock: Callable[[], object] | None = None,
    ) -> None:
        self._settings = settings
        self._run_manager = run_manager
        self._artifact_service = artifact_service
        self._logger = logger
        self._model_gateway = model_gateway
        self._checkpoint_provider = checkpoint_provider
        self._clock = clock

    def dependencies(self, *, agent_version: str = "0.1.0") -> GraphDependencies:
        """Dependency bundle handed to graph nodes."""
        return GraphDependencies(
            run_manager=self._run_manager,
            artifact_service=self._artifact_service,
            logger=self._logger,
            settings=self._settings,
            model_gateway=self._model_gateway,
            clock=None,
        )

    def build_agent_1a(self, *, agent_version: str = "0.1.0") -> Agent1ASmokeGraph:
        """Compile the technical Agent 1A graph."""
        if agent_version != "0.1.0" and not agent_version:
            raise ValueError("agent_version must not be empty")
        return create_agent_1a_graph(
            self.dependencies(agent_version=agent_version),
            checkpoint_provider=self._checkpoint_provider,
        )
