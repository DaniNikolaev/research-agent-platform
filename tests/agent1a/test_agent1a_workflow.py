"""Integration tests of the Agent 1A extraction graph.

The graph is exercised through the composition root (settings → adapters → services →
LangGraph → artefacts), never by calling nodes directly.  Everything that reaches the model
goes through the fake gateway, which is deterministic and offline.
"""

from __future__ import annotations

import json

import pytest

from research_platform.app import ApplicationContext, bootstrap_application
from research_platform.application.services.extraction_errors import (
    ExtractionArtifactError,
    ExtractionContextOverflow,
    ExtractionError,
    ExtractionIntegrityError,
)
from research_platform.application.services.extraction_prompts import load_prompt_set
from research_platform.domain.documents.quality import QualityStatus
from research_platform.domain.extraction.models import (
    ExtractionGroup,
    ExtractionRunManifest,
    GroupStatus,
    groups_in_order,
)
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.domain.runs.models import RunStatus
from tests.agent1a.conftest import (
    Project,
    document_run,
    write_txt,
)

ALL_GROUPS = tuple(group.value for group in groups_in_order())


def _rebuild(project: Project, **agent_1a: object) -> ApplicationContext:
    project.config(**agent_1a)
    return bootstrap_application(project_root=project.root)


def _artifacts_of_type(context: ApplicationContext, run_id: str, kind: ArtifactType) -> list:
    return [
        metadata
        for metadata in context.artifact_service.list_for_run(run_id)
        if metadata.artifact_type is kind
    ]


def _manifest(context: ApplicationContext, run_id: str) -> ExtractionRunManifest:
    metadata = [
        item
        for item in context.artifact_service.list_for_run(run_id)
        if item.artifact_id.startswith("art_extraction_manifest")
    ]
    assert metadata, "no extraction manifest was published"
    payload = json.loads(context.artifact_service.read_by_id(metadata[0].artifact_id))
    return ExtractionRunManifest.model_validate(payload)


# ------------------------------------------------------------------ happy paths


def test_full_extraction_completes_and_publishes_artifacts(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = document_run(context, project)
    outcome = context.agent1a_extraction.start(
        document_run_id=doc_run, correlation_id="corr_e2e_txt"
    )
    assert outcome.run is not None
    assert outcome.run.status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}
    state = outcome.state
    assert state["processing_status"] == "completed"
    assert state["retrieval_mode"] == "full_context"
    assert state["completed_groups"] == list(ALL_GROUPS)
    assert state["quality_status"] == QualityStatus.PASSED.value

    run_id = state["run_id"]
    assert run_id != doc_run, "the extraction run must have its own id"
    assert state["document_run_id"] == doc_run
    assert set(state["group_result_uris"]) == set(ALL_GROUPS)

    candidates = _artifacts_of_type(context, run_id, ArtifactType.EXTRACTION_CANDIDATES)
    assert len(candidates) == 5, "four groups plus the aggregate"
    plans = _artifacts_of_type(context, run_id, ArtifactType.TRACE)
    assert len(plans) == 1

    manifest = _manifest(context, run_id)
    assert manifest.final_status == "completed"
    assert manifest.execution_mode.value == "simulation"
    assert manifest.provider_id == "fake"
    assert manifest.model_id == "fake-structured-v1"
    assert manifest.prompt_set_version == "1.0"
    assert manifest.quality_status == "passed"
    assert set(manifest.group_statuses.values()) == {GroupStatus.COMPLETED.value}
    assert manifest.document_run_id == doc_run
    assert len(manifest.source_sha256) == 64

    # every published artefact carries a hash and lives in the Agent 1A run namespace
    for metadata in context.artifact_service.list_for_run(run_id):
        assert metadata.sha256
        assert metadata.uri.startswith(
            f"artifact://documents/{state['document_id']}/runs/{run_id}/"
        )


