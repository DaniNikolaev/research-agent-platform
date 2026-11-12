"""Platform health checks.

A health check answers "can this build do its job right now?" without touching anything the
iteration does not own.  It never contacts PostgreSQL or a real LLM endpoint — those are out
of scope — and it never creates a run.

Each check reports one of four statuses so that "not configured" is visibly different from
"broken": a platform that runs on defaults is not failing, it is simply unconfigured.
"""

from __future__ import annotations

import importlib
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from research_platform import __version__
from research_platform.application.ports.artifact_store import ArtifactStorePort
from research_platform.application.ports.model_gateway import ModelGatewayPort
from research_platform.config import PlatformSettings

__all__ = ["CheckStatus", "HealthCheck", "HealthCheckService", "HealthReport"]


class CheckStatus(StrEnum):
    """Outcome of a single platform check."""

    PASS = "PASS"
    WARNING = "WARNING"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One check result."""

    name: str
    status: CheckStatus
    detail: str

    def render(self) -> str:
        """Aligned one-line rendering for the CLI."""
        return f"{self.name:<24} {self.status.value:<15} {self.detail}"


@dataclass(slots=True)
class HealthReport:
    """Aggregated platform health."""

    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        """Whether any check failed."""
        return any(check.status is CheckStatus.FAILED for check in self.checks)

    @property
    def warnings(self) -> list[HealthCheck]:
        """Checks that passed with a warning."""
        return [check for check in self.checks if check.status is CheckStatus.WARNING]

    @property
    def ok(self) -> bool:
        """Whether the platform is usable (no failed checks)."""
        return not self.failed

    def add(self, name: str, status: CheckStatus, detail: str) -> None:
        """Append a check result."""
        self.checks.append(HealthCheck(name=name, status=status, detail=detail))


class HealthCheckService:
    """Run the platform's self-checks against already-built dependencies."""

    def __init__(
        self,
        *,
        project_root: Path,
        settings: PlatformSettings | None = None,
        artifact_store: ArtifactStorePort | None = None,
        model_gateway: ModelGatewayPort | None = None,
        runtime_builder: Callable[[], Any] | None = None,
        expected_python: tuple[int, int] = (3, 13),
        required_imports: tuple[str, ...] = (
            "research_platform.domain.runs.models",
            "research_platform.agents.agent_1a.contracts.graph_state",
            "research_platform.application.services.run_manager",
            "research_platform.platform.observability.logging",
            "langgraph",
        ),
    ) -> None:
        self._project_root = project_root
        self._settings = settings
        self._artifact_store = artifact_store
        self._model_gateway = model_gateway
        self._runtime_builder = runtime_builder
        self._expected_python = expected_python
        self._required_imports = required_imports

    def run(self) -> HealthReport:
        """Execute every check and return the aggregated report."""
        report = HealthReport()
        self._check_python(report)
        self._check_package(report)
        self._check_configuration(report)
        self._check_directories(report)
        self._check_artifact_store(report)
        self._check_model_gateway(report)
        self._check_runtime(report)
        self._check_imports(report)
        return report

    # ------------------------------------------------------------------ checks

    def _check_python(self, report: HealthReport) -> None:
        version = ".".join(str(part) for part in sys.version_info[:3])
        expected = ".".join(str(part) for part in self._expected_python)
        if sys.version_info[:2] == self._expected_python:
            report.add("python_version", CheckStatus.PASS, version)
        else:
            # The package requires >=3.13,<3.14, so a different minor version is a warning
            # about an unverified baseline rather than a failure.
            report.add(
                "python_version",
                CheckStatus.WARNING,
                f"{version} (verified baseline is {expected}.x)",
            )

    def _check_package(self, report: HealthReport) -> None:
        package = importlib.import_module("research_platform")
        version = getattr(package, "__version__", __version__)
        report.add("package_version", CheckStatus.PASS, str(version))

    def _check_configuration(self, report: HealthReport) -> None:
        config_dir = self._project_root / "config"
        has_files = any(
            (config_dir / name).exists() for name in ("application.yaml", "models.yaml")
        )
        if self._settings is None:
            report.add(
                "configuration",
                CheckStatus.NOT_CONFIGURED,
                "no settings were supplied to the check",
            )
        elif not has_files:
            report.add(
                "configuration",
                CheckStatus.NOT_CONFIGURED,
                "no config/application.yaml or config/models.yaml; using built-in defaults",
            )
        else:
            profiles = ", ".join(sorted(self._settings.models.profiles)) or "none"
            report.add(
                "configuration",
                CheckStatus.PASS,
                f"environment={self._settings.app.environment.value}, profiles={profiles}",
            )

    def _check_directories(self, report: HealthReport) -> None:
        if self._settings is None:
            report.add("data_directories", CheckStatus.NOT_CONFIGURED, "no settings to resolve")
            return
        paths = self._settings.paths
        targets = {
            "data_root": self._project_root / paths.data_root,
            "log_dir": self._project_root / paths.log_dir,
        }
        problems: list[str] = []
        for name, target in targets.items():
            try:
                target.mkdir(parents=True, exist_ok=True)
                probe = target / ".health-probe"
                probe.write_text("probe", encoding="utf-8")
                probe.unlink()
            except OSError as exc:
                problems.append(f"{name}={target}: {exc}")
        if problems:
            report.add("data_directories", CheckStatus.FAILED, "; ".join(problems))
        else:
            report.add(
                "data_directories",
                CheckStatus.PASS,
                ", ".join(f"{name}={target}" for name, target in targets.items()),
            )

    def _check_artifact_store(self, report: HealthReport) -> None:
        if self._artifact_store is None:
            report.add("artifact_store", CheckStatus.NOT_CONFIGURED, "no store configured")
            return
        try:
            writable = bool(self._artifact_store.check_writable())
        except Exception as exc:
            report.add("artifact_store", CheckStatus.FAILED, f"{type(exc).__name__}: {exc}")
            return
        status = CheckStatus.PASS if writable else CheckStatus.FAILED
        report.add(
            "artifact_store",
            status,
            "local file store is writable" if writable else "store reports it is not writable",
        )

    def _check_model_gateway(self, report: HealthReport) -> None:
        if self._model_gateway is None:
            report.add("model_gateway", CheckStatus.NOT_CONFIGURED, "no model gateway configured")
            return
        if self._settings is None or not self._settings.models.profiles:
            report.add("model_gateway", CheckStatus.NOT_CONFIGURED, "no model profiles configured")
            return
        profile_name = self._settings.models.default_profile or next(
            iter(sorted(self._settings.models.profiles))
        )
        try:
            capabilities = self._model_gateway.capabilities(profile_name)
        except Exception as exc:
            report.add("model_gateway", CheckStatus.FAILED, f"{type(exc).__name__}: {exc}")
            return
        profile = self._settings.models.profiles[profile_name]
        if profile.is_fake:
            report.add(
                "model_gateway",
                CheckStatus.WARNING,
                f"profile {profile_name!r} is a FAKE adapter "
                f"({capabilities.provider_id}/{capabilities.model_id}); no real provider",
            )
        else:
            report.add(
                "model_gateway",
                CheckStatus.PASS,
                f"profile {profile_name!r} ({capabilities.provider_id}/{capabilities.model_id})",
            )

    def _check_runtime(self, report: HealthReport) -> None:
        if self._runtime_builder is None:
            report.add(
                "langgraph_runtime", CheckStatus.NOT_CONFIGURED, "no runtime factory configured"
            )
            return
        try:
            bundle = self._runtime_builder()
        except Exception as exc:
            report.add("langgraph_runtime", CheckStatus.FAILED, f"{type(exc).__name__}: {exc}")
            return
        graph = getattr(bundle, "graph", bundle)
        compiled = type(graph).__name__
        report.add("langgraph_runtime", CheckStatus.PASS, f"graph compiled ({compiled})")

    def _check_imports(self, report: HealthReport) -> None:
        missing: list[str] = []
        for module in self._required_imports:
            try:
                importlib.import_module(module)
            except Exception as exc:
                missing.append(f"{module} ({type(exc).__name__})")
        if missing:
            report.add("imports", CheckStatus.FAILED, "cannot import: " + ", ".join(missing))
        else:
            report.add(
                "imports", CheckStatus.PASS, f"{len(self._required_imports)} modules importable"
            )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def python_banner() -> str:
        """Short interpreter description used by ``--version``."""
        return f"{platform.python_implementation()} {platform.python_version()}"
