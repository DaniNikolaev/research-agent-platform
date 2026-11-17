"""Unit tests of the extraction building blocks (state, payloads, prompts, fake gateway)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from research_platform.application.ports.model_gateway.errors import (
    ModelAuthError,
    ModelRateLimitError,
    ModelTimeoutError,
    ModelUnsupportedCapabilityError,
)
from research_platform.application.ports.model_gateway.types import (
    Message,
    ModelCapabilities,
    ModelRequest,
    Role,
    SchemaReference,
)
from research_platform.application.services.extraction_payloads import (
    GroupPayload,
    PayloadCandidate,
    PayloadField,
)
from research_platform.application.services.extraction_prompts import (
    load_prompt,
    load_prompt_set,
)
from research_platform.domain.extraction.models import (
    GROUP_FIELD_KEYS,
    ExecutionMode,
    ExtractionFieldValue,
    ExtractionGroup,
    ExtractionPlan,
    FieldValueStatus,
    GroupExtractionResult,
    GroupStatus,
    PlanStep,
    field_keys_of,
    groups_in_order,
)
from research_platform.platform.models.fake_extraction import (
    SCENARIOS,
    FakeExtractionGateway,
    FakeExtractionScenario,
)
from research_platform.workflows.agent1a_extraction_state import (
    AGENT1A_PROCESSING_STATUSES,
    initial_extraction_state,
    validate_extraction_state,
)

CAPABILITIES = ModelCapabilities(
    provider_id="fake",
    model_id="fake-structured-v1",
    max_context_tokens=16000,
    max_output_tokens=2048,
    supports_structured_output=True,
    supports_json_schema=True,
)


def _request(group: ExtractionGroup, *, task: str | None = None) -> ModelRequest:
    prompt = load_prompt(group)
    return ModelRequest(
        request_id="req_test_0001",
        task=task or prompt.prompt_id,
        model_profile="fake-extraction",
        messages=[Message(role=Role.USER, content="document text")],
        output_schema=SchemaReference(schema_id=prompt.schema_id, version="1.0"),
        metadata={
            "group": group.value,
            "field_keys": ",".join(field_keys_of(group)),
            "document_id": "doc_" + "a" * 24,
        },
    )


# ------------------------------------------------------------------------ groups


def test_groups_cover_the_twenty_seven_card_fields() -> None:
    assert [group.value for group in groups_in_order()] == [
        "g1_bibliography",
        "g2_research_design",
        "g3_methods_data_variables",
        "g4_findings_implications",
    ]
    assert sum(len(GROUP_FIELD_KEYS[group]) for group in ExtractionGroup) == 27
    assert [group.order for group in groups_in_order()] == [1, 2, 3, 4]


def test_group_result_rejects_a_field_of_another_group() -> None:
    with pytest.raises(ValidationError):
        GroupExtractionResult(
            group=ExtractionGroup.G1_BIBLIOGRAPHY,
            group_order=1,
            document_id="doc_" + "a" * 24,
            run_id="run_0001",
            document_run_id="run_0000",
            provider_id="fake",
            model_id="fake-structured-v1",
            execution_mode=ExecutionMode.SIMULATION,
            prompt_id="agent1a.extract.g1_bibliography",
            prompt_version="1.0",
            status=GroupStatus.COMPLETED,
            fields=[
                ExtractionFieldValue(
                    field_key="methodology",
                    status=FieldValueStatus.CANDIDATE,
                    proposed_value="x",
                )
            ],
            created_at="2026-11-17T12:00:00Z",
        )


def test_fake_provider_must_declare_simulation() -> None:
    with pytest.raises(ValidationError):
        GroupExtractionResult(
            group=ExtractionGroup.G1_BIBLIOGRAPHY,
            group_order=1,
            document_id="doc_" + "a" * 24,
            run_id="run_0001",
            document_run_id="run_0000",
            provider_id="fake",
            model_id="fake-structured-v1",
            execution_mode=ExecutionMode.REAL_MODEL,
            prompt_id="agent1a.extract.g1_bibliography",
            prompt_version="1.0",
            status=GroupStatus.COMPLETED,
            created_at="2026-11-17T12:00:00Z",
        )


def test_plan_requires_the_four_groups_in_order() -> None:
    prompts = load_prompt_set()
    steps = [
        PlanStep(
            group=group,
            order=group.order,
            field_keys=list(field_keys_of(group)),
            prompt_id=prompts[group].prompt_id,
            prompt_version=prompts[group].prompt_version,
            schema_id=prompts[group].schema_id,
        )
        for group in groups_in_order()
    ]
    plan = ExtractionPlan(
        document_id="doc_" + "a" * 24,
        run_id="run_0001",
        document_run_id="run_0000",
        steps=steps,
    )
    assert plan.retrieval_mode == "full_context"
    with pytest.raises(ValidationError):
        ExtractionPlan(
            document_id="doc_" + "a" * 24,
            run_id="run_0001",
            document_run_id="run_0000",
            steps=[steps[1], steps[0], steps[2], steps[3]],
        )


# ----------------------------------------------------------------------- payload


def test_payload_rejects_unknown_field_and_duplicates() -> None:
    with pytest.raises(ValidationError):
        GroupPayload(
            group=ExtractionGroup.G1_BIBLIOGRAPHY,
            fields=[PayloadField(field_key="methodology", status=FieldValueStatus.EMPTY)],
        )
    duplicate = PayloadField(field_key="bibliography.title", status=FieldValueStatus.EMPTY)
    with pytest.raises(ValidationError):
        GroupPayload(group=ExtractionGroup.G1_BIBLIOGRAPHY, fields=[duplicate, duplicate])


def test_payload_candidate_must_belong_to_the_group() -> None:
    with pytest.raises(ValidationError):
        GroupPayload(
            group=ExtractionGroup.G1_BIBLIOGRAPHY,
            evidence_candidates=[PayloadCandidate(field_path="methodology", quote="a quote")],
        )


def test_empty_candidate_is_not_a_found_value() -> None:
    field = PayloadField(field_key="bibliography.doi", status=FieldValueStatus.EMPTY)
    assert field.proposed_value is None
    with pytest.raises(ValidationError):
        PayloadField(
            field_key="bibliography.doi",
            status=FieldValueStatus.EMPTY,
            proposed_value="10.1000/x",
        )


# ----------------------------------------------------------------------- prompts


def test_prompts_are_versioned_and_group_specific() -> None:
    prompts = load_prompt_set()
    assert set(prompts) == set(ExtractionGroup)
    for group, prompt in prompts.items():
        assert prompt.prompt_version == "1.0"
        assert prompt.group is group
        assert prompt.field_keys == field_keys_of(group)
        assert prompt.text.strip()
        assert "untrusted" in prompt.text.lower()
        assert "Return JSON only" in prompt.text
    assert len({prompt.content_hash for prompt in prompts.values()}) == 4


def test_unknown_prompt_set_version_is_an_error() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt(ExtractionGroup.G1_BIBLIOGRAPHY, version="9.9")


# ------------------------------------------------------------------------- state


def test_initial_state_is_complete_and_valid() -> None:
    state = initial_extraction_state(document_run_id="run_doc_0001", correlation_id="corr_1")
    validate_extraction_state(state)
    assert state["processing_status"] == "created"
    assert state["retrieval_mode"] == "full_context"
    assert set(state["group_statuses"].values()) == {"pending"}
    # an empty run id is allowed before initialisation, everything else is present
    for key in (
        "source_manifest_uri",
        "parsed_document_uri",
        "canonical_text_uri",
        "quality_report_uri",
        "extraction_result_uri",
        "run_manifest_uri",
    ):
        assert state[key] is None


@pytest.mark.parametrize("status", ["nonsense", "", "RUNNING"])
def test_unknown_processing_status_is_rejected(status: str) -> None:
    state = initial_extraction_state(document_run_id="run_doc_0001", correlation_id="corr_1")
    state["processing_status"] = status
    with pytest.raises(ValueError):
        validate_extraction_state(state)


def test_state_rejects_a_broken_artifact_uri() -> None:
    state = initial_extraction_state(document_run_id="run_doc_0001", correlation_id="corr_1")
    state["parsed_document_uri"] = "C:/tmp/document.json"
    with pytest.raises(ValueError):
        validate_extraction_state(state)


def test_state_rejects_an_unknown_group() -> None:
    state = initial_extraction_state(document_run_id="run_doc_0001", correlation_id="corr_1")
    state["group_statuses"] = {"g9_unknown": "completed"}
    with pytest.raises(ValueError):
        validate_extraction_state(state)


def test_state_contains_no_document_payload() -> None:
    state = initial_extraction_state(document_run_id="run_doc_0001", correlation_id="corr_1")
    serialized = json.dumps(state, default=str)
    assert "canonical_text" not in state
    assert "elements" not in state
    assert len(serialized) < 2048
    assert AGENT1A_PROCESSING_STATUSES[0] == "created"


# ------------------------------------------------------------------ fake gateway


def _gateway(scenario: FakeExtractionScenario) -> FakeExtractionGateway:
    return FakeExtractionGateway(
        profiles={"fake-extraction": CAPABILITIES},
        scenario=scenario,
        default_profile="fake-extraction",
    )


def test_fake_success_returns_every_field_and_candidate() -> None:
    gateway = _gateway(FakeExtractionScenario.SUCCESS)
    group = ExtractionGroup.G3_METHODS_DATA_VARIABLES
    prompt = load_prompt(group)
    response = gateway.generate_structured(_request(group), _request(group).output_schema)
    payload = GroupPayload.model_validate(response.structured_payload)
    assert payload.group is group
    assert len(payload.fields) == len(field_keys_of(group))
    assert all(field.status is FieldValueStatus.CANDIDATE for field in payload.fields)
    assert response.provider_id == "fake"
    assert response.model_id == "fake-structured-v1"
    assert response.usage is None, "usage must not be invented"
    assert response.finish_reason.value == "stop"
    assert any("simulation" in warning for warning in response.warnings)
    assert prompt.prompt_id == _request(group).task


def test_fake_empty_result_has_no_candidates() -> None:
    gateway = _gateway(FakeExtractionScenario.EMPTY_RESULT)
    request = _request(ExtractionGroup.G1_BIBLIOGRAPHY)
    response = gateway.generate_structured(request, request.output_schema)
    payload = GroupPayload.model_validate(response.structured_payload)
    assert payload.fields
    assert all(field.status is FieldValueStatus.EMPTY for field in payload.fields)
    assert payload.evidence_candidates == []


def test_fake_invalid_schema_once_then_valid() -> None:
    gateway = _gateway(FakeExtractionScenario.INVALID_SCHEMA_ONCE)
    request = _request(ExtractionGroup.G1_BIBLIOGRAPHY)
    first = gateway.generate_structured(request, request.output_schema)
    with pytest.raises(ValidationError):
        GroupPayload.model_validate(first.structured_payload)
    second = gateway.generate_structured(request, request.output_schema)
    GroupPayload.model_validate(second.structured_payload)


def test_fake_invalid_schema_always_stays_invalid() -> None:
    gateway = _gateway(FakeExtractionScenario.INVALID_SCHEMA_ALWAYS)
    request = _request(ExtractionGroup.G1_BIBLIOGRAPHY)
    for _ in range(3):
        response = gateway.generate_structured(request, request.output_schema)
        with pytest.raises(ValidationError):
            GroupPayload.model_validate(response.structured_payload)


def test_fake_transient_error_once_then_success() -> None:
    gateway = _gateway(FakeExtractionScenario.TRANSIENT_ERROR_ONCE)
    request = _request(ExtractionGroup.G2_RESEARCH_DESIGN)
    with pytest.raises(ModelRateLimitError):
        gateway.generate_structured(request, request.output_schema)
    response = gateway.generate_structured(request, request.output_schema)
    GroupPayload.model_validate(response.structured_payload)


def test_fake_transient_error_always() -> None:
    gateway = _gateway(FakeExtractionScenario.TRANSIENT_ERROR_ALWAYS)
    request = _request(ExtractionGroup.G2_RESEARCH_DESIGN)
    for _ in range(3):
        with pytest.raises(ModelTimeoutError):
            gateway.generate_structured(request, request.output_schema)


def test_fake_permanent_error_is_not_retryable() -> None:
    gateway = _gateway(FakeExtractionScenario.PERMANENT_ERROR)
    request = _request(ExtractionGroup.G2_RESEARCH_DESIGN)
    with pytest.raises(ModelAuthError) as excinfo:
        gateway.generate_structured(request, request.output_schema)
    assert excinfo.value.retryable is False


def test_fake_is_deterministic() -> None:
    request = _request(ExtractionGroup.G4_FINDINGS_IMPLICATIONS)
    first = _gateway(FakeExtractionScenario.SUCCESS).generate_structured(
        request, request.output_schema
    )
    second = _gateway(FakeExtractionScenario.SUCCESS).generate_structured(
        request, request.output_schema
    )
    assert first.structured_payload == second.structured_payload


def test_fake_refuses_unknown_profile_and_text_generation() -> None:
    gateway = _gateway(FakeExtractionScenario.SUCCESS)
    request = _request(ExtractionGroup.G1_BIBLIOGRAPHY).model_copy(
        update={"model_profile": "missing-profile"}
    )
    with pytest.raises(ModelUnsupportedCapabilityError):
        gateway.generate_structured(request, request.output_schema)
    with pytest.raises(ModelUnsupportedCapabilityError):
        gateway.generate_text(_request(ExtractionGroup.G1_BIBLIOGRAPHY))


def test_fake_reads_nothing_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter has no network/file/shell access: the scenario is the only input."""

    def forbidden(*_: object, **__: object) -> None:
        raise AssertionError("the fake adapter must not touch the environment")

    monkeypatch.setattr("socket.socket.connect", forbidden, raising=False)
    monkeypatch.setattr("socket.create_connection", forbidden, raising=False)
    gateway = _gateway(FakeExtractionScenario.SUCCESS)
    request = _request(ExtractionGroup.G1_BIBLIOGRAPHY)
    response = gateway.generate_structured(request, request.output_schema)
    assert response.provider_id == "fake"


def test_every_scenario_name_is_supported() -> None:
    assert set(SCENARIOS) == {scenario.value for scenario in FakeExtractionScenario}
    for name in SCENARIOS:
        FakeExtractionScenario(name)
