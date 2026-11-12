"""Configuration service: load, validate and describe platform settings.

It wraps :mod:`research_platform.config` with the *policy* the platform needs:

* settings are loaded from a project root (``config/application.yaml`` +
  ``config/models.yaml``) with environment overrides;
* validation errors are turned into a typed result instead of a stack trace, so a CLI can
  print an actionable diagnostic and return the documented exit code;
* secret-looking values are detected in the raw YAML files, because a YAML file is exactly
  where a key must never be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from research_platform.application.ports.logging import CONFIG_LOADED, LoggerPort
from research_platform.application.services.redaction import Redactor
from research_platform.config import (
    PlatformSettings,
    SettingsError,
    discover_settings_paths,
    load_settings,
)

__all__ = ["ConfigIssue", "ConfigValidationResult", "ConfigurationService"]


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """One configuration problem, with the path to the offending parameter."""

    path: str
    message: str

    def render(self) -> str:
        """Human-readable one-liner."""
        return f"{self.path}: {self.message}"


@dataclass(slots=True)
class ConfigValidationResult:
    """Outcome of a configuration validation."""

    settings: PlatformSettings | None = None
    issues: list[ConfigIssue] = field(default_factory=list)
    secret_findings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the configuration is valid and free of secret values."""
        return self.settings is not None and not self.issues and not self.secret_findings


class ConfigurationService:
    """Load and validate configuration for one project root."""

    def __init__(
        self,
        *,
        project_root: Path,
        logger: LoggerPort,
        environ: dict[str, str] | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._project_root = project_root
        self._logger = logger
        self._environ = environ
        self._redactor = redactor or Redactor()

    @property
    def project_root(self) -> Path:
        """Root directory the configuration is resolved against."""
        return self._project_root

    def paths(self) -> tuple[Path | None, Path | None]:
        """``(application.yaml, models.yaml)`` paths that exist under the project root."""
        return discover_settings_paths(self._project_root)

    def load(self) -> ConfigValidationResult:
        """Load and validate settings from the project's ``config/`` directory."""
        application, models = self.paths()
        return self.load_from(application_path=application, models_path=models)

    def load_from(
        self,
        *,
        application_path: Path | None,
        models_path: Path | None,
    ) -> ConfigValidationResult:
        """Load and validate settings from explicit paths, collecting every problem."""
        sources = [str(path) for path in (application_path, models_path) if path is not None]
        result = ConfigValidationResult(sources=sources)
        try:
            settings = load_settings(
                application_path=application_path,
                models_path=models_path,
                environ=self._environ,
            )
        except SettingsError as exc:
            result.issues.append(ConfigIssue(path="config", message=str(exc)))
            self._logger.event(
                "config_validation_failed", error_code="settings_error", detail=str(exc)
            )
            return result
        except ValidationError as exc:
            for error in exc.errors():
                path = ".".join(str(part) for part in error["loc"]) or "<root>"
                result.issues.append(ConfigIssue(path=path, message=error["msg"]))
            self._logger.event(
                "config_validation_failed",
                error_code="validation_error",
                issue_count=len(result.issues),
            )
            return result

        result.settings = settings
        result.secret_findings.extend(self._scan_for_secrets(application_path, models_path))
        self._logger.event(
            CONFIG_LOADED,
            environment=settings.app.environment.value,
            sources=len(sources),
            profiles=len(settings.models.profiles),
        )
        return result

    def require_valid(self) -> PlatformSettings:
        """Return settings or raise with a diagnostic listing every issue."""
        result = self.load()
        if result.settings is None or result.issues or result.secret_findings:
            details = [issue.render() for issue in result.issues]
            details.extend(result.secret_findings)
            raise SettingsError("invalid configuration: " + "; ".join(details))
        return result.settings

    # ------------------------------------------------------------------ secrets

    def _scan_for_secrets(self, *paths: Path | None) -> list[str]:
        findings: list[str] = []
        for path in paths:
            if path is None or not path.exists():
                continue
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                continue
            findings.extend(self._walk(loaded, prefix=path.name))
        return findings

    def _walk(self, node: Any, *, prefix: str) -> list[str]:
        findings: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{prefix}.{key}"
                literal_secret = (
                    self._redactor.is_secret_key(str(key))
                    and not _is_env_reference(str(key))
                    and isinstance(value, str)
                    and bool(value.strip())
                )
                if literal_secret:
                    findings.append(
                        f"{path}: a secret-looking key has a literal value; use an "
                        "environment variable reference instead"
                    )
                findings.extend(self._walk(value, prefix=path))
        elif isinstance(node, list):
            for index, item in enumerate(node):
                findings.extend(self._walk(item, prefix=f"{prefix}[{index}]"))
        return findings


def _is_env_reference(key: str) -> bool:
    """Keys that intentionally hold the *name* of an environment variable."""
    return key.lower() in {"api_key_env", "database_dsn_reference", "dsn_reference"}
