"""Composition of the Agent 1A extraction workflow.

Builds the gateway, the token counter, the extraction service and a *fresh* runtime cache per
run.  The cache is intentionally not shared: two runs must never see each other's parsed
document or group results.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from research_platform.application.ports.checkpoint import CheckpointProviderPort
from research_platform.application.ports.logging import LoggerPort
from research_platform.application.services.agent1a_extraction import ExtractionService
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.extraction_errors import (
    ExtractionCheckpointError,
)
from research_platform.application.services.extraction_prompts import (
    PromptTemplate,
    load_prompt_set,
)
from research_platform.application.services.retrieval_pipeline import (
    RetrievalPipeline,
)
from research_platform.application.services.run_manager import RunManager
from research_platform.platform.models.fake_embedding import FakeEmbeddingAdapter
from research_platform.config import PlatformSettings
from research_platform.domain.extraction.models import ExtractionGroup
from research_platform.platform.models.fake_extraction import (
    FakeExtractionGateway,
    FakeExtractionScenario,
)
from research_platform.platform.models.fake_model_adapter import capabilities_from_profile
from research_platform.platform.models.token_counting import HeuristicTokenCounter
from research_platform.workflows.agent1a_extraction_state import Agent1AExtractionState
from research_platform.workflows.agent1a_extraction_workflow import (
    Agent1AExtractionRunner,
    ExtractionDependencies,
    ExtractionOutcome,
    ExtractionRuntimeCache,
    build_extraction_graph,
)

__all__ = ["Agent1AExtractionPipeline", "build_agent1a_pipeline", "build_extraction_gateway"]


def build_extraction_gateway(
    settings: PlatformSettings,
) -> FakeExtractionGateway:
    """Build the configured gateway for the extraction profile."""
    profile_name = settings.agent_1a.model_profile
    profiles = settings.models.profiles
    if profile_name not in profiles:
        raise ValueError(
            f"model profile {profile_name!r} is not configured; add it to models.profiles "
            f"(existing: {sorted(profiles)})"
        )
    resolve = getattr(settings.models, "capabilities_for", None)
    capabilities = (
        resolve(profile_name)
        if callable(resolve)
        else capabilities_from_profile(profiles[profile_name])
    )
    return FakeExtractionGateway(
        profiles={profile_name: capabilities},
        scenario=FakeExtractionScenario(settings.agent_1a.fake_scenario),
        default_profile=profile_name,
    )


@dataclass
class Agent1AExtractionPipeline:
    """Entry point of the extraction workflow for the CLI and the tests."""

    settings: PlatformSettings
    artifacts: ArtifactService
    run_manager: RunManager
    logger: LoggerPort
    gateway: FakeExtractionGateway
    token_counter: HeuristicTokenCounter
    prompts: dict[ExtractionGroup, PromptTemplate]
    embeddings: FakeEmbeddingAdapter | None = None
    checkpoint_provider: CheckpointProviderPort | None = None
    interrupt_after: tuple[str, ...] = ()
    clock: Callable[[], Any] | None = None

    def _dependencies(self, cache: ExtractionRuntimeCache) -> ExtractionDependencies:
        service = ExtractionService(
            gateway=self.gateway,
            token_counter=self.token_counter,
            settings=self.settings.agent_1a,
            logger=self.logger,
            clock=self.clock,
        )
        return ExtractionDependencies(
            settings=self.settings,
            artifacts=self.artifacts,
            extraction=service,
            run_manager=self.run_manager,
            logger=self.logger,
            cache=cache,
            prompts=self.prompts,
            retrieval=RetrievalPipeline(
                counter=self.token_counter,
                chunking=self.settings.chunking,
                retrieval=self.settings.retrieval,
                embedding=self.settings.embedding,
                embeddings=self.embeddings,
            ),
            clock=self.clock,
        )

    def _runner(self) -> Agent1AExtractionRunner:
        deps = self._dependencies(ExtractionRuntimeCache())
        try:
            saver = self.checkpoint_provider.create_saver() if self.checkpoint_provider else None
        except (OSError, sqlite3.Error) as exc:
            raise ExtractionCheckpointError(f"could not open the checkpoint store: {exc}") from exc
        graph = build_extraction_graph(
            deps,
            checkpointer=saver,
            interrupt_after=self.interrupt_after,
        )
        thread_id_for_run: Callable[[str], str] = (
            self.checkpoint_provider.thread_id_for_run
            if self.checkpoint_provider
            else (lambda run_id: run_id)
        )
        return Agent1AExtractionRunner(graph=graph, deps=deps, thread_id_for_run=thread_id_for_run)

    def start(self, *, document_run_id: str, correlation_id: str) -> ExtractionOutcome:
        """Run a new extraction for a finished document run."""
        return self._runner().run(document_run_id=document_run_id, correlation_id=correlation_id)

    def resume(self, *, run_id: str, correlation_id: str = "corr_resume") -> ExtractionOutcome:
        """Continue a checkpointed run (never bypasses the Quality Gate)."""
        return self._runner().resume(run_id=run_id, correlation_id=correlation_id)

    def state_for_run(self, run_id: str) -> Agent1AExtractionState | None:
        """Read the checkpointed state of one run."""
        return self._runner().state_for_run(run_id)


def _build_embeddings(settings: PlatformSettings) -> FakeEmbeddingAdapter | None:
    """Build the deterministic fake embedding adapter only on explicit simulation opt-in."""
    if (
        settings.embedding.execution_mode != "simulation"
        or settings.embedding.profile_id is None
    ):
        return None
    from research_platform.application.ports.embedding import EmbeddingCapabilities

    profile_id = settings.embedding.profile_id
    capabilities = EmbeddingCapabilities(
        profile_id=profile_id,
        provider_id="fake-embedding",
        model_id="fake-hash-embedding-v1",
        dimension=settings.embedding.dimension,
        normalized=True,
        is_fake=True,
        execution_mode="simulation",
    )
    return FakeEmbeddingAdapter(
        profiles={profile_id: capabilities}, profile_id=profile_id, dimension=settings.embedding.dimension
    )


def build_agent1a_pipeline(
    *,
    settings: PlatformSettings,
    artifacts: ArtifactService,
    run_manager: RunManager,
    logger: LoggerPort,
    checkpoint_provider: CheckpointProviderPort | None = None,
    interrupt_after: tuple[str, ...] = (),
    clock: Callable[[], Any] | None = None,
) -> Agent1AExtractionPipeline:
    """Assemble the extraction pipeline from platform services."""
    return Agent1AExtractionPipeline(
        settings=settings,
        artifacts=artifacts,
        run_manager=run_manager,
        logger=logger,
        gateway=build_extraction_gateway(settings),
        token_counter=HeuristicTokenCounter(),
        embeddings=_build_embeddings(settings),
        prompts=load_prompt_set(version=settings.agent_1a.prompt_set_version),
        checkpoint_provider=checkpoint_provider,
        interrupt_after=interrupt_after or tuple(settings.agent_1a.interrupt_after),
        clock=clock,
    )
