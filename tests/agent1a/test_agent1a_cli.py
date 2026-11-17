"""CLI tests of the Agent 1A extraction commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_platform.app import bootstrap_application
from research_platform.cli import (
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
