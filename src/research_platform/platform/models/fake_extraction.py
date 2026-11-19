"""Fake structured-extraction gateway.

This adapter is the only model backend of iteration 3.  It is deterministic, offline and
explicitly labelled:

* its outputs carry ``execution_mode="simulation"`` — they are *not* scientific results;
* it implements the same :class:`ModelGatewayPort` as a real adapter, so replacing it later
  requires no change in the workflow;
* it can replay the failure scenarios the workflow must survive (invalid structured output,
  transient errors, permanent errors, empty results).

The scenario is selected by configuration, never at random.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from research_platform.application.ports.model_gateway.errors import (
    ModelAuthError,
    ModelMalformedResponseError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnsupportedCapabilityError,
)
from research_platform.application.ports.model_gateway.protocols import require_structured_output
from research_platform.application.ports.model_gateway.types import (
    EmbeddingRequest,
    EmbeddingResponse,
    FinishReason,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    SchemaReference,
)

__all__ = ["SCENARIOS", "FakeExtractionGateway", "FakeExtractionScenario"]

FAKE_PROVIDER_ID = "fake"
FAKE_MODEL_ID = "fake-structured-v1"
SIMULATION_MODE = "simulation"


class FakeExtractionScenario(StrEnum):
    """Deterministic behaviours the workflow is tested against."""

    SUCCESS = "success"
    EMPTY_RESULT = "empty_result"
    INVALID_SCHEMA_ONCE = "invalid_schema_once"
    INVALID_SCHEMA_ALWAYS = "invalid_schema_always"
    TRANSIENT_ERROR_ONCE = "transient_error_once"
    TRANSIENT_ERROR_ALWAYS = "transient_error_always"
    PERMANENT_ERROR = "permanent_error"
    INTERRUPTION_SCENARIO = "interruption_scenario"


SCENARIOS: tuple[str, ...] = tuple(scenario.value for scenario in FakeExtractionScenario)


@dataclass
class _TaskState:
    """Per-task call counter, so "once" scenarios mean exactly that."""

    calls: int = 0
    invalid_returned: bool = False


@dataclass
class FakeExtractionGateway:
    """Profile-aware fake implementing structured extraction."""

    profiles: Mapping[str, ModelCapabilities]
    scenario: FakeExtractionScenario = FakeExtractionScenario.SUCCESS
    default_profile: str | None = None
    _state: dict[str, _TaskState] = field(default_factory=dict)

    provider_id = FAKE_PROVIDER_ID
    is_fake = True

    def __post_init__(self) -> None:
        if not self.profiles:
            raise ValueError("FakeExtractionGateway requires at least one model profile")
        if self.default_profile is None:
            self.default_profile = next(iter(sorted(self.profiles)))

    # ----------------------------------------------------------------- profiles

    @property
    def model_id(self) -> str:
        """Identifier of the fake model recorded in every result."""
        return FAKE_MODEL_ID

    def capabilities(self, model_profile: str) -> ModelCapabilities:
        """Capabilities of one configured profile."""
        name = model_profile or self.default_profile or ""
        if name not in self.profiles:
            raise ModelUnsupportedCapabilityError(
                f"unknown model profile {name!r}; configured: {sorted(self.profiles)}",
                provider_id=self.provider_id,
            )
        return self.profiles[name]

    def _resolve(self, request: ModelRequest) -> ModelCapabilities:
        capabilities = self.capabilities(request.model_profile)
        require_structured_output(capabilities)
        return capabilities

    # ------------------------------------------------------------------- calling

    def generate_text(self, request: ModelRequest) -> ModelResponse:  # pragma: no cover
        """Text generation is not part of the extraction path."""
        raise ModelUnsupportedCapabilityError(
            "the extraction workflow uses structured generation only",
            provider_id=self.provider_id,
        )

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:  # pragma: no cover
        """Embeddings are out of scope for this iteration."""
        raise ModelUnsupportedCapabilityError(
            "embeddings are not implemented in iteration 3",
            provider_id=self.provider_id,
        )

    def generate_structured(self, request: ModelRequest, schema: SchemaReference) -> ModelResponse:
        """Return a scripted structured response for the requested group."""
        self._resolve(request)
        state = self._state.setdefault(request.task, _TaskState())
        state.calls += 1

        if self.scenario is FakeExtractionScenario.PERMANENT_ERROR:
            raise ModelAuthError("scripted permanent failure", provider_id=self.provider_id)
        if self.scenario is FakeExtractionScenario.TRANSIENT_ERROR_ALWAYS:
            raise ModelTimeoutError("scripted transient failure", provider_id=self.provider_id)
        if self.scenario is FakeExtractionScenario.TRANSIENT_ERROR_ONCE and state.calls == 1:
            raise ModelRateLimitError("scripted transient failure", provider_id=self.provider_id)
        if self.scenario is FakeExtractionScenario.INVALID_SCHEMA_ALWAYS:
            return self._invalid_response(request, schema)
        if (
            self.scenario is FakeExtractionScenario.INVALID_SCHEMA_ONCE
            and not state.invalid_returned
        ):
            state.invalid_returned = True
            return self._invalid_response(request, schema)

        payload = self._payload(request)
        return ModelResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model_id=FAKE_MODEL_ID,
            finish_reason=FinishReason.STOP,
            structured_payload=payload,
            # No usage is reported: the fake has no tokenizer, and inventing token counts
            # would look like real consumption. The heuristic counter is used separately.
            usage=None,
            latency_ms=1.0,
            warnings=[
                "fake structured adapter: simulation output, no real model was called",
            ],
        )

    # -------------------------------------------------------------------- payload

    @staticmethod
    def _group(request: ModelRequest) -> str:
        group = request.metadata.get("group")
        if not group:
            raise ModelMalformedResponseError(
                "the fake adapter needs metadata['group'] to script its answer"
            )
        return group

    def _payload(self, request: ModelRequest) -> dict:
        group = self._group(request)
        field_keys = [
            key.strip()
            for key in (request.metadata.get("field_keys") or "").split(",")
            if key.strip()
        ]
        empty = self.scenario is FakeExtractionScenario.EMPTY_RESULT

        fields = []
        candidates = []
        for index, key in enumerate(field_keys):
            if empty:
                fields.append({"field_key": key, "status": "empty", "proposed_value": None})
                continue
            value = f"SIMULATED_VALUE::{key}"
            fields.append({"field_key": key, "status": "candidate", "proposed_value": value})
            chunk_ids = [
                item
                for item in (request.metadata.get("chunk_ids") or "").split(",")
                if item
            ]
            target_chunk = chunk_ids[index % len(chunk_ids)] if chunk_ids else None
            anchor = self._anchor_quote(request, target_chunk)
            candidates.append(
                {
                    "field_path": key,
                    "chunk_id": target_chunk,
                    "quote": anchor
                    or f"SIMULATED QUOTE for {key} - no scientific content",
                }
            )
        return {
            "group": group,
            "fields": fields,
            "evidence_candidates": candidates,
            "warnings": [] if not empty else ["fake scenario returned no candidates"],
        }

    @staticmethod
    def _anchor_quote(request: ModelRequest, chunk_id: str | None) -> str | None:
        """Echo a real substring of the provided chunk so the resolver can locate it."""
        if not chunk_id:
            return None
        content = request.messages[-1].content
        marker = f"[[chunk {chunk_id}]]"
        start = content.find(marker)
        if start < 0:
            return None
        body_start = start + len(marker)
        end = content.find("[[/chunk]]", body_start)
        body = content[body_start:end].strip() if end >= 0 else content[body_start:].strip()
        return body[:80].strip() or None

    def _invalid_response(
        self,
        request: ModelRequest,
        schema: SchemaReference,
    ) -> ModelResponse:
        """Well-formed envelope whose payload violates the requested schema."""
        return ModelResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model_id=FAKE_MODEL_ID,
            finish_reason=FinishReason.STOP,
            structured_payload={
                "group": self._group(request),
                "fields": "this is not a list",
                "schema_id": schema.schema_id,
            },
            usage=None,
            latency_ms=1.0,
            warnings=["fake structured adapter: scripted invalid payload"],
        )
