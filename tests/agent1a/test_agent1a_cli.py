"""CLI tests of the Agent 1A extraction commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_platform.app import bootstrap_application
from research_platform.cli import (
    EXIT_CONTRACT_VIOLATION,
    EXIT_CONTROLLED_STOP,
    EXIT_INPUT_ERROR,
    EXIT_OK,
    main,
)
from research_platform.domain.documents.quality import QualityStatus
from tests.agent1a.conftest import Project, document_run, seed_document_run, write_txt


def _document_run_id(project: Project) -> str:
    context = bootstrap_application(project_root=project.root)
    try:
        return document_run(context, project)
    finally:
        context.close()


def test_extract_reports_a_completed_run(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    document_run_id = _document_run_id(project)
    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            document_run_id,
            "--project-root",
            str(project.root),
            "--json",
        ]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_id"].startswith("run_")
    assert payload["document_run_id"] == document_run_id
    assert payload["processing_status"] == "completed"
    assert payload["execution_mode"] == "simulation"
    assert payload["provider_id"] == "fake"
    assert set(payload["completed_groups"]) == {
        "g1_bibliography",
        "g2_research_design",
        "g3_methods_data_variables",
        "g4_findings_implications",
    }
    assert payload["run_manifest_uri"].startswith("artifact://documents/")

    report_code = main(
        [
            "agent1a",
            "report",
            "--run-id",
            payload["run_id"],
            "--project-root",
            str(project.root),
            "--json",
        ]
    )
    assert report_code == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["manifest_found"] is True
    assert report["manifest"]["execution_mode"] == "simulation"
    assert report["manifest"]["final_status"] == "completed"
    assert report["checkpoint_found"] is True


def test_extract_of_an_unknown_document_run_fails(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            "run_missing_0001",
            "--project-root",
            str(project.root),
        ]
    )
    assert code == EXIT_INPUT_ERROR
    captured = capsys.readouterr()
    assert "artifact" in captured.err.lower()


def test_extract_stops_on_requires_ocr(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    context = bootstrap_application(project_root=project.root)
    try:
        document_run_id = seed_document_run(
            context.artifact_service, quality_status=QualityStatus.REQUIRES_OCR
        )
    finally:
        context.close()
    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            document_run_id,
            "--project-root",
            str(project.root),
            "--json",
        ]
    )
    assert code == EXIT_CONTROLLED_STOP
    payload = json.loads(capsys.readouterr().out)
    assert payload["processing_status"] == "requires_ocr"
    assert payload["interrupt_reason"].startswith("requires_ocr")
    assert payload["completed_groups"] == []


def test_resume_of_an_unknown_run_is_an_input_error(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["agent1a", "resume", "--run-id", "run_unknown_0001", "--project-root", str(project.root)]
    )
    assert code == EXIT_INPUT_ERROR
    assert "checkpoint" in capsys.readouterr().err.lower()


def test_report_of_an_unknown_run_is_an_input_error(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["agent1a", "report", "--run-id", "run_unknown_0002", "--project-root", str(project.root)]
    )
    assert code == EXIT_INPUT_ERROR
    assert "no extraction manifest" in capsys.readouterr().err.lower()


def test_extract_without_profiles_is_a_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "empty-project"
    root.mkdir()
    (root / "config").mkdir()
    (root / "config" / "application.yaml").write_text(
        "app:\n  environment: local\n", encoding="utf-8"
    )
    code = main(
        ["agent1a", "extract", "--document-run-id", "run_x_0001", "--project-root", str(root)]
    )
    assert code == 2
    assert "model profile" in capsys.readouterr().err.lower()


def test_resume_does_not_confirm_a_human_review(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    context = bootstrap_application(project_root=project.root)
    try:
        document_run_id = seed_document_run(
            context.artifact_service, quality_status=QualityStatus.NEEDS_HUMAN_REVIEW
        )
    finally:
        context.close()
    extract_code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            document_run_id,
            "--project-root",
            str(project.root),
            "--json",
        ]
    )
    assert extract_code == EXIT_CONTROLLED_STOP
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    resume_code = main(
        ["agent1a", "resume", "--run-id", run_id, "--project-root", str(project.root)]
    )
    assert resume_code == EXIT_CONTROLLED_STOP
    assert "manual review required" in capsys.readouterr().out.lower()


def _document_scoped_manifest(root: Path, document_run_id: str, document_id: str | None = None):
    """Read the Agent 1A manifest through the document scope (RunRepository is in-memory)."""
    import json as _json

    from research_platform.app import bootstrap_application
    from research_platform.domain.extraction.models import ExtractionRunManifest

    context = bootstrap_application(project_root=root)
    try:
        if document_id is None:
            document_id = context.artifact_service.list_for_run(document_run_id)[0].document_id
        manifests = [
            item
            for item in context.artifact_service.list_for_document(document_id)
            if item.artifact_id.startswith("art_extraction_manifest")
        ]
        if not manifests:
            return None
        return ExtractionRunManifest.model_validate(
            _json.loads(context.artifact_service.read_by_id(manifests[0].artifact_id))
        )
    finally:
        context.close()


def test_context_overflow_is_terminal_failure_with_exit_one(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    """Controlled *failure* (Run failed + RunError + manifest) is exit 1, not stop 4."""
    text = "D8-CONTRACT-CHECK " + "a" * 20_000
    context = bootstrap_application(project_root=project.root)
    try:
        document_run_id = seed_document_run(context.artifact_service, canonical_text=text)
    finally:
        context.close()

    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            document_run_id,
            "--project-root",
            str(project.root),
        ]
    )
    captured = capsys.readouterr()
    assert code == EXIT_CONTRACT_VIOLATION
    assert code != EXIT_CONTROLLED_STOP
    assert "extraction_context_overflow" in captured.err
    assert "max_chars=8192" in captured.err

    manifest = _document_scoped_manifest(project.root, document_run_id)
    assert manifest is not None
    assert manifest.final_status == "failed"
    assert manifest.errors
    assert "extraction_context_overflow" in manifest.errors[0]


def test_quality_failed_is_terminal_failure_not_controlled_stop(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    context = bootstrap_application(project_root=project.root)
    try:
        document_run_id = seed_document_run(
            context.artifact_service, quality_status=QualityStatus.FAILED
        )
    finally:
        context.close()
    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            document_run_id,
            "--project-root",
            str(project.root),
        ]
    )
    capsys.readouterr()
    assert code == EXIT_CONTRACT_VIOLATION
    assert code != EXIT_CONTROLLED_STOP

    manifest = _document_scoped_manifest(project.root, document_run_id)
    assert manifest is not None
    assert manifest.final_status == "failed"
    assert manifest.errors
    assert "quality_gate_failed" in manifest.errors[0]


@pytest.mark.parametrize(
    "status",
    [QualityStatus.REQUIRES_OCR, QualityStatus.NEEDS_HUMAN_REVIEW],
    ids=["requires_ocr", "needs_human_review"],
)
def test_controlled_stops_keep_exit_four(
    project: Project, capsys: pytest.CaptureFixture[str], status: QualityStatus
) -> None:
    """A controlled stop waits for a human: exit 4, ``waiting_for_human``, no RunError."""
    context = bootstrap_application(project_root=project.root)
    try:
        document_run_id = seed_document_run(context.artifact_service, quality_status=status)
    finally:
        context.close()
    code = main(
        [
            "agent1a",
            "extract",
            "--document-run-id",
            document_run_id,
            "--project-root",
            str(project.root),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_CONTROLLED_STOP
    assert code != EXIT_CONTRACT_VIOLATION
    assert payload["processing_status"] == status.value
    assert payload["run_status"] == "waiting_for_human"

    manifest = _document_scoped_manifest(project.root, document_run_id)
    assert manifest is not None
    assert manifest.final_status == status.value
    assert manifest.errors == [], "a controlled stop must not carry a RunError"


def test_document_commands_are_unchanged(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_txt(project)
    code = main(
        ["document", "process", "--input", str(path), "--project-root", str(project.root), "--json"]
    )
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["quality_status"] == "passed"
    report_code = main(
        ["document", "report", "--run-id", payload["run_id"], "--project-root", str(project.root)]
    )
    assert report_code == EXIT_OK
    assert "quality report" in capsys.readouterr().out.lower()
