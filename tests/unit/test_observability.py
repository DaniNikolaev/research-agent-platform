"""Observability: structured records, event vocabulary, correlation ids, redaction."""

from __future__ import annotations

import io
import json

import pytest

from research_platform.application.ports.logging import (
    EVENT_NAMES,
    FAKE_MODEL_REQUEST_COMPLETED,
    MODEL_REQUEST_COMPLETED,
    RUN_CREATED,
    LoggerPort,
)
from research_platform.application.services.redaction import REDACTED, Redactor
from research_platform.config import LogFormat, LogLevel
from research_platform.platform.observability.logging import StructuredLogger


def logger(**kwargs: object) -> tuple[StructuredLogger, io.StringIO]:
    stream = io.StringIO()
    instance = StructuredLogger(stream=stream, enabled=True, level=LogLevel.DEBUG, **kwargs)  # type: ignore[arg-type]
    return instance, stream


def records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# ------------------------------------------------------------------- structure


def test_logger_implements_the_port() -> None:
    instance, _ = logger()
    assert isinstance(instance, LoggerPort)


def test_record_has_all_required_fields() -> None:
    instance, stream = logger()
    instance.event(RUN_CREATED, run_id="run_1", correlation_id="corr_1", stage="registration")
    record = records(stream)[0]

    assert set(record) >= {
        "timestamp",
        "level",
        "event",
        "application",
        "run_id",
        "correlation_id",
        "stage",
    }
    assert record["event"] == RUN_CREATED
    assert record["level"] == "INFO"
    assert str(record["timestamp"]).endswith("Z")


def test_bound_context_is_merged() -> None:
    instance, stream = logger()
    instance.bind(run_id="run_1", correlation_id="corr_1").event(RUN_CREATED, stage="registration")
    record = records(stream)[0]
    assert record["run_id"] == "run_1"
    assert record["correlation_id"] == "corr_1"


def test_bound_logger_does_not_mutate_the_original() -> None:
    instance, stream = logger()
    child = instance.bind(run_id="run_1")
    instance.event(RUN_CREATED)
    child.event(RUN_CREATED)
    first, second = records(stream)
    assert "run_id" not in first
    assert second["run_id"] == "run_1"


def test_unknown_event_is_rejected() -> None:
    instance, _ = logger()
    with pytest.raises(ValueError):
        instance.event("something_invented")


def test_level_filtering() -> None:
    stream = io.StringIO()
    info_only = StructuredLogger(stream=stream, enabled=True, level=LogLevel.INFO)
    info_only.event(RUN_CREATED, level="DEBUG")
    assert records(stream) == []

    info_only.event(RUN_CREATED, level="INFO")
    info_only.event(RUN_CREATED, level="ERROR")
    assert [record["level"] for record in records(stream)] == ["INFO", "ERROR"]


def test_disabled_logger_writes_nothing() -> None:
    stream = io.StringIO()
    instance = StructuredLogger(stream=stream, enabled=False)
    record = instance.event(RUN_CREATED, run_id="run_1")
    assert record["event"] == RUN_CREATED
    assert stream.getvalue() == ""


def test_text_format_is_key_value() -> None:
    instance, stream = logger(log_format=LogFormat.TEXT)
    instance.event(RUN_CREATED, run_id="run_1")
    line = stream.getvalue().strip()
    assert RUN_CREATED in line
    assert "run_id=run_1" in line
    assert not line.startswith("{")


def test_fake_model_event_is_distinct_from_a_real_request() -> None:
    assert FAKE_MODEL_REQUEST_COMPLETED != MODEL_REQUEST_COMPLETED
    assert FAKE_MODEL_REQUEST_COMPLETED in EVENT_NAMES


def test_event_vocabulary_is_stable() -> None:
    for name in (
        "application_started",
        "application_shutdown",
        "config_loaded",
        "config_validation_failed",
        "run_created",
        "run_started",
        "stage_started",
        "stage_completed",
        "stage_failed",
        "model_request_started",
        "model_request_completed",
        "artifact_created",
        "artifact_validation_failed",
        "run_waiting_for_human",
        "run_completed",
        "run_failed",
        "retry_started",
        "runtime_error",
    ):
        assert name in EVENT_NAMES


# ------------------------------------------------------------------- redaction


@pytest.mark.parametrize(
    "value",
    [
        "sk-live-abcdef0123456789",
        "Authorization: Bearer abcdef123456789",
        "api_key=super-secret-value",
        "password: hunter2hunter2",
        "postgresql://user:secret@localhost:5432/db",
        "token: eyJhbGciOiJIUzI1NiJ9.payload.signature",
    ],
)
def test_secret_like_values_are_masked(value: str) -> None:
    redactor = Redactor()
    masked = redactor.redact_text(value)
    assert REDACTED in masked
    assert "abcdef0123456789" not in masked
    assert "super-secret-value" not in masked
    assert "hunter2hunter2" not in masked
    assert "secret@localhost" not in masked


def test_secret_keys_are_masked_even_without_a_pattern() -> None:
    redactor = Redactor()
    assert redactor.redact_value("api_key", "plain") == REDACTED
    assert redactor.redact_value("Authorization", "plain") == REDACTED
    assert redactor.redact_value("document_text", "ordinary") == "ordinary"


def test_mapping_redaction_is_recursive() -> None:
    redactor = Redactor()
    result = redactor.redact_mapping(
        {
            "run_id": "run_1",
            "credentials": {"api_key": "value", "note": "sk-live-abcdef0123456789"},
        }
    )
    assert result["run_id"] == "run_1"
    assert result["credentials"]["api_key"] == REDACTED
    assert REDACTED in result["credentials"]["note"]


def test_logger_masks_a_secret_in_a_field_value() -> None:
    instance, stream = logger()
    instance.event(RUN_CREATED, detail="key is sk-live-abcdef0123456789")
    record = records(stream)[0]
    assert REDACTED in str(record["detail"])
    assert "abcdef0123456789" not in str(record["detail"])


def test_logger_never_writes_a_secret_named_field() -> None:
    instance, stream = logger()
    instance.event(RUN_CREATED, api_key="sk-live-abcdef0123456789")
    raw = stream.getvalue()
    assert "abcdef0123456789" not in raw


def test_clean_text_survives_redaction() -> None:
    redactor = Redactor()
    text = "document doc_alpha_0001 parsed into 42 blocks in 1.2 s"
    assert redactor.redact_text(text) == text
