"""Logging port and the closed set of platform event names.

Application services and graph nodes log through this port, so they never depend on the
concrete JSONL writer in ``platform.observability``.  The event vocabulary itself is part
of the contract: an event name that is not listed here is rejected by the implementation,
which keeps dashboards and tests from silently drifting.

Records carry identifiers (``run_id``, ``correlation_id``, ``document_id``, ``stage``) and
never payloads: no document text, no prompts, no credentials.
"""

from __future__ import annotations

from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "APPLICATION_SHUTDOWN",
    "APPLICATION_STARTED",
    "ARTIFACT_CREATED",
    "ARTIFACT_VALIDATION_FAILED",
    "CHECK_COMPLETED",
    "CONFIG_LOADED",
    "CONFIG_VALIDATION_FAILED",
    "EVENT_NAMES",
    "FAKE_MODEL_REQUEST_COMPLETED",
    "MODEL_REQUEST_COMPLETED",
    "MODEL_REQUEST_STARTED",
    "RETRY_STARTED",
    "RUNTIME_ERROR",
    "RUN_COMPLETED",
    "RUN_CREATED",
    "RUN_FAILED",
    "RUN_STARTED",
    "RUN_WAITING_FOR_HUMAN",
    "STAGE_COMPLETED",
    "STAGE_FAILED",
    "STAGE_STARTED",
    "LoggerPort",
]

APPLICATION_STARTED: Final = "application_started"
APPLICATION_SHUTDOWN: Final = "application_shutdown"
CONFIG_LOADED: Final = "config_loaded"
CONFIG_VALIDATION_FAILED: Final = "config_validation_failed"
CHECK_COMPLETED: Final = "check_completed"
RUN_CREATED: Final = "run_created"
RUN_STARTED: Final = "run_started"
STAGE_STARTED: Final = "stage_started"
STAGE_COMPLETED: Final = "stage_completed"
STAGE_FAILED: Final = "stage_failed"
MODEL_REQUEST_STARTED: Final = "model_request_started"
MODEL_REQUEST_COMPLETED: Final = "model_request_completed"
FAKE_MODEL_REQUEST_COMPLETED: Final = "fake_model_request_completed"
ARTIFACT_CREATED: Final = "artifact_created"
ARTIFACT_VALIDATION_FAILED: Final = "artifact_validation_failed"
RUN_WAITING_FOR_HUMAN: Final = "run_waiting_for_human"
RUN_COMPLETED: Final = "run_completed"
RUN_FAILED: Final = "run_failed"
RETRY_STARTED: Final = "retry_started"
RUNTIME_ERROR: Final = "runtime_error"

EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        APPLICATION_STARTED,
        APPLICATION_SHUTDOWN,
        CONFIG_LOADED,
        CONFIG_VALIDATION_FAILED,
        CHECK_COMPLETED,
        RUN_CREATED,
        RUN_STARTED,
        STAGE_STARTED,
        STAGE_COMPLETED,
        STAGE_FAILED,
        MODEL_REQUEST_STARTED,
        MODEL_REQUEST_COMPLETED,
        FAKE_MODEL_REQUEST_COMPLETED,
        ARTIFACT_CREATED,
        ARTIFACT_VALIDATION_FAILED,
        RUN_WAITING_FOR_HUMAN,
        RUN_COMPLETED,
        RUN_FAILED,
        RETRY_STARTED,
        RUNTIME_ERROR,
    }
)


@runtime_checkable
class LoggerPort(Protocol):
    """Structured, bindable, redacting logger."""

    def event(self, event: str, *, level: str = "INFO", **fields: Any) -> dict[str, Any]:
        """Record one structured event and return the emitted record."""
        ...

    def bind(self, **fields: Any) -> LoggerPort:
        """Return a logger with additional bound context (run_id, correlation_id, ...)."""
        ...

    def close(self) -> None:
        """Flush pending records."""
        ...
