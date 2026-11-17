"""Application bootstrap: the single place where the platform is assembled.

Responsibilities, in order:

1. resolve the project root and load/validate settings;
2. create the structured logger (redaction always on);
3. create infrastructure adapters (artifact store, run repository, checkpoint provider,
   fake model gateway);
4. create application services (run manager, artifact service, health check);
5. create the runtime factory;
6. expose a context that closes everything in reverse order.

There are no global singletons: the context is an object returned to the caller (CLI or a
test).  Bootstrap performs no network access and creates no database connection.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from research_platform.application.ports.checkpoint import CheckpointProviderPort
from research_platform.application.ports.model_gateway import ModelGatewayPort
from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.configuration_service import ConfigurationService
from research_platform.application.services.health_check import HealthCheckService
from research_platform.application.services.redaction import Redactor
from research_platform.application.services.run_manager import RunManager
from research_platform.composition.agent1a_extraction import (
    Agent1AExtractionPipeline,
    build_agent1a_pipeline,
)
from research_platform.composition.document_pipeline import (
    DocumentPipeline,
    build_document_pipeline,
)
from research_platform.composition.runtime_factory import RuntimeFactory
from research_platform.config import (
    CheckpointerKind,
    LogLevel,
    PlatformSettings,
    SettingsError,
    load_settings_from_project,
)
from research_platform.platform.artifact_store import LocalFileArtifactStore
from research_platform.platform.checkpointing import InMemoryCheckpointProvider
from research_platform.platform.checkpointing.sqlite import SQLiteCheckpointProvider
from research_platform.platform.models import FakeModelGateway, capabilities_from_profile
from research_platform.platform.observability.logging import (
    APPLICATION_SHUTDOWN,
    APPLICATION_STARTED,
    CONFIG_LOADED,
    StructuredLogger,
)
from research_platform.platform.persistence.in_memory_run_repository import (
    InMemoryRunRepository,
)

__all__ = ["ApplicationBootstrap", "ApplicationContext", "bootstrap_application"]


@dataclass(slots=True)
class ApplicationContext:
    """Everything the platform assembled, plus the lifecycle hooks."""

    project_root: Path
    settings: PlatformSettings
    logger: StructuredLogger
    configuration: ConfigurationService
    run_manager: RunManager
    artifact_service: ArtifactService
    run_repository: InMemoryRunRepository
    artifact_store: LocalFileArtifactStore
    checkpoint_provider: CheckpointProviderPort | None
    model_gateway: ModelGatewayPort | None
    runtime_factory: RuntimeFactory
    document_pipeline: DocumentPipeline
    agent1a_extraction: Agent1AExtractionPipeline | None
    health_check: HealthCheckService
    _closers: list[Callable[[], None]]

    def close(self) -> None:
        """Close adapters and emit the shutdown event (idempotent)."""
        closers, self._closers = self._closers, []
        for closer in reversed(closers):
            with contextlib.suppress(Exception):
                closer()
        self.logger.event(APPLICATION_SHUTDOWN)


class ApplicationBootstrap:
    """Assemble a :class:`ApplicationContext`."""

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        settings: PlatformSettings | None = None,
        log_stream: TextIO | None = None,
        environ: dict[str, str] | None = None,
        enable_model_gateway: bool = True,
    ) -> None:
        self._project_root = (project_root or Path.cwd()).resolve()
        self._settings = settings
        self._log_stream = log_stream
        self._environ = environ
        self._enable_model_gateway = enable_model_gateway

    # ------------------------------------------------------------------ settings

    @property
    def project_root(self) -> Path:
        """Root directory the platform resolves relative paths against."""
        return self._project_root

    def load_settings(self) -> PlatformSettings:
        """Load settings (or return the ones supplied explicitly).

        Pydantic validation messages include the offending input value, which is exactly
        where a mistyped secret would appear.  The diagnostic is therefore rebuilt from the
        error *type* and location only, and passed through the redactor.
        """
        if self._settings is not None:
            return self._settings
        try:
            return load_settings_from_project(self._project_root, environ=self._environ)
        except SettingsError:
            raise
        except ValidationError as exc:
            redactor = Redactor()
            details: list[str] = []
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"]) or "<root>"
                details.append(f"{location}: {redactor.redact_text(str(error['msg']))}")
            raise SettingsError("invalid configuration: " + "; ".join(details)) from None

    # ------------------------------------------------------------------- build

    def build(self) -> ApplicationContext:
        """Create the context; raises on invalid configuration."""
        settings = self.load_settings()
        redactor = Redactor()
        logger = StructuredLogger(
            service_name=settings.app.name,
            stream=self._log_stream,
            level=LogLevel(settings.app.log_level.value),
            log_format=settings.observability.log_format,
            redactor=redactor,
            enabled=settings.observability.enabled or self._log_stream is not None,
        )
        configuration = ConfigurationService(
            project_root=self._project_root,
            logger=logger,
            environ=self._environ,
            redactor=redactor,
        )
        logger.event(
            APPLICATION_STARTED,
            environment=settings.app.environment.value,
            project_root=str(self._project_root),
        )

        artifact_root = (
            self._project_root / settings.paths.data_root / settings.paths.artifact_subdir
        ).resolve()
        artifact_store = LocalFileArtifactStore(root=artifact_root)
        run_repository = InMemoryRunRepository()

        checkpoint_provider: CheckpointProviderPort | None = None
        if settings.runtime.checkpointer is CheckpointerKind.SQLITE:
            checkpoint_provider = SQLiteCheckpointProvider(
                database_path=(self._project_root / settings.runtime.checkpoint_database).resolve(),
                timeout_seconds=settings.runtime.checkpoint_timeout_seconds,
            )
        elif settings.runtime.checkpointer is CheckpointerKind.IN_MEMORY:
            checkpoint_provider = InMemoryCheckpointProvider()

        model_gateway: ModelGatewayPort | None = None
        if self._enable_model_gateway and settings.models.profiles:
            profiles = {
                name: capabilities_from_profile(profile)
                for name, profile in settings.models.profiles.items()
            }
            model_gateway = FakeModelGateway(
                profiles=profiles, default_profile=settings.models.default_profile
            )

        run_manager = RunManager(repository=run_repository, logger=logger)
        artifact_service = ArtifactService(store=artifact_store, logger=logger)
        runtime_factory = RuntimeFactory(
            settings=settings,
            run_manager=run_manager,
            artifact_service=artifact_service,
            logger=logger,
            model_gateway=model_gateway,
            checkpoint_provider=checkpoint_provider,
        )
        health_check = HealthCheckService(
            project_root=self._project_root,
            settings=settings,
            artifact_store=artifact_store,
            model_gateway=model_gateway,
            runtime_builder=lambda: runtime_factory.build_agent_1a(),
        )

        document_pipeline = build_document_pipeline(
            settings=settings,
            artifacts=artifact_service,
            run_manager=run_manager,
            logger=logger,
        )
        agent1a_extraction: Agent1AExtractionPipeline | None | None = None
        if settings.models.profiles:
            agent1a_extraction = build_agent1a_pipeline(
                settings=settings,
                artifacts=artifact_service,
                run_manager=run_manager,
                logger=logger,
                checkpoint_provider=checkpoint_provider,
            )

        closers: list[Callable[[], None]] = [
            artifact_store.close,
            run_repository.close,
            logger.close,
        ]
        if checkpoint_provider is not None:
            closers.append(checkpoint_provider.close)

        logger.event(
            CONFIG_LOADED,
            environment=settings.app.environment.value,
            profiles=len(settings.models.profiles),
            checkpointer=settings.runtime.checkpointer.value,
        )
        return ApplicationContext(
            project_root=self._project_root,
            settings=settings,
            logger=logger,
            configuration=configuration,
            run_manager=run_manager,
            artifact_service=artifact_service,
            run_repository=run_repository,
            artifact_store=artifact_store,
            checkpoint_provider=checkpoint_provider,
            model_gateway=model_gateway,
            runtime_factory=runtime_factory,
            document_pipeline=document_pipeline,
            agent1a_extraction=agent1a_extraction,
            health_check=health_check,
            _closers=closers,
        )


def bootstrap_application(
    *,
    project_root: Path | None = None,
    settings: PlatformSettings | None = None,
    log_stream: TextIO | None = None,
    environ: dict[str, str] | None = None,
    enable_model_gateway: bool = True,
) -> ApplicationContext:
    """Convenience wrapper: build a context in one call."""
    return ApplicationBootstrap(
        project_root=project_root,
        settings=settings,
        log_stream=log_stream,
        environ=environ,
        enable_model_gateway=enable_model_gateway,
    ).build()