def test_group_results_are_typed_and_marked_as_simulation(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = document_run(context, project)
    outcome = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_types")
    run_id = outcome.state["run_id"]
    payloads = [
        json.loads(context.artifact_service.read_by_id(metadata.artifact_id))
        for metadata in _artifacts_of_type(context, run_id, ArtifactType.EXTRACTION_CANDIDATES)
        if metadata.artifact_id.startswith("art_g")
    ]
    assert len(payloads) == 4
    for payload in payloads:
        assert payload["execution_mode"] == "simulation"
        assert payload["provider_id"] == "fake"
        assert payload["prompt_version"] == "1.0"
        assert payload["status"] == "completed"
        assert payload["attempts"] >= 1
        assert payload["evidence_candidates"]
        for candidate in payload["evidence_candidates"]:
            assert candidate["note"], "simulated candidates must say they are simulated"
            assert candidate["candidate_id"].startswith("cand_")


def test_second_run_does_not_overwrite_the_first(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = document_run(context, project)
    first = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_first")
    second = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_second")
    assert first.state["run_id"] != second.state["run_id"]
    assert first.state["document_id"] == second.state["document_id"]

    first_artifacts = context.artifact_service.list_for_run(first.state["run_id"])
    second_artifacts = context.artifact_service.list_for_run(second.state["run_id"])
    assert {item.artifact_id for item in first_artifacts}.isdisjoint(
        {item.artifact_id for item in second_artifacts}
    )
    # the earlier run is still fully readable
    for metadata in first_artifacts:
        assert context.artifact_service.read_by_id(metadata.artifact_id)


def test_state_stays_compact_and_holds_no_document_text(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = document_run(context, project)
    outcome = context.agent1a_extraction.start(
        document_run_id=doc_run, correlation_id="corr_compact"
    )
    serialized = json.dumps(outcome.state, default=str)
    assert len(serialized) < 4096
    assert "Deterministic" not in serialized
    assert "elements" not in outcome.state
    assert "canonical_text" not in outcome.state
    for key in ("parsed_document_uri", "canonical_text_uri", "quality_report_uri"):
        assert outcome.state[key].startswith("artifact://")


# ------------------------------------------------------------- quality routing


def test_scan_requires_ocr_and_never_calls_the_model(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.agent1a.conftest import seed_document_run as seed

    doc_run = seed(context.artifact_service, quality_status=QualityStatus.REQUIRES_OCR)
    calls: list[str] = []
    pipeline = context.agent1a_extraction
    original = pipeline.gateway.generate_structured

    def spy(request, schema):
        calls.append(request.request_id)
        return original(request, schema)

    monkeypatch.setattr(pipeline.gateway, "generate_structured", spy)
    outcome = pipeline.start(document_run_id=doc_run, correlation_id="corr_ocr")
    assert calls == [], "the model must not be called for a requires_ocr document"
    assert outcome.state["processing_status"] == "requires_ocr"
    assert outcome.state["interrupt_reason"].startswith("requires_ocr")
    assert outcome.run is not None
    assert outcome.run.status is RunStatus.WAITING_FOR_HUMAN


def test_needs_human_review_interrupts_the_graph(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.agent1a.conftest import seed_document_run as seed

    doc_run = seed(context.artifact_service, quality_status=QualityStatus.NEEDS_HUMAN_REVIEW)
    calls: list[str] = []
    pipeline = context.agent1a_extraction
    original = pipeline.gateway.generate_structured

    def spy(request, schema):
        calls.append(request.request_id)
        return original(request, schema)

    monkeypatch.setattr(pipeline.gateway, "generate_structured", spy)
    outcome = pipeline.start(document_run_id=doc_run, correlation_id="corr_review")
    assert calls == []
    assert outcome.paused is True
    assert "needs_human_review" in (outcome.stop_reason or "")
    assert outcome.run is not None
    assert outcome.run.status is RunStatus.WAITING_FOR_HUMAN

    # a technical resume must not confirm the review on its own
    resumed = pipeline.resume(run_id=outcome.state["run_id"], correlation_id="corr_review2")
    assert resumed.paused is True


def test_failed_quality_marks_the_run_failed(project: Project, context: ApplicationContext) -> None:
    from tests.agent1a.conftest import seed_document_run as seed

    doc_run = seed(context.artifact_service, quality_status=QualityStatus.FAILED)
    outcome = context.agent1a_extraction.start(
        document_run_id=doc_run, correlation_id="corr_failed"
    )
    assert outcome.state["processing_status"] == "failed"
    assert outcome.run is not None
    assert outcome.run.status is RunStatus.FAILED
    assert (
        _artifacts_of_type(context, outcome.state["run_id"], ArtifactType.EXTRACTION_CANDIDATES)
        == []
    )


def test_passed_with_warnings_is_allowed_and_keeps_the_warnings(
    project: Project, context: ApplicationContext
) -> None:
    from tests.agent1a.conftest import seed_document_run as seed

    doc_run = seed(context.artifact_service, quality_status=QualityStatus.PASSED_WITH_WARNINGS)
    outcome = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_warn")
    assert outcome.state["processing_status"] == "completed"
    assert outcome.run is not None
    assert outcome.run.warnings, "warnings must be carried into the run"
    manifest = _manifest(context, outcome.state["run_id"])
    assert manifest.quality_status == "passed_with_warnings"


# ---------------------------------------------------------------- input failures


def test_missing_document_run_is_reported(project: Project, context: ApplicationContext) -> None:
    with pytest.raises(ExtractionArtifactError):
        context.agent1a_extraction.start(
            document_run_id="run_does_not_exist_0001", correlation_id="corr_missing"
        )


def test_integrity_mismatch_blocks_extraction(
    project: Project, context: ApplicationContext
) -> None:
    from tests.agent1a.conftest import seed_document_run as seed

    doc_run = seed(context.artifact_service, tamper_sha=True)
    with pytest.raises(ExtractionIntegrityError):
        context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_integrity")


def test_context_overflow_is_controlled(project: Project, context: ApplicationContext) -> None:
    doc_run = document_run(context, project)
    pipeline = context.agent1a_extraction
    # shrink the window until the full document cannot fit
    pipeline.gateway.profiles["fake-extraction"] = pipeline.gateway.profiles[
        "fake-extraction"
    ].model_copy(update={"max_context_tokens": 200})
    with pytest.raises(ExtractionContextOverflow) as excinfo:
        pipeline.start(document_run_id=doc_run, correlation_id="corr_overflow")
    assert excinfo.value.category.value == "context"
    assert "context" in str(excinfo.value)


# ---------------------------------------------------------------- run-side effects


def test_terminal_group_failure_stops_the_sequence(
    project: Project, context: ApplicationContext
) -> None:
    project.config(fake_scenario="permanent_error")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        with pytest.raises(ExtractionError):
            ctx.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_permanent")
        failed = ctx.run_manager.list_runs(RunStatus.FAILED)
        assert failed, "a terminal model failure must fail the run"
        run_id = failed[0].run_id
        assert _artifacts_of_type(ctx, run_id, ArtifactType.EXTRACTION_CANDIDATES) == []
    finally:
        ctx.close()


def test_transient_error_once_is_retried(project: Project, context: ApplicationContext) -> None:
    project.config(fake_scenario="transient_error_once")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        outcome = ctx.agent1a_extraction.start(
            document_run_id=doc_run, correlation_id="corr_transient"
        )
        assert outcome.state["processing_status"] == "completed"
        assert outcome.state["retry_counters"]["g1_bibliography"] >= 1
    finally:
        ctx.close()


def test_invalid_structured_output_is_repaired(
    project: Project, context: ApplicationContext
) -> None:
    project.config(fake_scenario="invalid_schema_once")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        outcome = ctx.agent1a_extraction.start(
            document_run_id=doc_run, correlation_id="corr_repair"
        )
        assert outcome.state["processing_status"] == "completed"
        assert outcome.state["retry_counters"]["g1_bibliography"] == 1
        manifest = _manifest(ctx, outcome.state["run_id"])
        assert manifest.retry_counts["g1_bibliography"] == 1
    finally:
        ctx.close()


def test_always_invalid_output_never_reports_success(
    project: Project, context: ApplicationContext
) -> None:
    project.config(fake_scenario="invalid_schema_always")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        with pytest.raises(ExtractionError) as excinfo:
            ctx.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_invalid")
        assert excinfo.value.category.value == "structured_output"
        failed = ctx.run_manager.list_runs(RunStatus.FAILED)
        assert failed
        assert all(
            record.metadata.get("purpose") != "extraction" or record.run_id == failed[0].run_id
            for record in ctx.run_manager.list_runs(RunStatus.COMPLETED)
        )
    finally:
        ctx.close()


def test_prompts_used_by_the_run_are_the_versioned_ones(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = document_run(context, project)
    outcome = context.agent1a_extraction.start(
        document_run_id=doc_run, correlation_id="corr_prompts"
    )
    plan_artifact = _artifacts_of_type(context, outcome.state["run_id"], ArtifactType.TRACE)[0]
    plan = json.loads(context.artifact_service.read_by_id(plan_artifact.artifact_id))
    prompts = load_prompt_set()
    for step in plan["steps"]:
        group = ExtractionGroup(step["group"])
        assert step["prompt_id"] == prompts[group].prompt_id
        assert step["prompt_version"] == "1.0"
        assert step["field_keys"] == list(prompts[group].field_keys)


def test_txt_document_keeps_no_invented_pagination(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = document_run(context, project)
    outcome = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_txt")
    # a TXT source has no physical pages; the extraction inherits that, not a fake page 1
    parsed_uri = outcome.state["parsed_document_uri"]
    metadata = next(
        item for item in context.artifact_service.list_for_run(doc_run) if item.uri == parsed_uri
    )
    parsed = json.loads(context.artifact_service.read_by_id(metadata.artifact_id))
    assert parsed["pages"] == []
    assert parsed["page_count"] is None
    assert parsed["source_type"] == "txt"


def test_write_txt_helper_reuses_the_same_document(
    project: Project, context: ApplicationContext
) -> None:
    """Two extraction runs of one document share its id but not its run artefacts."""
    doc_run = document_run(context, project)
    first = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_id_1")
    second = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_id_2")
    assert first.state["document_id"] == second.state["document_id"]
    assert first.state["source_sha256"] == second.state["source_sha256"]
    assert write_txt(project).read_text(encoding="utf-8")
    assert len(context.run_manager.list_runs()) >= 2
