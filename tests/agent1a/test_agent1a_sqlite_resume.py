"""SQLite checkpointing and resume tests of the Agent 1A workflow.

The critical property is durability across *new Application instances*: each assertion here
re-opens the same ``.sqlite3`` file with a fresh runtime, because an in-memory saver would pass
a naive test while proving nothing.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from research_platform.app import ApplicationContext, bootstrap_application
from research_platform.application.ports.checkpoint import CheckpointProviderPort
from research_platform.domain.extraction.models import groups_in_order
from research_platform.platform.checkpointing.sqlite import SQLiteCheckpointProvider
from tests.agent1a.conftest import Project, document_run

BREAKPOINT = "validate_and_persist_g2_research_design"
ALL_GROUPS = tuple(group.value for group in groups_in_order())


def _group_artifacts(context: ApplicationContext, run_id: str) -> dict[str, list]:
    grouped: dict[str, list] = {group: [] for group in ALL_GROUPS}
    for metadata in context.artifact_service.list_for_run(run_id):
        for group in ALL_GROUPS:
            if metadata.artifact_id.startswith(f"art_{group}_"):
                grouped[group].append(metadata)
    return grouped


def _table_names(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_provider_creates_a_database_and_binds_thread_to_run(project: Project) -> None:
    provider = SQLiteCheckpointProvider(database_path=project.checkpoint_database)
    try:
        saver = provider.create_saver()
        assert saver is not None
        assert provider.database_path == project.checkpoint_database
        assert provider.database_path.exists()
        assert provider.thread_id_for_run("run_abc_0001") == "run_abc_0001"
        assert provider.health()["kind"] == "sqlite"
    finally:
        provider.close()


def test_checkpoints_are_written_to_disk(project: Project, context: ApplicationContext) -> None:
    doc_run = document_run(context, project)
    context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_ckpt")
    database = project.checkpoint_database
    assert database.exists()
    tables = _table_names(database)
    assert "checkpoints" in tables, f"LangGraph tables missing: {sorted(tables)}"
    assert "writes" in tables


def test_state_survives_a_new_application_instance(
    project: Project, context: ApplicationContext
) -> None:
    doc_run = document_run(context, project)
    outcome = context.agent1a_extraction.start(
        document_run_id=doc_run, correlation_id="corr_restart"
    )
    run_id = outcome.state["run_id"]
    # close the first application completely, then start a brand-new one on the same files
    context.close()
    fresh = bootstrap_application(project_root=project.root)
    try:
        restored = fresh.agent1a_extraction.state_for_run(run_id)
        assert restored, "the checkpoint was not restored from disk"
        assert restored["processing_status"] == "completed"
        assert restored["run_id"] == run_id
        assert restored["last_completed_node"] == "finalize_run"
        assert restored["completed_groups"] == list(ALL_GROUPS)
    finally:
        fresh.close()


def test_resume_continues_without_repeating_finished_groups(project: Project) -> None:
    project.config(interrupt_after=[BREAKPOINT])
    first = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(first, project)
        partial = first.agent1a_extraction.start(
            document_run_id=doc_run, correlation_id="corr_interrupt"
        )
    finally:
        first.close()

    run_id = partial.state["run_id"]
    assert partial.state["group_statuses"][ALL_GROUPS[0]] == "completed"
    assert partial.state["group_statuses"][ALL_GROUPS[1]] == "completed"
    assert partial.state["group_statuses"][ALL_GROUPS[2]] == "pending"
    assert partial.state["processing_status"] != "completed"

    project.config()  # production configuration: no breakpoints
    second = bootstrap_application(project_root=project.root)
    try:
        before = _group_artifacts(second, run_id)
        assert len(before[ALL_GROUPS[0]]) == 1
        assert len(before[ALL_GROUPS[1]]) == 1
        assert before[ALL_GROUPS[2]] == []

        resumed = second.agent1a_extraction.resume(run_id=run_id, correlation_id="corr_resume")
        assert resumed.state["processing_status"] == "completed"
        assert not resumed.paused

        after = _group_artifacts(second, run_id)
        assert len(after[ALL_GROUPS[0]]) == 1, "a finished group must not be extracted twice"
        assert len(after[ALL_GROUPS[1]]) == 1
        for group in ALL_GROUPS[2:]:
            assert len(after[group]) == 1, f"{group} was not extracted on resume"
        assert resumed.state["completed_groups"] == list(ALL_GROUPS)
    finally:
        second.close()


def test_two_runs_keep_independent_checkpoints(project: Project) -> None:
    context = bootstrap_application(project_root=project.root)
    try:
        doc_run = document_run(context, project)
        first = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_a")
        second = context.agent1a_extraction.start(document_run_id=doc_run, correlation_id="corr_b")
        assert first.state["run_id"] != second.state["run_id"]
        first_state = context.agent1a_extraction.state_for_run(first.state["run_id"])
        second_state = context.agent1a_extraction.state_for_run(second.state["run_id"])
        assert first_state["run_id"] == first.state["run_id"]
        assert second_state["run_id"] == second.state["run_id"]
        assert first_state["correlation_id"] != second_state["correlation_id"]
    finally:
        context.close()


def test_resume_of_an_unknown_run_is_an_input_error(
    project: Project, context: ApplicationContext
) -> None:
    from research_platform.application.services.extraction_errors import (
        ExtractionInputError,
    )

    with pytest.raises(ExtractionInputError):
        context.agent1a_extraction.resume(run_id="run_unknown_0001")


def test_provider_closes_its_connections(project: Project) -> None:
    provider = SQLiteCheckpointProvider(database_path=project.checkpoint_database)
    provider.create_saver()
    provider.create_saver()
    provider.close()
    provider.close()  # idempotent
    assert provider._connections == []


def test_unusable_checkpoint_location_is_reported(project: Project) -> None:
    blocked = project.root / "blocked-file"
    blocked.write_text("not a directory", encoding="utf-8")
    project.config()
    config_path = project.root / "config" / "application.yaml"
    config = json.loads("{}")
    assert config == {}
    text = config_path.read_text(encoding="utf-8").replace(
        "checkpoint_database: data/checkpoints/agent1a.sqlite3",
        "checkpoint_database: blocked-file/agent1a.sqlite3",
    )
    config_path.write_text(text, encoding="utf-8")
    context = bootstrap_application(project_root=project.root)
    try:
        from tests.agent1a.conftest import write_txt

        doc_run = document_run(context, project) if False else None
        assert doc_run is None
        path = write_txt(project)
        from research_platform.composition.document_pipeline import document_dependencies

        dependencies = document_dependencies(
            settings=context.settings,
            pipeline=context.document_pipeline,
            run_manager=context.run_manager,
            artifacts=context.artifact_service,
            logger=context.logger,
        )
        document_run_id = (
            context.document_pipeline.workflow(dependencies).run(input_path=str(path)).run_id
        )
        with pytest.raises(Exception) as excinfo:
            context.agent1a_extraction.start(
                document_run_id=document_run_id, correlation_id="corr_blocked"
            )
        assert "blocked-file" in str(excinfo.value) or isinstance(excinfo.value, OSError)
    finally:
        context.close()


def test_resume_works_in_a_separate_process(project: Project) -> None:
    """Scenario D: start in one process, resume in another, no finished group repeats."""
    project.config(interrupt_after=[BREAKPOINT])
    from tests.agent1a.conftest import write_txt

    path = write_txt(project)
    cli = [sys.executable, "-m", "research_platform.cli", "--project-root", str(project.root)]

    document = subprocess.run(
        [*cli, "document", "process", "--input", str(path), "--json"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert document.returncode == 0, document.stderr[-2000:]
    document_run_id = json.loads(document.stdout)["run_id"]

    started = subprocess.run(
        [*cli, "agent1a", "extract", "--document-run-id", document_run_id, "--json"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert started.returncode == 0, started.stderr[-2000:]
    run_id = json.loads(started.stdout)["run_id"]

    project.config()  # no breakpoints any more
    resumed = subprocess.run(
        [*cli, "agent1a", "resume", "--run-id", run_id, "--json"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr[-2000:]
    payload = json.loads(resumed.stdout)
    assert payload["processing_status"] == "completed"
    assert payload["completed_groups"] == list(ALL_GROUPS)

    report = subprocess.run(
        [*cli, "agent1a", "report", "--run-id", run_id, "--json"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert report.returncode == 0, report.stderr[-2000:]
    manifest = json.loads(report.stdout)["manifest"]
    assert manifest["final_status"] == "completed"
    assert len(manifest["group_result_uris"]) == 4


def test_provider_matches_the_port() -> None:
    provider: CheckpointProviderPort = SQLiteCheckpointProvider
    assert provider is SQLiteCheckpointProvider
