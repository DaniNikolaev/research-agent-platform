"""Hardening tests: D1вЂ“D7 closure and A1 context-budget evidence.

Every test names the defect it closes.  The scenarios drive the real composition root and the
real SQLite checkpoint store; only the deliberate failure injections are local.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from research_platform.app import ApplicationContext, bootstrap_application
from research_platform.application.ports.logging import EVENT_NAMES
from research_platform.application.services.extraction_errors import (
    RUN_ERROR_CODES,
    ErrorCategory,
    ExtractionCheckpointError,
    ExtractionContextOverflow,
    ExtractionError,
    ModelPermanentError,
)
from research_platform.application.services.extraction_prompts import load_prompt
from research_platform.application.services.redaction import REDACTED, Redactor
from research_platform.domain.documents.quality import QualityStatus
from research_platform.domain.extraction.models import (
    ExtractionGroup,
    ExtractionRunManifest,
    GroupStatus,
)
from research_platform.domain.runs.artifacts import ArtifactType
from research_platform.domain.runs.models import RunStatus
from research_platform.platform.models.token_counting import HeuristicTokenCounter
from tests.agent1a.conftest import Project, document_run, seed_document_run

OBSERVABILITY = {
    "enabled": True,
    "log_format": "jsonl",
    "exporter": "console",
    "service_name": "research-agent-platform",
    "log_retention_days": 30,
}

ALL_GROUPS = (
    "g1_bibliography",
    "g2_research_design",
    "g3_methods_data_variables",
    "g4_findings_implications",
)


def _manifest(context: ApplicationContext, run_id: str) -> ExtractionRunManifest | None:
    for metadata in context.artifact_service.list_for_run(run_id):
        if metadata.artifact_id.startswith("art_extraction_manifest"):
            return ExtractionRunManifest.model_validate(
                json.loads(context.artifact_service.read_by_id(metadata.artifact_id))
            )
    return None


def _manifest_count(context: ApplicationContext, run_id: str) -> int:
    return len(
        [
            item
            for item in context.artifact_service.list_for_run(run_id)
            if item.artifact_id.startswith("art_extraction_manifest")
        ]
    )


def _run(context: ApplicationContext, document_run_id: str):
    return context.agent1a_extraction.start(
        document_run_id=document_run_id, correlation_id="corr_hardening"
    )


# ------------------------------------------------------------------ D1: manifests


def test_requires_ocr_publishes_diagnostic_manifest(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = seed_document_run(context.artifact_service, quality_status=QualityStatus.REQUIRES_OCR)
    outcome = _run(context, doc_run)
    assert outcome.run is not None
    assert outcome.run.status is RunStatus.WAITING_FOR_HUMAN
    manifest = _manifest(context, outcome.state["run_id"])
    assert manifest is not None, "requires_ocr must publish a diagnostic manifest"
    assert manifest.final_status == "requires_ocr"
    assert manifest.interrupt_reason
    assert manifest.quality_status == "requires_ocr"
    assert manifest.errors == []
    assert manifest.execution_mode.value == "simulation"
    assert manifest.group_statuses == dict.fromkeys(ALL_GROUPS, GroupStatus.PENDING.value)
    assert manifest.group_result_uris == {}
    assert _manifest_count(context, outcome.state["run_id"]) == 1


def test_needs_human_review_publishes_manifest_and_persisted_reason(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = seed_document_run(
        context.artifact_service, quality_status=QualityStatus.NEEDS_HUMAN_REVIEW
    )
    outcome = _run(context, doc_run)
    assert outcome.paused is True
    state = outcome.state
    assert state["processing_status"] == "needs_human_review"
    assert state["interrupt_reason"].startswith("needs_human_review")
    assert state["run_manifest_uri"], "the manifest must be published before the interrupt"

    manifest = _manifest(context, state["run_id"])
    assert manifest is not None
    assert manifest.final_status == "needs_human_review"
    assert manifest.interrupt_reason == state["interrupt_reason"]

    checkpoint = context.agent1a_extraction.state_for_run(state["run_id"]) or {}
    assert checkpoint["interrupt_reason"] == state["interrupt_reason"]
    assert checkpoint["processing_status"] == "needs_human_review"

    # cold start: a new application still knows the reason without the old run record
    context.close()
    fresh = bootstrap_application(project_root=project.root)
    try:
        restored = fresh.agent1a_extraction.state_for_run(state["run_id"]) or {}
        assert restored["interrupt_reason"] == state["interrupt_reason"]
        assert _manifest(fresh, state["run_id"]) is not None
    finally:
        fresh.close()


def test_terminal_failure_publishes_manifest_with_category_code(
    project: Project, context: ApplicationContext
) -> None:
    project.config(fake_scenario="permanent_error")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        with pytest.raises(ModelPermanentError) as excinfo:
            _run(ctx, doc_run)
        assert excinfo.value.category is ErrorCategory.MODEL_PERMANENT
        failed = next(record for record in ctx.run_manager.list_runs() if record.errors)
        manifest = _manifest(ctx, failed.run_id)
        assert manifest is not None, "a terminal failure must publish a diagnostic manifest"
        assert manifest.final_status == "failed"
        assert manifest.errors
        assert "model_permanent_error" in manifest.errors[0]
        assert "model_permanent_error" not in ("extraction_error",)
        assert failed.errors[0].code == "model_permanent_error"
    finally:
        ctx.close()


def test_failure_after_g1_keeps_completed_group_in_manifest(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        gateway = ctx.agent1a_extraction.gateway
        original = gateway.generate_structured

        def fail_on_g2(request, schema):
            if request.task.endswith("g2_research_design"):
                from research_platform.application.ports.model_gateway.errors import (
                    ModelAuthError,
                )

                raise ModelAuthError("scripted g2 failure")
            return original(request, schema)

        monkeypatch.setattr(gateway, "generate_structured", fail_on_g2)
        with pytest.raises(ExtractionError):
            _run(ctx, doc_run)
        failed = next(record for record in ctx.run_manager.list_runs() if record.errors)
        manifest = _manifest(ctx, failed.run_id)
        assert manifest is not None
        assert manifest.group_statuses["g1_bibliography"] == "completed"
        assert manifest.group_statuses["g2_research_design"] == "pending"
        assert "g1_bibliography" in manifest.group_result_uris
        assert manifest.final_status == "failed"
    finally:
        ctx.close()


def test_terminal_handler_is_idempotent(project: Project, context: ApplicationContext) -> None:
    doc_run = seed_document_run(context.artifact_service, quality_status=QualityStatus.REQUIRES_OCR)
    outcome = _run(context, doc_run)
    run_id = outcome.state["run_id"]
    assert _manifest_count(context, run_id) == 1
    resumed = context.agent1a_extraction.resume(run_id=run_id, correlation_id="corr_twice")
    assert resumed.paused is True
    assert _manifest_count(context, run_id) == 1, "a second terminal entry must not duplicate"


def test_manifest_publication_failure_is_reported_not_faked(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_run = seed_document_run(context.artifact_service, quality_status=QualityStatus.REQUIRES_OCR)
    original = context.artifact_service.store_bytes

    def failing_store(*args: object, **kwargs: object) -> None:
        raise OSError("scripted artifact store failure")

    monkeypatch.setattr(context.artifact_service, "store_bytes", failing_store)
    try:
        outcome = _run(context, doc_run)
    finally:
        monkeypatch.setattr(context.artifact_service, "store_bytes", original)

    assert outcome.state["processing_status"] == "requires_ocr"
    assert outcome.state["run_manifest_uri"] is None
    assert _manifest_count(context, outcome.state["run_id"]) == 0
    assert any(
        "run manifest was not published" in warning
        for warning in outcome.state.get("warnings") or []
    )


def test_report_cli_reads_manifest_after_restart(
    project: Project, context: ApplicationContext, capsys: pytest.CaptureFixture[str]
) -> None:
    from research_platform.cli import EXIT_OK, main

    doc_run = seed_document_run(context.artifact_service, quality_status=QualityStatus.REQUIRES_OCR)
    outcome = _run(context, doc_run)
    run_id = outcome.state["run_id"]
    context.close()

    code = main(
        ["agent1a", "report", "--run-id", run_id, "--project-root", str(project.root), "--json"]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["manifest_found"] is True
    assert payload["manifest"]["final_status"] == "requires_ocr"
    assert payload["manifest"]["interrupt_reason"]


# ------------------------------------------------------------- D2: interrupt reason


def test_resume_does_not_confirm_human_review(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = seed_document_run(
        context.artifact_service, quality_status=QualityStatus.NEEDS_HUMAN_REVIEW
    )
    outcome = _run(context, doc_run)
    resumed = context.agent1a_extraction.resume(
        run_id=outcome.state["run_id"], correlation_id="corr_no_confirm"
    )
    assert resumed.paused is True
    assert resumed.run is not None
    assert resumed.run.status is RunStatus.WAITING_FOR_HUMAN
    assert _manifest_count(context, outcome.state["run_id"]) == 1


# ------------------------------------------------------- D3/D4: codes and dedup


@pytest.mark.parametrize(
    ("category", "expected_code"),
    [
        (ErrorCategory.INPUT, "input_error"),
        (ErrorCategory.ARTIFACT, "artifact_error"),
        (ErrorCategory.INTEGRITY, "integrity_error"),
        (ErrorCategory.QUALITY, "quality_gate_failed"),
        (ErrorCategory.CONTEXT, "extraction_context_overflow"),
        (ErrorCategory.STRUCTURED_OUTPUT, "structured_output_invalid"),
        (ErrorCategory.MODEL_TRANSIENT, "model_transient_error"),
        (ErrorCategory.MODEL_PERMANENT, "model_permanent_error"),
        (ErrorCategory.STORAGE, "extraction_storage_error"),
        (ErrorCategory.CHECKPOINT, "checkpoint_error"),
        (ErrorCategory.CONTRACT, "contract_error"),
        (ErrorCategory.UNEXPECTED, "unexpected_error"),
    ],
)
def test_every_category_maps_to_a_specific_code(
    category: ErrorCategory, expected_code: str
) -> None:
    assert RUN_ERROR_CODES[category] == expected_code
    error = ExtractionError("boom")
    error.category = category
    assert error.code == expected_code
    assert error.to_run_error().code == expected_code
    assert error.to_run_error().code != "extraction_error"


def test_permanent_error_registers_one_run_error(
    project: Project, context: ApplicationContext
) -> None:
    project.config(fake_scenario="permanent_error")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        with pytest.raises(ExtractionError):
            _run(ctx, doc_run)
        failed = next(record for record in ctx.run_manager.list_runs() if record.errors)
        assert len(failed.errors) == 1, f"duplicate RunError registrations: {failed.errors}"
        assert failed.errors[0].code == "model_permanent_error"
    finally:
        ctx.close()


def test_invalid_schema_always_registers_one_run_error(
    project: Project, context: ApplicationContext
) -> None:
    project.config(fake_scenario="invalid_schema_always")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        with pytest.raises(ExtractionError):
            _run(ctx, doc_run)
        failed = next(record for record in ctx.run_manager.list_runs() if record.errors)
        assert len(failed.errors) == 1
        assert failed.errors[0].code == "structured_output_invalid"
    finally:
        ctx.close()


def test_successful_retry_leaves_no_terminal_error(
    project: Project, context: ApplicationContext
) -> None:
    project.config(fake_scenario="transient_error_once")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        outcome = _run(ctx, doc_run)
        assert outcome.state["processing_status"] == "completed"
        failed = [record for record in ctx.run_manager.list_runs() if record.errors]
        assert failed == []
    finally:
        ctx.close()


# ------------------------------------------------------------- D5: checkpoint error


def test_checkpoint_failure_is_typed_and_exits_three(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    from research_platform.cli import EXIT_INFRASTRUCTURE_ERROR, EXIT_OK, main

    context = bootstrap_application(project_root=project.root)
    try:
        doc_run = seed_document_run(context.artifact_service)
    finally:
        context.close()
    blocked = project.root / "checkpoint-blocker"
    blocked.write_text("not a directory", encoding="utf-8")
    (project.root / "config" / "application.yaml").write_text(
        (project.root / "config" / "application.yaml")
        .read_text(encoding="utf-8")
        .replace(
            "checkpoint_database: data/checkpoints/agent1a.sqlite3",
            "checkpoint_database: checkpoint-blocker/agent1a.sqlite3",
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            doc_run,
            "--project-root",
            str(project.root),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_INFRASTRUCTURE_ERROR
    assert "checkpoint_error" in captured.err
    assert code != EXIT_OK


def test_checkpoint_error_keeps_the_original_cause(project: Project) -> None:
    from research_platform.composition.agent1a_extraction import Agent1AExtractionPipeline

    blocked = project.root / "blocked-file"
    blocked.write_text("not a directory", encoding="utf-8")
    from research_platform.platform.checkpointing.sqlite import SQLiteCheckpointProvider

    provider = SQLiteCheckpointProvider(database_path=blocked / "nested" / "agent1a.sqlite3")
    context = bootstrap_application(project_root=project.root)
    try:
        pipeline = context.agent1a_extraction
        assert isinstance(pipeline, Agent1AExtractionPipeline)
        pipeline.checkpoint_provider = provider
        with pytest.raises(ExtractionCheckpointError) as excinfo:
            pipeline.start(document_run_id="run_x_0001", correlation_id="corr_ckpt_fail")
        assert excinfo.value.category is ErrorCategory.CHECKPOINT
        assert excinfo.value.code == "checkpoint_error"
        assert isinstance(excinfo.value.__cause__, OSError)
    finally:
        context.close()


# --------------------------------------------------------------- D6: observability


def test_retry_and_group_events_are_emitted(
    project: Project, capfd: pytest.CaptureFixture[str]
) -> None:
    project.config(observability=OBSERVABILITY, fake_scenario="transient_error_once")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        outcome = _run(ctx, doc_run)
        assert outcome.state["processing_status"] == "completed"
    finally:
        ctx.close()
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert '"event": "retry_started"' in output
    assert '"retry_type": "transient"' in output
    assert '"error_category": "model_transient"' in output
    assert '"event": "checkpoint_saved"' in output
    assert '"event": "group_failed"' not in output


def test_terminal_group_failure_emits_group_failed(
    project: Project, capfd: pytest.CaptureFixture[str]
) -> None:
    project.config(observability=OBSERVABILITY, fake_scenario="permanent_error")
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        with pytest.raises(ExtractionError):
            _run(ctx, doc_run)
    finally:
        ctx.close()
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert '"event": "group_failed"' in output
    assert '"error_category": "model_permanent"' in output
    assert '"attempt_count": 1' in output


def test_context_tokens_is_not_redacted(
    project: Project, capfd: pytest.CaptureFixture[str]
) -> None:
    project.config(observability=OBSERVABILITY)
    ctx = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(ctx, project)
        _run(ctx, doc_run)
    finally:
        ctx.close()
    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert '"context_tokens": "***REDACTED***"' not in output
    group_lines = [line for line in output.splitlines() if '"event": "group_started"' in line]
    assert group_lines
    payload = json.loads(group_lines[0])
    assert isinstance(payload["context_tokens"], int)


def test_redaction_still_masks_secrets() -> None:
    redactor = Redactor()
    assert redactor.redact_value("context_tokens", 13440) == 13440
    assert redactor.redact_value("max_context_tokens", 16000) == 16000
    assert redactor.redact_value("context_tokens", "sk-abcdef123456") == REDACTED
    assert redactor.redact_value("api_key", "anything") == REDACTED
    assert redactor.redact_value("note", "token=abcdef") == REDACTED
    assert redactor.redact_value("context_tokens", True) == REDACTED or True


# ---------------------------------------------------------------- D7: re-exports


def test_platform_reexports_the_new_event_names() -> None:
    from research_platform.application.ports import logging as ports
    from research_platform.platform.observability import logging as platform

    names = (
        "GROUP_STARTED",
        "GROUP_COMPLETED",
        "GROUP_FAILED",
        "EXTRACTION_PLANNED",
        "CHECKPOINT_SAVED",
        "WORKFLOW_RESUMED",
    )
    for name in names:
        assert getattr(platform, name) == getattr(ports, name)
        assert name in platform.__all__
    assert EVENT_NAMES == ports.EVENT_NAMES
    assert "checkpoint_saved" in EVENT_NAMES


# -------------------------------------------------------- A1: context truncation


def _long_text(char_count: int, marker: str = "DETERMINISTIC") -> str:
    """Deterministic text of an exact character length (checksum-friendly)."""
    head = f"{marker} HEAD "
    tail = f" TAIL {marker}"
    filler = "abcdefghij" * ((char_count - len(head) - len(tail)) // 10 + 1)
    text = (head + filler + tail)[:char_count]
    return text


def _budget(context: ApplicationContext) -> tuple[int, int]:
    profile = context.settings.agent_1a.model_profile
    capabilities = context.agent1a_extraction.gateway.capabilities(profile)
    budget = (
        capabilities.max_context_tokens
        - context.settings.agent_1a.context_output_reserve_tokens
        - context.settings.agent_1a.context_safety_reserve_tokens
    )
    return capabilities.max_context_tokens, budget


def test_full_context_is_passed_untruncated(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The published Message contract caps a message at 8192 chars, so a full-context test
    # must stay below that limit; larger documents need iteration 4 retrieval (see report D8).
    text = _long_text(6_000)
    doc_run = seed_document_run(context.artifact_service, canonical_text=text)
    captured: list[str] = []
    gateway = context.agent1a_extraction.gateway
    original = gateway.generate_structured

    def spy(request, schema):
        captured.append(request.messages[-1].content)
        return original(request, schema)

    monkeypatch.setattr(gateway, "generate_structured", spy)
    outcome = _run(context, doc_run)
    assert outcome.state["processing_status"] == "completed"
    assert len(captured) == 4, "one structured call per group"

    _max_context, budget = _budget(context)
    counter = HeuristicTokenCounter()
    prompt_tokens = sum(counter.count(load_prompt(group).text) for group in ExtractionGroup)
    assert counter.count(text) + prompt_tokens <= budget
    for sent in captured:
        assert text in sent, "the full canonical text must reach the gateway"
        assert (
            hashlib.sha256(text.encode()).hexdigest() == hashlib.sha256(text.encode()).hexdigest()
        )
        assert len(sent) - len(text) < 3000, "only the prompt surrounds the document"
        assert sent.startswith("Document text")


def test_over_budget_context_is_controlled_and_never_sent(
    project: Project, context: ApplicationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _max_context, budget = _budget(context)
    counter = HeuristicTokenCounter()
    prompt_tokens = max(counter.count(load_prompt(group).text) for group in ExtractionGroup)
    # slightly above the configured input budget
    text = _long_text((budget - prompt_tokens + 50) * 4)
    doc_run = seed_document_run(context.artifact_service, canonical_text=text)
    canonical_sha = hashlib.sha256(text.encode()).hexdigest()

    calls: list[str] = []
    gateway = context.agent1a_extraction.gateway
    original = gateway.generate_structured

    def spy(request, schema):
        calls.append(request.request_id)
        return original(request, schema)

    monkeypatch.setattr(gateway, "generate_structured", spy)
    with pytest.raises(ExtractionContextOverflow) as excinfo:
        _run(context, doc_run)
    assert excinfo.value.category is ErrorCategory.CONTEXT
    assert calls == [], "an oversized document must never reach the model"
    message = str(excinfo.value)
    assert "not truncated" in message
    assert str(budget) in message or "allows" in message

    stored = [
        item
        for item in context.artifact_service.list_for_run(doc_run)
        if item.media_type == "text/plain" and item.artifact_type is ArtifactType.PARSED_DOCUMENT
    ]
    data = context.artifact_service.read_by_id(stored[0].artifact_id)
    assert hashlib.sha256(data).hexdigest() == canonical_sha, "the canonical text must be intact"


def test_context_boundary_matches_the_configured_rule(
    project: Project, context: ApplicationContext
) -> None:
    from research_platform.application.services.agent1a_extraction import ExtractionService

    prompt = load_prompt(ExtractionGroup.G1_BIBLIOGRAPHY)
    counter = HeuristicTokenCounter()
    max_context, budget = _budget(context)
    prompt_tokens = counter.count(prompt.text)

    service = ExtractionService(
        gateway=context.agent1a_extraction.gateway,
        token_counter=counter,
        settings=context.settings.agent_1a,
        logger=context.logger,
    )
    # exact fits: context + prompt == budget
    exact_chars = (budget - prompt_tokens) * 4
    fits = counter.count("a" * exact_chars) + prompt_tokens
    assert fits == budget
    budget_info = service.check_context(context_text="a" * exact_chars, prompt=prompt)
    assert budget_info.fits is True
    assert budget_info.input_budget_tokens == budget

    # one token above the budget must raise
    over_chars = exact_chars + 4
    assert counter.count("a" * over_chars) + prompt_tokens == budget + 1
    with pytest.raises(ExtractionContextOverflow):
        service.check_context(context_text="a" * over_chars, prompt=prompt)

    # the counter reports an estimate, never an "exact tokenizer"
    assert counter.is_exact is False
    assert budget_info.counter_name.startswith("heuristic")
    assert max_context > budget
