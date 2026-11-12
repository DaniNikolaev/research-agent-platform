"""Run Manager: identity, lifecycle transitions, error bookkeeping, repository contract."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from tests.fixtures.logging import silent_logger

from research_platform.application.ports.run_repository import (
    RunRepositoryError,
    RunRepositoryPort,
)
from research_platform.application.services.run_manager import (
    RunManager,
    RunManagerError,
    StartRunRequest,
)
from research_platform.domain.runs.models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_RUN_STATUSES,
    InvalidRunTransitionError,
    RunError,
    RunStage,
    RunStatus,
    generate_correlation_id,
    generate_run_id,
)
from research_platform.platform.persistence.in_memory_run_repository import (
    InMemoryRunRepository,
)

_BASE = datetime(2026, 11, 12, 12, 0, 0, tzinfo=UTC)


class Clock:
    """Monotonic fake clock."""

    def __init__(self) -> None:
        self._now = _BASE

    def __call__(self) -> datetime:
        self._now += timedelta(seconds=1)
        return self._now


def build_manager() -> tuple[RunManager, InMemoryRunRepository]:
    repository = InMemoryRunRepository()
    return RunManager(repository=repository, logger=silent_logger(), clock=Clock()), repository


def error(code: str = "boom", *, retryable: bool = False) -> RunError:
    return RunError(
        code=code,
        message=f"{code} happened",
        stage=RunStage.SMOKE,
        retryable=retryable,
        correlation_id=generate_correlation_id(),
    )


# --------------------------------------------------------------------- identity


def test_run_id_format_and_uniqueness() -> None:
    first = generate_run_id()
    second = generate_run_id()
    assert first != second
    assert first.startswith("run_")
    assert len(first) <= 64
    assert "--" not in first


def test_run_id_is_deterministic_when_inputs_are_given() -> None:
    assert generate_run_id(now=_BASE, suffix="abcdef") == "run_20261112_120000_abcdef"
    with pytest.raises(ValueError):
        generate_run_id(suffix="XYZ")


def test_correlation_id_format() -> None:
    value = generate_correlation_id()
    assert value.startswith("corr_")
    assert len(value) == 17


def test_start_run_creates_a_running_record() -> None:
    manager, repository = build_manager()
    record = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))

    assert record.status is RunStatus.RUNNING
    assert record.current_stage is RunStage.REGISTRATION
    assert record.started_at is not None
    assert record.completed_at is None
    assert record.correlation_id is not None
    assert repository.exists(record.run_id)
    assert repository.get_by_id(record.run_id).status is RunStatus.RUNNING


def test_start_run_uses_the_supplied_id_and_refuses_a_duplicate() -> None:
    manager, _ = build_manager()
    run_id = "run_20260101_000000_abcdef"
    first = manager.start_run(StartRunRequest(document_id="doc_alpha_0001", run_id=run_id))
    assert first.run_id == run_id
    with pytest.raises(RunManagerError):
        manager.start_run(StartRunRequest(document_id="doc_alpha_0001", run_id=run_id))


def test_two_runs_of_the_same_document_are_independent() -> None:
    manager, repository = build_manager()
    first = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    second = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))

    assert first.run_id != second.run_id
    assert first.correlation_id != second.correlation_id
    assert repository.count() == 2
    assert first.status is RunStatus.RUNNING
    assert second.status is RunStatus.RUNNING


def test_document_id_is_not_the_run_id() -> None:
    manager, _ = build_manager()
    record = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    assert record.document_id == "doc_alpha_0001"
    assert record.run_id != record.document_id


# --------------------------------------------------------------------- lifecycle


def test_successful_lifecycle() -> None:
    manager, _ = build_manager()
    created = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    staged = manager.update_stage(created.run_id, RunStage.SMOKE)
    assert staged.current_stage is RunStage.SMOKE

    finished = manager.complete_run(created.run_id)
    assert finished.status is RunStatus.COMPLETED
    assert finished.is_terminal
    assert finished.completed_at is not None
    assert finished.finished_at == finished.completed_at


def test_warnings_produce_a_distinct_terminal_status() -> None:
    manager, _ = build_manager()
    run = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    finished = manager.complete_run(run.run_id, warnings=["smoke only"])
    assert finished.status is RunStatus.COMPLETED_WITH_WARNINGS
    assert finished.warnings == ["smoke only"]


def test_waiting_for_human_pauses_and_resumes() -> None:
    manager, _ = build_manager()
    run = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    paused = manager.request_human_review(run.run_id, reason="needs a human")
    assert paused.status is RunStatus.WAITING_FOR_HUMAN
    assert paused.completed_at is None
    resumed = manager.resume_run(run.run_id)
    assert resumed.status is RunStatus.RUNNING


def test_failure_records_the_error_and_terminates() -> None:
    manager, repository = build_manager()
    run = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    failed = manager.fail_run(run.run_id, error("model_timeout"))
    assert failed.status is RunStatus.FAILED
    assert [item.code for item in failed.errors] == ["model_timeout"]
    assert repository.get_by_id(run.run_id).status is RunStatus.FAILED


def test_terminal_run_cannot_be_resurrected() -> None:
    manager, _ = build_manager()
    run = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    manager.complete_run(run.run_id)
    with pytest.raises(InvalidRunTransitionError):
        manager.resume_run(run.run_id)
    with pytest.raises(RunManagerError):
        manager.update_stage(run.run_id, RunStage.SMOKE)
    with pytest.raises((InvalidRunTransitionError, RunManagerError)):
        manager.fail_run(run.run_id, error())


def test_fail_stage_keeps_the_run_running() -> None:
    manager, _ = build_manager()
    run = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    updated = manager.fail_stage(run.run_id, RunStage.SMOKE, error("transient", retryable=True))
    assert updated.status is RunStatus.RUNNING
    assert len(updated.errors) == 1


def test_retry_counter_is_monotonic() -> None:
    manager, _ = build_manager()
    run = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    first = manager.record_retry(run.run_id, RunStage.SMOKE, 1)
    second = manager.record_retry(run.run_id, RunStage.SMOKE, 2)
    assert first.retry_count == 1
    assert second.retry_count == 2


def test_cancel_is_terminal() -> None:
    manager, _ = build_manager()
    run = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    cancelled = manager.cancel_run(run.run_id, reason="operator asked")
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.status in TERMINAL_RUN_STATUSES


def test_unknown_run_is_a_manager_error() -> None:
    manager, _ = build_manager()
    with pytest.raises(RunManagerError):
        manager.get_run("run_20260101_000000_abcdef")
    with pytest.raises(RunManagerError):
        manager.complete_run("run_20260101_000000_abcdef")


def test_every_terminal_status_has_no_outgoing_transition() -> None:
    for status in TERMINAL_RUN_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset()


def test_list_runs_filters_by_status() -> None:
    manager, _ = build_manager()
    running = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    done = manager.start_run(StartRunRequest(document_id="doc_beta_0002"))
    manager.complete_run(done.run_id)

    assert [item.run_id for item in manager.list_runs(RunStatus.RUNNING)] == [running.run_id]
    assert [item.run_id for item in manager.list_runs(RunStatus.COMPLETED)] == [done.run_id]
    assert len(manager.list_runs()) == 2


# ------------------------------------------------------------------- repository


def test_repository_implements_the_port() -> None:
    repository = InMemoryRunRepository()
    assert isinstance(repository, RunRepositoryPort)
    assert repository.is_durable is False
    assert repository.backend_name == "in_memory"


def test_repository_rejects_duplicate_ids() -> None:
    manager, repository = build_manager()
    record = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    with pytest.raises(ValueError):
        repository.create(record)


def test_repository_returns_copies_not_references() -> None:
    manager, repository = build_manager()
    record = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    fetched = repository.get_by_id(record.run_id)
    fetched.warnings.append("mutated by the caller")
    assert repository.get_by_id(record.run_id).warnings == []


def test_repository_missing_run_raises() -> None:
    repository = InMemoryRunRepository()
    with pytest.raises(RunRepositoryError):
        repository.get_by_id("run_20260101_000000_abcdef")
    assert repository.exists("run_20260101_000000_abcdef") is False


def test_repository_update_requires_an_existing_run() -> None:
    manager, repository = build_manager()
    record = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    repository.clear()
    with pytest.raises(RunRepositoryError):
        repository.update(record)


def test_repository_refuses_to_change_a_terminal_record() -> None:
    manager, repository = build_manager()
    record = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
    finished = manager.complete_run(record.run_id)
    payload = finished.model_dump()
    payload["status"] = RunStatus.RUNNING
    payload["completed_at"] = None
    with pytest.raises(ValueError):
        repository.update(type(finished).model_validate(payload))


def test_repository_is_safe_under_threads() -> None:
    repository = InMemoryRunRepository()
    manager = RunManager(repository=repository, logger=silent_logger(), clock=Clock())
    created: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        record = manager.start_run(StartRunRequest(document_id="doc_alpha_0001"))
        with lock:
            created.append(record.run_id)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(created) == 12
    assert len(set(created)) == 12
    assert repository.count() == 12
