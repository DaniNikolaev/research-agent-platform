"""Structured logging implementation: JSON Lines (or documented key=value) with redaction.

The event vocabulary lives in :mod:`research_platform.application.ports.logging` so that
application services and graph nodes can log without importing this adapter.  This module
implements the port and adds the encoding, level filtering and stream handling.

Every record is one line.  JSONL is the default because it is greppable, diffable and
machine-readable without a collector; ``log_format: text`` exists for humans reading a
terminal.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, TextIO

from research_platform.application.ports.logging import (
    APPLICATION_SHUTDOWN,
    APPLICATION_STARTED,
    ARTIFACT_CREATED,
    ARTIFACT_VALIDATION_FAILED,
    CHECK_COMPLETED,
    CHECKPOINT_SAVED,
    CONFIG_LOADED,
    CONFIG_VALIDATION_FAILED,
    EVENT_NAMES,
    EXTRACTION_PLANNED,
    FAKE_MODEL_REQUEST_COMPLETED,
    GROUP_COMPLETED,
    GROUP_FAILED,
    GROUP_STARTED,
    MODEL_REQUEST_COMPLETED,
    CHUNKING_COMPLETED,
    EVIDENCE_RESOLUTION_COMPLETED,
    MODEL_REQUEST_STARTED,
    RETRIEVAL_COMPLETED,
    RETRIEVAL_FALLBACK,
    RETRIEVAL_INDEX_BUILT,
    RETRY_STARTED,
    RUN_COMPLETED,
    RUN_CREATED,
    RUN_FAILED,
    RUN_MANIFEST_PUBLISH_FAILED,
    RUN_STARTED,
    RUN_WAITING_FOR_HUMAN,
    RUNTIME_ERROR,
    STAGE_COMPLETED,
    STAGE_FAILED,
    STAGE_STARTED,
    WORKFLOW_RESUMED,
)
from research_platform.application.services.redaction import Redactor
from research_platform.config import LogFormat, LogLevel

__all__ = [
    "APPLICATION_SHUTDOWN",
    "APPLICATION_STARTED",
    "ARTIFACT_CREATED",
    "ARTIFACT_VALIDATION_FAILED",
    "CHECKPOINT_SAVED",
    "CHECK_COMPLETED",
    "CONFIG_LOADED",
    "CONFIG_VALIDATION_FAILED",
    "EVENT_NAMES",
    "EXTRACTION_PLANNED",
    "FAKE_MODEL_REQUEST_COMPLETED",
    "GROUP_COMPLETED",
    "GROUP_FAILED",
    "GROUP_STARTED",
    "MODEL_REQUEST_COMPLETED",
    "CHUNKING_COMPLETED",
    "EVIDENCE_RESOLUTION_COMPLETED",
    "MODEL_REQUEST_STARTED",
    "RETRIEVAL_COMPLETED",
    "RETRIEVAL_FALLBACK",
    "RETRIEVAL_INDEX_BUILT",
    "RETRY_STARTED",
    "RUNTIME_ERROR",
    "RUN_COMPLETED",
    "RUN_CREATED",
    "RUN_FAILED",
    "RUN_MANIFEST_PUBLISH_FAILED",
    "RUN_STARTED",
    "RUN_WAITING_FOR_HUMAN",
    "STAGE_COMPLETED",
    "STAGE_FAILED",
    "STAGE_STARTED",
    "WORKFLOW_RESUMED",
    "StructuredLogger",
    "new_log_stream",
]

_LEVEL_ORDER: Final[dict[LogLevel, int]] = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
    LogLevel.CRITICAL: 50,
}


def new_log_stream() -> TextIO:
    """A writable stream that discards everything (used when logging is disabled)."""
    return io.StringIO()


class StructuredLogger:
    """Small structured logger with bound context and mandatory redaction."""

    def __init__(
        self,
        *,
        service_name: str = "research-agent-platform",
        stream: TextIO | None = None,
        level: LogLevel = LogLevel.INFO,
        log_format: LogFormat = LogFormat.JSONL,
        redactor: Redactor | None = None,
        enabled: bool = True,
        _bound: Mapping[str, Any] | None = None,
    ) -> None:
        self._service_name = service_name
        self._stream = stream if stream is not None else sys.stderr
        self._level = level
        self._format = log_format
        self._redactor = redactor if redactor is not None else Redactor()
        self._enabled = enabled
        self._bound: dict[str, Any] = dict(_bound or {})

    # ------------------------------------------------------------------ helpers

    @property
    def service_name(self) -> str:
        """Name written into the ``application`` field."""
        return self._service_name

    @property
    def enabled(self) -> bool:
        """Whether records are actually written."""
        return self._enabled

    def bind(self, **fields: Any) -> StructuredLogger:
        """Return a logger with additional bound context (run_id, correlation_id, ...)."""
        merged = {**self._bound, **fields}
        return StructuredLogger(
            service_name=self._service_name,
            stream=self._stream,
            level=self._level,
            log_format=self._format,
            redactor=self._redactor,
            enabled=self._enabled,
            _bound=merged,
        )

    def _should_emit(self, level: LogLevel) -> bool:
        if not self._enabled:
            return False
        return _LEVEL_ORDER[level] >= _LEVEL_ORDER[self._level]

    def _record(self, event: str, level: LogLevel, fields: Mapping[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": level.value,
            "event": event,
            "application": self._service_name,
            **self._bound,
            **fields,
        }
        cleaned = {key: value for key, value in payload.items() if value is not None}
        return self._redactor.redact_mapping(cleaned)

    def event(self, event: str, *, level: str = "INFO", **fields: Any) -> dict[str, Any]:
        """Record one structured event and return the emitted record."""
        if event not in EVENT_NAMES:
            raise ValueError(
                f"unknown event {event!r}; add it to EVENT_NAMES instead of inventing a name"
            )
        resolved = LogLevel(level.upper())
        record = self._record(event, resolved, fields)
        if not self._should_emit(resolved):
            return record
        if self._format is LogFormat.JSONL:
            line = json.dumps(record, ensure_ascii=False, default=str, sort_keys=True)
        else:
            pairs = " ".join(
                f"{key}={value}"
                for key, value in record.items()
                if key not in {"timestamp", "level", "event"}
            )
            line = f"{record['timestamp']} {record['level']} {record['event']} {pairs}".rstrip()
        self._stream.write(line + "\n")
        self._stream.flush()
        return record

    def close(self) -> None:
        """Flush the stream; never closes a stream owned by the caller."""
        with contextlib.suppress(ValueError):  # stream already closed by the caller
            self._stream.flush()


def parsing_level(value: str) -> LogLevel:
    """Parse a level name (convenience for CLI/bootstrap)."""
    return LogLevel(value.upper())
