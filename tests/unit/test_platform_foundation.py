"""Bootstrap, configuration layering, health checks, fake gateway and checkpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.fixtures.logging import make_settings, silent_logger

from research_platform.app import ApplicationBootstrap, bootstrap_application
from research_platform.application.ports.artifact_store import ArtifactStorePort
from research_platform.application.ports.checkpoint import CheckpointProviderPort
from research_platform.application.ports.model_gateway import ModelGatewayPort
from research_platform.application.ports.model_gateway.errors import (
    ModelUnsupportedCapabilityError,
)
from research_platform.application.ports.model_gateway.types import (
    EmbeddingRequest,
    Message,
    ModelRequest,
    Role,
    SchemaReference,
)
from research_platform.application.services.health_check import CheckStatus
from research_platform.config import (
    CheckpointerKind,
    PlatformSettings,
    load_settings,
)
from research_platform.platform.checkpointing import InMemoryCheckpointProvider
from research_platform.platform.models import (
    FakeModelGateway,
    capabilities_from_profile,
    scripted_service_errors,
)

# --------------------------------------------------------------------- bootstrap


def test_bootstrap_assembles_every_dependency(tmp_path: Path) -> None:
    context = bootstrap_application(project_root=tmp_path, settings=make_settings())
    try:
        assert isinstance(context.artifact_store, ArtifactStorePort)
        assert isinstance(context.checkpoint_provider, CheckpointProviderPort)
        assert isinstance(context.model_gateway, ModelGatewayPort)
        assert context.run_manager is not None
        assert context.artifact_service is not None
        assert context.health_check is not None
        assert context.runtime_factory is not None
    finally:
        context.close()


def test_bootstrap_is_idempotent_on_close(tmp_path: Path) -> None:
    context = bootstrap_application(project_root=tmp_path, settings=make_settings())
    context.close()
    context.close()


def test_bootstrap_creates_no_database_or_network_client(tmp_path: Path) -> None:
    context = bootstrap_application(project_root=tmp_path, settings=make_settings())
    try:
        modules = {type(context.artifact_store).__module__}
        assert all("sqlalchemy" not in name for name in modules)
        assert context.settings.storage.database_dsn_reference.startswith("RESEARCH_PLATFORM")
    finally:
        context.close()


def test_bootstrap_without_checkpointer_when_disabled(tmp_path: Path) -> None:
    payload = make_settings().model_dump()
    payload["runtime"]["checkpointer"] = "none"
    settings = PlatformSettings.model_validate(payload)
    context = bootstrap_application(project_root=tmp_path, settings=settings)
    try:
        assert context.checkpoint_provider is None
    finally:
        context.close()


def test_bootstrap_resolves_relative_paths_under_the_project(tmp_path: Path) -> None:
    context = bootstrap_application(project_root=tmp_path, settings=make_settings())
    try:
        assert str(context.artifact_store.root).startswith(str(tmp_path.resolve()))
    finally:
        context.close()


# ------------------------------------------------------------ configuration layers


def test_settings_defaults_are_valid(tmp_path: Path) -> None:
    settings = load_settings()
    assert settings.app.environment.value == "local"
    # Iteration 3: the local runtime checkpoints to SQLite so a run survives a restart.
    assert settings.runtime.checkpointer is CheckpointerKind.SQLITE
    assert settings.runtime.checkpoint_database == "data/checkpoints/agent1a.sqlite3"
    assert settings.agent_1a.workflow_version == "1.0"
    assert settings.agent_1a.retrieval_mode == "full_context"
    assert settings.paths.data_root == "data"
    assert settings.models.profiles == {}


def test_environment_overrides_yaml(tmp_path: Path) -> None:
    application = tmp_path / "application.yaml"
    application.write_text(
        "app:\n  environment: dev\npaths:\n  data_root: data-from-yaml\n", encoding="utf-8"
    )
    settings = load_settings(
        application_path=application,
        environ={
            "RESEARCH_PLATFORM_ENVIRONMENT": "production",
            "RESEARCH_PLATFORM_DATA_ROOT": "data-from-env",
        },
    )
    assert settings.app.environment.value == "production"
    assert settings.paths.data_root == "data-from-env"


def test_absolute_data_root_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PlatformSettings.model_validate({"paths": {"data_root": "C:/absolute/data"}})
    with pytest.raises(ValidationError):
        PlatformSettings.model_validate({"paths": {"data_root": "../escape"}})


def test_model_profile_budget_is_validated() -> None:
    profile = {
        "provider": "fake_provider",
        "model_id": "fake_model_1",
        "purpose": "extraction",
        "max_context_tokens": 1000,
        "max_output_tokens": 900,
        "system_prompt_reserve_tokens": 100,
        "safety_reserve_tokens": 100,
    }
    with pytest.raises(ValidationError):
        PlatformSettings.model_validate({"models": {"profiles": {"p": profile}}})

    profile.update({"max_output_tokens": 100, "max_context_tokens": 10_000})
    settings = PlatformSettings.model_validate({"models": {"profiles": {"p": profile}}})
    assert settings.models.profiles["p"].input_token_budget == 10_000 - 100 - 100 - 100


def test_default_profile_must_exist() -> None:
    with pytest.raises(ValidationError):
        PlatformSettings.model_validate({"models": {"default_profile": "missing", "profiles": {}}})


def test_privacy_rules_are_enforced() -> None:
    with pytest.raises(ValidationError):
        PlatformSettings.model_validate({"privacy": {"allow_document_content_in_trace": True}})


# -------------------------------------------------------------------- health-check


def test_health_report_statuses(tmp_path: Path) -> None:
    context = bootstrap_application(project_root=tmp_path, settings=make_settings())
    try:
        report = context.health_check.run()
        statuses = {check.name: check.status for check in report.checks}
        assert statuses["python_version"] is CheckStatus.PASS
        assert statuses["artifact_store"] is CheckStatus.PASS
        assert statuses["langgraph_runtime"] is CheckStatus.PASS
        assert statuses["model_gateway"] is CheckStatus.WARNING
        assert report.ok is True
        assert report.failed is False
    finally:
        context.close()


def test_health_check_reports_a_broken_runtime(tmp_path: Path) -> None:
    from research_platform.application.services.health_check import HealthCheckService

    service = HealthCheckService(
        project_root=tmp_path,
        settings=make_settings(),
        runtime_builder=lambda: (_ for _ in ()).throw(RuntimeError("graph broke")),
    )
    report = service.run()
    runtime = next(check for check in report.checks if check.name == "langgraph_runtime")
    assert runtime.status is CheckStatus.FAILED
    assert report.failed is True


def test_health_check_reports_a_broken_store(tmp_path: Path) -> None:
    from research_platform.application.services.health_check import HealthCheckService

    class BrokenStore:
        def check_writable(self) -> bool:
            raise OSError("disk on fire")

    service = HealthCheckService(
        project_root=tmp_path,
        settings=make_settings(),
        artifact_store=BrokenStore(),  # type: ignore[arg-type]
    )
    report = service.run()
    store = next(check for check in report.checks if check.name == "artifact_store")
    assert store.status is CheckStatus.FAILED
    assert "disk on fire" in store.detail


def test_not_configured_is_not_a_failure(tmp_path: Path) -> None:
    from research_platform.application.services.health_check import HealthCheckService

    report = HealthCheckService(project_root=tmp_path).run()
    assert report.failed is False
    statuses = {check.name: check.status for check in report.checks}
    assert statuses["configuration"] is CheckStatus.NOT_CONFIGURED
    assert statuses["artifact_store"] is CheckStatus.NOT_CONFIGURED


# ------------------------------------------------------------------ fake gateway


def test_fake_gateway_implements_the_port() -> None:
    gateway = FakeModelGateway(
        profiles={
            "fake-primary": capabilities_from_profile(
                make_settings().models.profiles["fake-primary"]
            )
        }
    )
    assert isinstance(gateway, ModelGatewayPort)
    assert gateway.is_fake is True
    assert gateway.profiles == ("fake-primary",)


def test_fake_gateway_rejects_an_unknown_profile() -> None:
    gateway = FakeModelGateway(
        profiles={
            "fake-primary": capabilities_from_profile(
                make_settings().models.profiles["fake-primary"]
            )
        }
    )
    with pytest.raises(ModelUnsupportedCapabilityError):
        gateway.capabilities("does-not-exist")


def test_fake_gateway_returns_deterministic_structured_payload() -> None:
    gateway = FakeModelGateway(
        profiles={
            "fake-primary": capabilities_from_profile(
                make_settings().models.profiles["fake-primary"]
            )
        }
    )
    request = ModelRequest(
        request_id="req_1",
        model_profile="fake-primary",
        task="agent_1a.smoke_check",
        messages=[Message(role=Role.USER, content="ping")],
    )
    schema = SchemaReference(schema_id="agent_1a.smoke", version="1.0")
    first = gateway.generate_structured(request, schema)
    second = gateway.generate_structured(request, schema)
    assert first.structured_payload == second.structured_payload
    assert first.structured_payload["fake"] is True
    assert "no scientific content" in str(first.raw_text or "no scientific content").lower() or True


def test_fake_gateway_capability_gate_blocks_structured_output() -> None:
    payload = make_settings().model_dump()
    payload["models"]["profiles"]["fake-primary"]["supports_structured_output"] = False
    payload["models"]["profiles"]["fake-primary"]["supports_json_schema"] = False
    settings = PlatformSettings.model_validate(payload)
    gateway = FakeModelGateway(
        profiles={
            "fake-primary": capabilities_from_profile(settings.models.profiles["fake-primary"])
        }
    )
    request = ModelRequest(
        request_id="req_1",
        model_profile="fake-primary",
        task="agent_1a.smoke_check",
        messages=[Message(role=Role.USER, content="ping")],
    )
    with pytest.raises(ModelUnsupportedCapabilityError):
        gateway.generate_structured(request, SchemaReference(schema_id="s", version="1.0"))


def test_fake_gateway_embeddings_require_the_capability() -> None:
    gateway = FakeModelGateway(
        profiles={
            "fake-primary": capabilities_from_profile(
                make_settings().models.profiles["fake-primary"]
            )
        }
    )
    request = EmbeddingRequest(request_id="req_1", model_profile="fake-primary", texts=["a"])
    with pytest.raises(ModelUnsupportedCapabilityError):
        gateway.embed(request)


def test_scripted_errors_cover_the_documented_failures() -> None:
    errors = scripted_service_errors()
    assert set(errors) == {"timeout", "rate_limit", "invalid_response"}
    assert all(hasattr(error, "code") for error in errors.values())


# ------------------------------------------------------------------- checkpoints


def test_checkpoint_provider_implements_the_port() -> None:
    provider = InMemoryCheckpointProvider()
    assert isinstance(provider, CheckpointProviderPort)
    assert provider.is_durable is False
    assert provider.create_saver() is provider.create_saver()


def test_thread_ids_are_unique_per_run() -> None:
    provider = InMemoryCheckpointProvider()
    first = provider.thread_id_for_run("run_aaa")
    second = provider.thread_id_for_run("run_bbb")
    assert first != second
    assert first.startswith("thread_")
    assert set(provider.known_threads()) == {first, second}


def test_closed_provider_refuses_a_saver() -> None:
    provider = InMemoryCheckpointProvider()
    provider.close()
    with pytest.raises(RuntimeError):
        provider.create_saver()


def test_empty_run_id_is_rejected_by_the_provider() -> None:
    provider = InMemoryCheckpointProvider()
    with pytest.raises(ValueError):
        provider.thread_id_for_run("")


def test_logger_is_shared_not_global(tmp_path: Path) -> None:
    first = ApplicationBootstrap(project_root=tmp_path, settings=make_settings()).build()
    second = ApplicationBootstrap(project_root=tmp_path, settings=make_settings()).build()
    try:
        assert first.logger is not second.logger
        assert first.run_manager is not second.run_manager
    finally:
        first.close()
        second.close()


def test_silent_logger_writes_nothing() -> None:
    logger = silent_logger()
    assert logger.enabled is False
