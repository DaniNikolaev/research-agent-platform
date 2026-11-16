"""LangGraph document-workflow integration tests (TZ §11).

Each test drives the real graph through the composition root: settings → adapters → services
→ workflow → artefacts.  The only substitution is inside the failure tests, where a documented
defect is injected on purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_platform.app import ApplicationContext, bootstrap_application
from research_platform.application.services.document_errors import (
    DocumentIngestionError,
    DocumentPipelineError,
)
from research_platform.composition.document_pipeline import document_dependencies
from research_platform.domain.documents.quality import QualityStatus
from research_platform.domain.runs.models import RunStatus
from research_platform.workflows.document_workflow import DocumentWorkflow

pytestmark = pytest.mark.parsing

EXPECTED_ARTIFACT_TYPES = {
    "original_document",
    "handoff_package",
    "parsed_document",
    "quality_report",
    "run_log",
}

EN_TEXT = (
    "Deterministic Extraction\n\n"
    "The pipeline parses a text source into blocks and keeps line numbers.\n"
    "It never invents physical pages for a text document.\n\n"
    "Conclusion\n\n"
    "The behaviour is reproducible.\n"
)


def _context(tmp_path: Path) -> ApplicationContext:
    return bootstrap_application(project_root=tmp_path)


def _workflow(context: ApplicationContext) -> DocumentWorkflow:
    dependencies = document_dependencies(
        settings=context.settings,
        pipeline=context.document_pipeline,
        run_manager=context.run_manager,
        artifacts=context.artifact_service,
        logger=context.logger,
    )
    return context.document_pipeline.workflow(dependencies)


def _write_txt(tmp_path: Path, name: str = "note.txt", text: str = EN_TEXT) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------- successes


def test_txt_document_completes_with_a_passing_verdict(tmp_path: Path) -> None:
    context = _context(tmp_path)
    try:
        path = _write_txt(tmp_path)
        result = _workflow(context).run(input_path=str(path))
        assert result.status is RunStatus.COMPLETED
        assert result.quality_report.overall_status is QualityStatus.PASSED
        assert result.state["page_count"] is None
        assert result.state["source_type"] == "txt"
        assert result.state["parsed_document_uri"]
        assert result.state["quality_report_uri"]

        artifacts = context.artifact_service.list_for_run(result.run_id)
        kinds = {metadata.artifact_type.value for metadata in artifacts}
        assert kinds >= EXPECTED_ARTIFACT_TYPES
        assert len(artifacts) >= 6
        for metadata in artifacts:
            assert metadata.uri.startswith(
                f"artifact://documents/{result.document_id}/runs/{result.run_id}/"
            )
            assert metadata.sha256
    finally:
        context.close()


def test_pdf_document_completes_with_real_docling(tmp_path: Path) -> None:
    from tests.parsing.pdf_fixtures import build_fixture

    context = _context(tmp_path)
    try:
        pdf = build_fixture("single_page_en", tmp_path / "fixtures")
        result = _workflow(context).run(input_path=str(pdf))
        assert result.status is RunStatus.COMPLETED
        assert result.quality_report.overall_status.is_ready
        assert result.state["page_count"] == 1
        assert result.state["parser_name"] == "docling"
        assert result.state["used_fallback"] is False
    finally:
        context.close()


# ---------------------------------------------------------------- controlled stops


def test_scanned_pdf_stops_and_keeps_diagnostics(tmp_path: Path) -> None:
    from tests.parsing.pdf_fixtures import build_fixture

    context = _context(tmp_path)
    try:
        pdf = build_fixture("scanned_only", tmp_path / "fixtures")
        result = _workflow(context).run(input_path=str(pdf))
        assert result.status is RunStatus.WAITING_FOR_HUMAN
        assert result.quality_report.overall_status is QualityStatus.REQUIRES_OCR
        assert result.quality_report.ocr_required_pages == [1]
        assert result.state["stop_reason"].startswith("ocr_required")
        artifacts = context.artifact_service.list_for_run(result.run_id)
        assert any(item.artifact_type.value == "quality_report" for item in artifacts)
        assert any(item.artifact_type.value == "run_log" for item in artifacts)
    finally:
        context.close()


# ------------------------------------------------------------------- input errors


def test_missing_input_fails_before_a_run_is_created(tmp_path: Path) -> None:
    context = _context(tmp_path)
    try:
        with pytest.raises(DocumentIngestionError) as excinfo:
            _workflow(context).run(input_path=str(tmp_path / "absent.pdf"))
        assert excinfo.value.code == "source_not_found"
        assert context.run_manager.list_runs() == []
    finally:
        context.close()


def test_unsupported_extension_is_rejected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    try:
        path = tmp_path / "article.docx"
        path.write_bytes(b"PK\x03\x04 not a pdf")
        with pytest.raises(DocumentIngestionError) as excinfo:
            _workflow(context).run(input_path=str(path))
        assert excinfo.value.code == "unsupported_format"
        assert context.run_manager.list_runs() == []
    finally:
        context.close()


# ----------------------------------------------------------------- store failures


def test_artifact_store_failure_marks_the_run_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    try:
        path = _write_txt(tmp_path)

        def boom(*_: object, **__: object) -> None:
            raise OSError("disk unavailable")

        monkeypatch.setattr(context.artifact_store, "put", boom)
        with pytest.raises(DocumentPipelineError):
            _workflow(context).run(input_path=str(path))

        failed = context.run_manager.list_runs(RunStatus.FAILED)
        assert len(failed) == 1
        assert failed[0].errors
        assert failed[0].errors[0].correlation_id
        assert context.run_manager.list_runs(RunStatus.COMPLETED) == []
    finally:
        context.close()


# -------------------------------------------------------------------- idempotency


def test_reprocessing_creates_a_new_run_without_overwriting(tmp_path: Path) -> None:
    context = _context(tmp_path)
    try:
        path = _write_txt(tmp_path)
        workflow = _workflow(context)
        first = workflow.run(input_path=str(path))
        second = workflow.run(input_path=str(path))

        assert first.run_id != second.run_id
        assert first.document_id == second.document_id, "the same bytes must map to one id"
        assert first.state["source_artifact_uri"] != second.state["source_artifact_uri"]

        first_artifacts = context.artifact_service.list_for_run(first.run_id)
        second_artifacts = context.artifact_service.list_for_run(second.run_id)
        assert len(first_artifacts) == len(second_artifacts) >= 6
        assert {item.artifact_id for item in first_artifacts}.isdisjoint(
            {item.artifact_id for item in second_artifacts}
        )
        # the earlier run is still fully readable
        assert context.artifact_service.read_by_id(first_artifacts[0].artifact_id)
        assert len(context.run_manager.list_runs()) == 2
    finally:
        context.close()


# ------------------------------------------------------------------- state hygiene


def test_graph_state_stays_compact(tmp_path: Path) -> None:
    context = _context(tmp_path)
    try:
        path = _write_txt(tmp_path)
        result = _workflow(context).run(input_path=str(path))
        serialized = json.dumps(result.state, default=str)
        assert len(serialized) < 4096, "GraphState must not carry payloads"
        assert EN_TEXT not in serialized
        assert "elements" not in result.state
        assert "canonical_text" not in result.state
        for key in ("parsed_document_uri", "canonical_text_uri", "quality_report_uri"):
            assert result.state[key].startswith("artifact://")
    finally:
        context.close()


def test_smoke_workflow_is_unaffected(tmp_path: Path) -> None:
    context = _context(tmp_path)
    try:
        runtime = context.runtime_factory.build_agent_1a()
        result = runtime.run()
        assert result.status is RunStatus.COMPLETED_WITH_WARNINGS
        assert result.technical_trace_ref
    finally:
        context.close()
