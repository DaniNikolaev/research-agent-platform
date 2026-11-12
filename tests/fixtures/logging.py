"""Test helpers for logging and settings (shared by unit and integration tests)."""

from __future__ import annotations

from typing import Any, TextIO

from research_platform.config import LogFormat, LogLevel, PlatformSettings
from research_platform.platform.observability.logging import StructuredLogger

__all__ = ["capturing_logger", "make_settings", "silent_logger"]

_REQUIRED = "test"


def silent_logger() -> StructuredLogger:
    """A logger that records nothing (bounds and filters still apply)."""
    import io

    return StructuredLogger(stream=io.StringIO(), enabled=False)


def capturing_logger(
    *, level: LogLevel = LogLevel.DEBUG, log_format: LogFormat = LogFormat.JSONL
) -> tuple[StructuredLogger, TextIO]:
    """A logger writing to an in-memory stream; returns ``(logger, stream)``."""
    import io

    stream = io.StringIO()
    logger = StructuredLogger(stream=stream, level=level, log_format=log_format, enabled=True)
    return logger, stream


def make_settings(**overrides: Any) -> PlatformSettings:
    """Settings with the fake profile, overridable per section."""
    base: dict[str, Any] = {
        "models": {
            "default_profile": "fake-primary",
            "profiles": {
                "fake-primary": {
                    "provider": "fake_provider",
                    "model_id": "fake_model_1",
                    "purpose": "extraction",
                    "is_fake": True,
                    "supports_structured_output": True,
                    "supports_json_schema": True,
                    "max_retries": 1,
                }
            },
        }
    }
    base.update(overrides)
    return PlatformSettings.model_validate(base)
