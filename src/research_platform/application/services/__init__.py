"""Application services.

Each service takes its collaborators through the constructor, talks to infrastructure only
through ports, returns typed results and raises the platform's normalised errors.  No
service imports ``platform``, and no service is a global singleton.
"""

from __future__ import annotations

from research_platform.application.services.artifact_service import ArtifactService
from research_platform.application.services.configuration_service import (
    ConfigurationService,
    ConfigValidationResult,
)
from research_platform.application.services.health_check import (
    CheckStatus,
    HealthCheckService,
    HealthReport,
)
from research_platform.application.services.run_manager import (
    RunManager,
    RunManagerError,
    StartRunRequest,
)

__all__ = [
    "ArtifactService",
    "CheckStatus",
    "ConfigValidationResult",
    "ConfigurationService",
    "HealthCheckService",
    "HealthReport",
    "RunManager",
    "RunManagerError",
    "StartRunRequest",
]
