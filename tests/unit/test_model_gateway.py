"""Model Gateway contract: normalised errors, capability gate, no secrets."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fixtures.fakes import FakeChatModel, FakeEmbeddingModel, FakeReranker

from research_platform.application.ports.model_gateway.errors import (
    ModelAuthError,
    ModelError,
    ModelMalformedResponseError,
    ModelProviderUnavailableError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnknownError,
    ModelUnsupportedCapabilityError,
    normalize_provider_error,
)
from research_platform.application.ports.model_gateway.protocols import (
    ChatModelPort,
    EmbeddingModelPort,
    RerankerPort,
    StructuredOutputModelPort,
    require_structured_output,
)
from research_platform.application.ports.model_gateway.types import (
    EmbeddingRequest,
    FinishReason,
    Message,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    Role,
    SchemaReference,
    TokenUsage,
)


def request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "req_0001",
        "task": "extraction.field_group",
        "model_profile": "extraction-primary",
        "messages": [{"role": "user", "content": "Extract the bibliography group."}],
        "timeout_seconds": 30.0,
    }
    payload.update(overrides)
    return payload


def test_fake_chat_model_implements_the_protocols() -> None:
    model = FakeChatModel()
    assert isinstance(model, ChatModelPort)
    assert isinstance(model, StructuredOutputModelPort)
    embedding = FakeEmbeddingModel()
    assert isinstance(embedding, EmbeddingModelPort)
    assert isinstance(FakeReranker(), RerankerPort)


def test_fake_model_generates_structured_output() -> None:
    model = FakeChatModel(payload={"fields": 3})
    schema = SchemaReference(schema_id="agent_1a.field_group", version="2.0")
    response = model.generate_structured(ModelRequest.model_validate(request()), schema)
    assert response.structured_payload == {"fields": 3}
    assert response.finish_reason is FinishReason.STOP
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    ("raised", "expected_type", "expected_code"),
    [
        (TimeoutError("too slow"), ModelTimeoutError, "timeout"),
        (ConnectionError("refused"), ModelProviderUnavailableError, "provider_unavailable"),
        (PermissionError("denied"), ModelAuthError, "auth_error"),
        (ValueError("bad payload"), ModelMalformedResponseError, "malformed_response"),
        (
            json.JSONDecodeError("bad json", "{", 0),
            ModelMalformedResponseError,
            "malformed_response",
        ),
        (MemoryError("boom"), ModelUnknownError, "unknown"),
    ],
)
def test_provider_errors_are_normalised(
    raised: BaseException, expected_type: type[ModelError], expected_code: str
) -> None:
    error = normalize_provider_error(raised, provider_id="p", model_id="m", request_id="req_0001")
    assert type(error) is expected_type
    assert error.code == expected_code
    assert error.request_id == "req_0001"


def test_already_normalised_error_is_returned_unchanged() -> None:
    original = ModelRateLimitError("429", provider_id="p")
    assert normalize_provider_error(original) is original
    assert original.retryable is True


def test_adapter_raises_normalised_error() -> None:
    model = FakeChatModel(raise_error=TimeoutError("gateway timeout"))
    with pytest.raises(ModelTimeoutError) as excinfo:
        model.generate_text(ModelRequest.model_validate(request(task="freeform.text")))
    assert excinfo.value.request_id == "req_0001"


def test_capability_gate_fails_before_any_call() -> None:
    model = FakeChatModel(structured=False, json_schema=False)
    schema = SchemaReference(schema_id="agent_1a.field_group", version="2.0")
    with pytest.raises(ModelUnsupportedCapabilityError):
        model.generate_structured(ModelRequest.model_validate(request()), schema)
    assert model.calls == [], "the request must not be issued without the capability"


def test_require_structured_output_reports_both_identifiers() -> None:
    capabilities = ModelCapabilities(
        provider_id="p",
        model_id="m",
        max_context_tokens=1000,
        max_output_tokens=100,
        supports_structured_output=False,
        supports_json_schema=False,
    )
    with pytest.raises(ModelUnsupportedCapabilityError) as excinfo:
        require_structured_output(capabilities)
    assert "p/m" in str(excinfo.value)


def test_json_schema_capability_implies_structured_output() -> None:
    with pytest.raises(ValidationError):
        ModelCapabilities(
            provider_id="p",
            model_id="m",
            max_context_tokens=1000,
            max_output_tokens=100,
            supports_structured_output=False,
            supports_json_schema=True,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "value"},
        {"Authorization": "Bearer abc"},
        {"note": "sk-live-1234567890"},
        {"password_hint": "x"},
    ],
)
def test_request_metadata_rejects_secrets(metadata: dict[str, str]) -> None:
    with pytest.raises(ValidationError) as excinfo:
        ModelRequest.model_validate(request(metadata=metadata))
    assert "credential" in str(excinfo.value)


def test_request_has_no_credential_field_and_redacts_content() -> None:
    model_request = ModelRequest.model_validate(
        request(messages=[{"role": "system", "content": "secret-ish system prompt"}])
    )
    assert not hasattr(model_request, "api_key")
    redacted = model_request.redacted()
    serialized = json.dumps(redacted)
    assert "system prompt" not in serialized
    assert redacted["message_count"] == 1
    assert Message(role=Role.SYSTEM, content="x").role is Role.SYSTEM


def test_truncated_response_must_be_reported() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ModelResponse(
            request_id="req_0001",
            provider_id="p",
            model_id="m",
            finish_reason=FinishReason.LENGTH,
            raw_text="partial",
            latency_ms=1.0,
        )
    assert "truncated" in str(excinfo.value)


def test_empty_response_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelResponse(
            request_id="req_0001",
            provider_id="p",
            model_id="m",
            finish_reason=FinishReason.STOP,
            latency_ms=1.0,
        )


def test_token_usage_total_is_consistent() -> None:
    usage = TokenUsage(input_tokens=10, output_tokens=5)
    assert usage.effective_total == 15
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=10, output_tokens=5, total_tokens=3)


def test_embedding_vectors_are_rectangular() -> None:
    model = FakeEmbeddingModel(dimensions=4)
    request_obj = EmbeddingRequest(
        request_id="req_emb_0001", model_profile="embedding-primary", texts=["one", "two"]
    )
    response = model.embed(request_obj)
    assert len(response.vectors) == 2
    assert {len(vector) for vector in response.vectors} == {4}


def test_text_task_must_not_declare_a_schema() -> None:
    schema = SchemaReference(schema_id="agent_1a.field_group", version="2.0")
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            request(task="freeform.text", output_schema=schema.model_dump())
        )
