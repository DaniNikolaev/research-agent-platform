"""Typed configuration contract.

Configuration is layered, later sources win:

1. defaults declared on the models below;
2. ``config/application.yaml`` and ``config/models.yaml`` (optional);
3. environment variables listed in :data:`ENVIRONMENT_OVERRIDES`.

Secrets are never configuration values — only the *name* of an environment variable that
holds a secret (``api_key_env``, ``database_dsn_reference``).  Physical paths are relative
and are resolved by the infrastructure layer, so the same configuration works on Windows
and Linux.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final

import yaml
from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import ContractModel, NonEmptyStr, ShortStr

__all__ = [
    "ENVIRONMENT_OVERRIDES",
    "AppSettings",
    "CheckpointerKind",
    "Environment",
    "LogFormat",
    "LogLevel",
    "ModelProfileSettings",
    "ModelsSettings",
    "ObservabilitySettings",
    "PathsSettings",
    "PlatformSettings",
    "PrivacySettings",
    "RuntimeSettings",
    "SettingsError",
    "StorageSettings",
    "WorkflowSettings",
    "discover_settings_paths",
    "load_settings",
    "load_settings_from_project",
]


class SettingsError(RuntimeError):
    """Configuration file is missing, unreadable or not a mapping."""


class Environment(StrEnum):
    """Deployment environment of the application."""

    LOCAL = "local"
    DEV = "dev"
    CI = "ci"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Allowed log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogFormat(StrEnum):
    """Structured log encoding."""

    JSONL = "jsonl"
    TEXT = "text"


class CheckpointerKind(StrEnum):
    """Which LangGraph checkpointer the runtime uses."""

    IN_MEMORY = "in_memory"
    """Test/local only: state disappears with the process."""

    NONE = "none"
    """No checkpointing (stateless execution)."""


ENV_VAR_NAME = r"^[A-Z][A-Z0-9_]{2,63}$"
EnvVarName = Annotated[str, StringConstraints(pattern=ENV_VAR_NAME)]
"""Name of an environment variable; the value itself is never part of the config."""

URI_PATTERN = r"^[a-z][a-z0-9+.\-]*://[A-Za-z0-9_\-./]+$"
LogicalUri = Annotated[str, StringConstraints(pattern=URI_PATTERN, max_length=512)]

RelativePath = Annotated[str, StringConstraints(min_length=1, max_length=256)]

ENVIRONMENT_OVERRIDES: Final[dict[str, tuple[str, ...]]] = {
    "RESEARCH_PLATFORM_ENVIRONMENT": ("app", "environment"),
    "RESEARCH_PLATFORM_LOG_LEVEL": ("app", "log_level"),
    "RESEARCH_PLATFORM_ARTIFACT_ROOT": ("storage", "artifact_root_uri"),
    "RESEARCH_PLATFORM_DB_DSN_REFERENCE": ("storage", "database_dsn_reference"),
    "RESEARCH_PLATFORM_DATA_ROOT": ("paths", "data_root"),
    "RESEARCH_PLATFORM_LOG_DIR": ("paths", "log_dir"),
}
"""Environment variables accepted as operational overrides (non-secret by design)."""


def _reject_unsafe_relative_path(value: str) -> str:
    if Path(value).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", value):
        raise ValueError(
            f"path {value!r} must be relative: physical locations are resolved against the "
            "project root by the infrastructure layer, so an absolute path would make the "
            "configuration machine-specific"
        )
    parts = re.split(r"[\\/]+", value)
    if any(part in {"..", ""} for part in parts):
        raise ValueError(f"path {value!r} must not contain '..' or empty segments (path traversal)")
    return value


class AppSettings(ContractModel):
    """Application identity and logging."""

    name: ShortStr = "research-agent-platform"
    environment: Environment = Environment.LOCAL
    log_level: LogLevel = LogLevel.INFO


class PathsSettings(ContractModel):
    """Relative physical roots owned by the infrastructure layer."""

    data_root: RelativePath = "data"
    artifact_subdir: RelativePath = "artifacts"
    checkpoint_subdir: RelativePath = "checkpoints"
    log_dir: RelativePath = "logs"

    @model_validator(mode="after")
    def _paths_are_relative(self) -> PathsSettings:
        for name in ("data_root", "artifact_subdir", "checkpoint_subdir", "log_dir"):
            _reject_unsafe_relative_path(getattr(self, name))
        return self


class RuntimeSettings(ContractModel):
    """LangGraph runtime knobs owned by the platform (not by an agent)."""

    checkpointer: CheckpointerKind = CheckpointerKind.IN_MEMORY
    max_graph_steps: Annotated[int, Field(ge=1, le=1_000)] = 50
    """Upper bound on graph super-steps; protects against an accidental loop."""

    @model_validator(mode="after")
    def _in_memory_is_not_durable(self) -> RuntimeSettings:
        # Kept as a validation hook: the only supported values today are explicit.
        return self


class StorageSettings(ContractModel):
    """Storage locations, expressed as references rather than credentials."""

    artifact_root_uri: LogicalUri = "artifact://local/artifacts"
    database_dsn_reference: ShortStr = "RESEARCH_PLATFORM_DB_DSN"

    @model_validator(mode="after")
    def _dsn_is_a_reference_not_a_dsn(self) -> StorageSettings:
        if "://" in self.database_dsn_reference or "@" in self.database_dsn_reference:
            raise ValueError(
                "storage.database_dsn_reference must be the NAME of an environment "
                "variable, not a DSN: a committed DSN would leak the password"
            )
        if not re.fullmatch(ENV_VAR_NAME, self.database_dsn_reference):
            raise ValueError(
                "storage.database_dsn_reference must look like an environment variable "
                "name, for example RESEARCH_PLATFORM_DB_DSN"
            )
        return self


class ModelProfileSettings(ContractModel):
    """One logical model profile.

    A profile declares what the provider *claims* about itself.  The values are inputs to
    the deterministic retrieval/context policy, never a guarantee: a real adapter must
    confirm them before the profile is used in production (``OI-001``…``OI-004``).
    """

    provider: ShortStr
    model_id: ShortStr
    purpose: Annotated[
        str,
        StringConstraints(
            pattern=r"^(extraction|planning|reconciliation|embedding|reranking|verification)$"
        ),
    ]
    api_key_env: EnvVarName | None = None
    base_url: Annotated[
        str | None, StringConstraints(pattern=r"^https://[^\s]+$", max_length=512)
    ] = None
    max_context_tokens: Annotated[int, Field(gt=0)] = 32_000
    max_output_tokens: Annotated[int, Field(gt=0)] = 8_000
    supports_structured_output: bool = False
    supports_json_schema: bool = False
    supports_tool_calls: bool = False
    supports_streaming: bool = False
    supports_embeddings: bool = False
    supports_reranking: bool = False
    timeout_seconds: Annotated[float, Field(gt=0.0, le=3600.0)] = 120.0
    max_retries: Annotated[int, Field(ge=0, le=10)] = 2
    system_prompt_reserve_tokens: Annotated[int, Field(ge=0)] = 2_000
    safety_reserve_tokens: Annotated[int, Field(ge=0)] = 2_000
    is_fake: bool = False
    """True for the local fake profile; the platform refuses to treat it as production."""

    notes: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _json_schema_implies_structured_output(self) -> ModelProfileSettings:
        if self.supports_json_schema and not self.supports_structured_output:
            raise ValueError(
                f"profile {self.model_id!r}: supports_json_schema=True requires "
                "supports_structured_output=True"
            )
        return self

    @model_validator(mode="after")
    def _token_budget_is_positive(self) -> ModelProfileSettings:
        if self.max_output_tokens >= self.max_context_tokens:
            raise ValueError(
                f"profile {self.model_id!r}: max_output_tokens must be smaller than "
                "max_context_tokens"
            )
        if self.input_token_budget <= 0:
            raise ValueError(
                f"profile {self.model_id!r}: reserves leave no room for input "
                "(max_context_tokens - max_output_tokens - system_prompt - safety <= 0)"
            )
        return self

    @property
    def input_token_budget(self) -> int:
        """Tokens available for document context after all reserves."""
        return (
            self.max_context_tokens
            - self.max_output_tokens
            - self.system_prompt_reserve_tokens
            - self.safety_reserve_tokens
        )


class ModelsSettings(ContractModel):
    """Named model profiles."""

    profiles: Annotated[dict[ShortStr, ModelProfileSettings], Field(max_length=32)] = Field(
        default_factory=dict
    )
    default_profile: ShortStr | None = None

    @model_validator(mode="after")
    def _default_profile_exists(self) -> ModelsSettings:
        if self.default_profile is None:
            return self
        if self.default_profile not in self.profiles:
            raise ValueError(
                f"models.default_profile {self.default_profile!r} is not defined in "
                f"profiles: {sorted(self.profiles)}"
            )
        return self

    def purposes(self) -> set[str]:
        """Distinct purposes declared by the configured profiles."""
        return {profile.purpose for profile in self.profiles.values()}

    def first_profile_for(self, purpose: str) -> str | None:
        """Return the first profile name declared for a purpose, if any."""
        for name, profile in self.profiles.items():
            if profile.purpose == purpose:
                return name
        return None


class WorkflowSettings(ContractModel):
    """Retry budgets and timeouts owned by the workflow, not by adapters."""

    max_retry_parsing: Annotated[int, Field(ge=0, le=10)] = 1
    max_retry_extraction: Annotated[int, Field(ge=0, le=10)] = 2
    max_retry_evidence: Annotated[int, Field(ge=0, le=10)] = 2
    max_retry_validation: Annotated[int, Field(ge=0, le=10)] = 2
    node_timeout_seconds: Annotated[float, Field(gt=0.0, le=7200.0)] = 600.0
    max_quality_gate_reparses: Annotated[int, Field(ge=0, le=5)] = 1

    @property
    def retry_limits(self) -> dict[str, int]:
        """Retry limits keyed by ``Agent1AState.retry_counters`` field name."""
        return {
            "parsing": self.max_retry_parsing,
            "extraction": self.max_retry_extraction,
            "evidence": self.max_retry_evidence,
            "validation": self.max_retry_validation,
        }


class PrivacySettings(ContractModel):
    """What may leave the local machine for tracing."""

    allow_prompt_content_in_trace: bool = False
    allow_document_content_in_trace: bool = False

    @model_validator(mode="after")
    def _document_content_implies_prompt_content(self) -> PrivacySettings:
        if self.allow_document_content_in_trace and not self.allow_prompt_content_in_trace:
            raise ValueError(
                "document content cannot be exported to tracing while prompt content is "
                "withheld; document text is the more sensitive artefact"
            )
        return self


class ObservabilitySettings(ContractModel):
    """Local-first observability."""

    enabled: bool = False
    log_format: LogFormat = LogFormat.JSONL
    exporter: Annotated[str, StringConstraints(pattern=r"^(none|console|otlp)$")] = "none"
    service_name: ShortStr = "research-agent-platform"
    log_retention_days: Annotated[int, Field(ge=1, le=3650)] = 30


class PlatformSettings(ContractModel):
    """Root configuration object."""

    app: AppSettings = Field(default_factory=AppSettings)
    paths: PathsSettings = Field(default_factory=PathsSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    models: ModelsSettings = Field(default_factory=ModelsSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SettingsError(f"configuration file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise SettingsError(f"configuration file is not valid YAML: {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise SettingsError(f"configuration file must contain a mapping: {path}")
    return loaded


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _apply_environment(data: dict[str, Any], environ: Mapping[str, str]) -> dict[str, Any]:
    updated = dict(data)
    for variable, path in ENVIRONMENT_OVERRIDES.items():
        raw = environ.get(variable)
        if raw is None or raw == "":
            continue
        node = updated
        for part in path[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[path[-1]] = raw
    return updated


def discover_settings_paths(root: Path) -> tuple[Path | None, Path | None]:
    """Return ``(application.yaml, models.yaml)`` if they exist under ``config/``."""
    config_dir = root / "config"
    application = config_dir / "application.yaml"
    models = config_dir / "models.yaml"
    return (
        application if application.exists() else None,
        models if models.exists() else None,
    )


def load_settings(
    *,
    application_path: Path | None = None,
    models_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> PlatformSettings:
    """Load and validate platform settings.

    Args:
        application_path: YAML file with the platform sections.
        models_path: YAML file with the ``models`` section.
        environ: Environment mapping used for the documented overrides.

    Raises:
        SettingsError: a file is missing or is not a YAML mapping.
        pydantic.ValidationError: the configuration violates the contract.
    """
    data: dict[str, Any] = {}
    if application_path is not None:
        data = _deep_merge(data, _read_yaml(application_path))
    if models_path is not None:
        data = _deep_merge(data, _read_yaml(models_path))
    data = _apply_environment(data, environ or {})
    return PlatformSettings.model_validate(data)


def load_settings_from_project(
    root: Path, *, environ: Mapping[str, str] | None = None
) -> PlatformSettings:
    """Load settings from ``<root>/config`` (falls back to pure defaults)."""
    application, models = discover_settings_paths(root)
    return load_settings(application_path=application, models_path=models, environ=environ)
