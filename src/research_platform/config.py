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
from typing import TYPE_CHECKING, Annotated, Any, Final

import yaml
from pydantic import Field, StringConstraints, model_validator

from research_platform.domain.base import ContractModel, NonEmptyStr, ShortStr

if TYPE_CHECKING:  # pragma: no cover - typing only
    from research_platform.domain.documents.quality import QualityPolicy

__all__ = [
    "ENVIRONMENT_OVERRIDES",
    "Agent1ASettings",
    "ChunkingSettings",
    "EmbeddingSettings",
    "RetrievalSettings",
    "AppSettings",
    "CheckpointerKind",
    "DoclingSettings",
    "DocumentIngestionSettings",
    "DocumentParsingSettings",
    "Environment",
    "LogFormat",
    "LogLevel",
    "ModelAssetsSettings",
    "ModelProfileSettings",
    "ModelsSettings",
    "ObservabilitySettings",
    "OcrSettings",
    "PathsSettings",
    "PlatformSettings",
    "PrivacySettings",
    "QualityGateSettings",
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

    SQLITE = "sqlite"
    """Durable local checkpointing (default for the local runtime)."""

    IN_MEMORY = "in_memory"
    """Test-only: state disappears with the process."""

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

    checkpointer: CheckpointerKind = CheckpointerKind.SQLITE
    checkpoint_database: RelativePath = "data/checkpoints/agent1a.sqlite3"
    """Relative path of the SQLite checkpoint database (outside version control)."""

    checkpoint_timeout_seconds: Annotated[float, Field(gt=0.0, le=600.0)] = 30.0
    max_graph_steps: Annotated[int, Field(ge=1, le=1_000)] = 50
    """Upper bound on graph super-steps; protects against an accidental loop."""

    @model_validator(mode="after")
    def _sqlite_needs_a_database_path(self) -> RuntimeSettings:
        if self.checkpointer is CheckpointerKind.SQLITE:
            _reject_unsafe_relative_path(self.checkpoint_database)
            if not self.checkpoint_database.endswith(".sqlite3"):
                raise ValueError("runtime.checkpoint_database must point to a .sqlite3 file")
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


class DocumentIngestionSettings(ContractModel):
    """Limits applied to a user-supplied source before anything is stored or parsed."""

    allowed_extensions: Annotated[
        list[Annotated[str, StringConstraints(pattern=r"^\.[a-z]+$")]],
        Field(min_length=1, max_length=8),
    ] = Field(default_factory=lambda: [".pdf", ".txt"])
    max_pdf_size_mb: Annotated[int, Field(gt=0, le=2000)] = 100
    max_txt_size_mb: Annotated[int, Field(gt=0, le=2000)] = 20
    max_pdf_pages: Annotated[int, Field(gt=0, le=20_000)] = 500
    calculate_sha256: bool = True
    verify_source_unchanged: bool = True

    @model_validator(mode="after")
    def _only_supported_extensions(self) -> DocumentIngestionSettings:
        unsupported = sorted(set(self.allowed_extensions) - {".pdf", ".txt"})
        if unsupported:
            raise ValueError(
                f"unsupported document extensions {unsupported}; this iteration accepts PDF "
                "and TXT only"
            )
        return self

    @property
    def max_size_bytes(self) -> dict[str, int]:
        """Per-extension size limit in bytes."""
        return {
            ".pdf": self.max_pdf_size_mb * 1024 * 1024,
            ".txt": self.max_txt_size_mb * 1024 * 1024,
        }


class ModelAssetsSettings(ContractModel):
    """Policy for parser model weights."""

    allow_unpinned: bool = False
    allow_auto_download: bool = False
    """Must stay false in this iteration: assets are downloaded only on explicit approval."""

    cache_dir: str | None = None
    """Optional explicit cache location; the environment default is used when null."""


class DoclingSettings(ContractModel):
    """Docling pipeline flags that change the extraction result."""

    do_ocr: bool = False
    do_table_structure: bool = True
    generate_picture_images: bool = False
    do_cell_matching: bool = True

    @model_validator(mode="after")
    def _ocr_requires_an_approved_engine(self) -> DoclingSettings:
        if self.do_ocr:
            raise ValueError(
                "docling.do_ocr must stay false: no OCR engine is approved by the owner "
                "(OI-008). Enable OCR only together with an 'ocr' section engine_id."
            )
        return self


class DocumentParsingSettings(ContractModel):
    """Which parser is primary, which is the fallback, and what is preserved."""

    default_pdf_parser: Annotated[str, StringConstraints(pattern=r"^(docling|pymupdf)$")] = (
        "docling"
    )
    fallback_pdf_parser: Annotated[str, StringConstraints(pattern=r"^(pymupdf|none)$")] = "pymupdf"
    docling: DoclingSettings = Field(default_factory=DoclingSettings)
    model_assets: ModelAssetsSettings = Field(default_factory=ModelAssetsSettings)
    preserve_page_provenance: bool = True
    preserve_bbox: bool = True
    preserve_reading_order: bool = True

    @model_validator(mode="after")
    def _fallback_differs_from_primary(self) -> DocumentParsingSettings:
        if self.fallback_pdf_parser == self.default_pdf_parser:
            raise ValueError(
                "fallback_pdf_parser must differ from default_pdf_parser; a fallback to the "
                "same parser reproduces the same failure"
            )
        return self


class QualityGateSettings(ContractModel):
    """Thresholds and switches of the Quality Gate (versioned policy)."""

    policy_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    fail_on_page_count_mismatch: bool = True
    require_page_provenance: bool = True
    warn_on_empty_pages: bool = True
    warn_on_low_text_density: bool = True
    detect_garbled_text: bool = True
    verify_character_ranges: bool = True
    verify_table_structure: bool = True
    minimum_meaningful_text_chars: Annotated[int, Field(ge=1, le=10_000)] = 50
    low_text_chars_per_page: Annotated[int, Field(ge=1, le=10_000)] = 120
    max_replacement_char_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.02
    max_control_char_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.01
    image_dominant_area_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    max_parser_warnings: Annotated[int, Field(ge=0, le=1000)] = 20

    def to_policy(self) -> QualityPolicy:
        """Build the domain :class:`QualityPolicy` from these settings."""
        from research_platform.domain.documents.quality import QualityPolicy

        return QualityPolicy(
            policy_version=self.policy_version,
            minimum_meaningful_text_chars=self.minimum_meaningful_text_chars,
            low_text_chars_per_page=self.low_text_chars_per_page,
            max_replacement_char_ratio=self.max_replacement_char_ratio,
            max_control_char_ratio=self.max_control_char_ratio,
            image_dominant_area_ratio=self.image_dominant_area_ratio,
            page_count_mismatch_is_critical=self.fail_on_page_count_mismatch,
            require_page_provenance=self.require_page_provenance,
            verify_character_ranges=self.verify_character_ranges,
            verify_table_structure=self.verify_table_structure,
            warn_on_empty_pages=self.warn_on_empty_pages,
            detect_garbled_text=self.detect_garbled_text,
            max_parser_warnings=self.max_parser_warnings,
        )


class OcrSettings(ContractModel):
    """OCR policy; no engine is selected in this iteration (OI-008)."""

    enabled: bool = False
    engine_id: ShortStr | None = None
    supported_languages: Annotated[list[ShortStr], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: ["ru", "en"]
    )

    @model_validator(mode="after")
    def _enabled_requires_an_engine(self) -> OcrSettings:
        if self.enabled and not self.engine_id:
            raise ValueError(
                "ocr.enabled=true requires ocr.engine_id; the engine is chosen by the owner "
                "(OI-008) and is not selected by the platform"
            )
        return self


class Agent1ASettings(ContractModel):
    """Agent 1A extraction workflow settings (iteration 3)."""

    workflow_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    graph_state_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    prompt_set_version: Annotated[str, Field(pattern=r"^\d+\.\d+$")] = "1.0"
    model_profile: ShortStr = "fake-primary"
    retrieval_mode: Annotated[str, Field(pattern=r"^full_context$")] = "full_context"
    max_repair_attempts: Annotated[int, Field(ge=0, le=5)] = 2
    """Targeted schema-repair attempts after the first invalid structured answer."""
    max_transient_attempts: Annotated[int, Field(ge=1, le=10)] = 3
    transient_backoff_seconds: Annotated[float, Field(ge=0.0, le=60.0)] = 0.0
    context_output_reserve_tokens: Annotated[int, Field(ge=0, le=200_000)] = 2048
    context_safety_reserve_tokens: Annotated[int, Field(ge=0, le=200_000)] = 512
    interrupt_after: Annotated[list[ShortStr], Field(max_length=4)] = Field(default_factory=list)
    """Technical breakpoints (node names) used to verify checkpoint/resume; empty in production."""

    fake_scenario: Annotated[
        str,
        StringConstraints(
            pattern=r"^(success|empty_result|invalid_schema_once|invalid_schema_always|"
            r"transient_error_once|transient_error_always|permanent_error|"
            r"interruption_scenario)$"
        ),
    ] = "success"

    @model_validator(mode="after")
    def _breakpoints_are_known_nodes(self) -> Agent1ASettings:
        from research_platform.workflows.agent1a_extraction_workflow import (
            INTERRUPTABLE_NODES,
        )

        unknown = sorted(set(self.interrupt_after) - set(INTERRUPTABLE_NODES))
        if unknown:
            raise ValueError(
                f"agent_1a.interrupt_after contains unknown nodes: {unknown}; "
                f"allowed: {list(INTERRUPTABLE_NODES)}"
            )
        return self

    @model_validator(mode="after")
    def _reserves_fit_the_profile_window(self) -> Agent1ASettings:
        if self.context_output_reserve_tokens <= 0:
            raise ValueError(
                "agent_1a.context_output_reserve_tokens must leave room for the structured answer"
            )
        return self


class ChunkingSettings(ContractModel):
    """Structural chunker parameters (provisional, subject to evaluation)."""

    target_tokens: Annotated[int, Field(ge=50, le=8000)] = 600
    max_tokens: Annotated[int, Field(ge=50, le=16000)] = 800
    overlap_tokens: Annotated[int, Field(ge=0, le=2000)] = 40
    min_tokens: Annotated[int, Field(ge=1, le=2000)] = 100
    chunker_version: Annotated[str, Field(pattern=r"^[a-z0-9\-]{3,32}$")] = "structural-v1"


class EmbeddingSettings(ContractModel):
    """Embedding profile selection; the fake adapter must be opted into explicitly."""

    profile_id: ShortStr | None = None
    """``None`` means: no embedding provider configured -> lexical_only retrieval."""

    execution_mode: Annotated[str, Field(pattern=r"^(real|simulation|not_configured)$")] = (
        "not_configured"
    )
    dimension: Annotated[int, Field(ge=8, le=4096)] = 64
    batch_size: Annotated[int, Field(ge=1, le=1024)] = 64

    @model_validator(mode="after")
    def _fake_needs_explicit_opt_in(self) -> EmbeddingSettings:
        if self.execution_mode == "simulation" and not self.profile_id:
            raise ValueError(
                "embedding simulation requires an explicit retrieval.embedding.profile_id"
            )
        return self


class RetrievalSettings(ContractModel):
    """Retrieval policy and ranking parameters (provisional)."""

    mode_policy: Annotated[str, Field(pattern=r"^(auto|full_context|hybrid|rag)$")] = "auto"
    lexical_top_k: Annotated[int, Field(ge=1, le=100)] = 8
    dense_top_k: Annotated[int, Field(ge=1, le=100)] = 8
    final_top_k: Annotated[int, Field(ge=1, le=50)] = 6
    rrf_k: Annotated[int, Field(ge=1, le=1000)] = 60
    profile_id: ShortStr = "default"
    fallback_scan: bool = True
    resolver_version: Annotated[str, Field(pattern=r"^[a-z0-9\-]{3,32}$")] = "evidence-resolver-v1"

    @model_validator(mode="after")
    def _top_k_order(self) -> RetrievalSettings:
        if self.final_top_k > max(self.lexical_top_k, self.dense_top_k):
            raise ValueError(
                "retrieval.final_top_k must not exceed the largest candidate pool"
            )
        return self

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
    document_ingestion: DocumentIngestionSettings = Field(default_factory=DocumentIngestionSettings)
    document_parsing: DocumentParsingSettings = Field(default_factory=DocumentParsingSettings)
    quality_gate: QualityGateSettings = Field(default_factory=QualityGateSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    agent_1a: Agent1ASettings = Field(default_factory=Agent1ASettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)

    @model_validator(mode="after")
    def _agent_profile_exists(self) -> PlatformSettings:
        # Only an explicitly configured profile is validated here: a configuration that never
        # mentions agent_1a keeps working with its defaults, while a configuration that names a
        # profile must actually define it.
        explicitly_configured = "model_profile" in self.agent_1a.model_fields_set
        if (
            explicitly_configured
            and self.models.profiles
            and self.agent_1a.model_profile not in self.models.profiles
        ):
            raise ValueError(
                f"agent_1a.model_profile {self.agent_1a.model_profile!r} is not defined in "
                f"models.profiles: {sorted(self.models.profiles)}"
            )
        return self


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
