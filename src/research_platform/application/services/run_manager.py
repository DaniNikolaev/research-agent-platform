"""Run lifecycle orchestration.

The Run Manager is the only place where a run changes state.  Adapters persist records and
know nothing about transitions; the domain state machine
(:data:`~research_platform.domain.runs.models.ALLOWED_TRANSITIONS`) declares what is legal.
Every operation follows the same six steps:

1. load the run (and fail loudly when it does not exist);
2. check that the requested transition is allowed;
3. build a new immutable :class:`RunRecord`;
4. persist it through :class:`RunRepositoryPort`;
5. emit the corresponding structured event;
6. return the new record.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from research_platform.application.ports.logging import (
    RETRY_STARTED,
    RUN_COMPLETED,
    RUN_CREATED,
    RUN_FAILED,
    RUN_STARTED,
    RUN_WAITING_FOR_HUMAN,
    STAGE_COMPLETED,
    STAGE_FAILED,
    STAGE_STARTED,
    LoggerPort,
)
from research_platform.application.ports.run_repository import (
    RunRepositoryError,
    RunRepositoryPort,
)
from research_platform.config import LogLevel
from research_platform.domain.runs.models import (
    ALLOWED_TRANSITIONS,
    InvalidRunTransitionError,
    RetrievalMode,
    RunError,
    RunRecord,
    RunStage,
    RunStatus,
    generate_correlation_id,
    generate_run_id,
)

__all__ = ["RunManager", "RunManagerError", "StartRunRequest"]


class RunManagerError(RuntimeError):
    """A lifecycle operation could not be completed."""


@dataclass(frozen=True, slots=True)
class StartRunRequest:
    """Inputs of a new run.

    ``run_id`` may be supplied when the caller already put it into a graph state; a new id
    is generated otherwise.  A new attempt must always be a new id — the manager refuses to
    reuse an existing one.
    """

    document_id: str
    agent_id: str = "1A"
    agent_version: str = "0.1.0"
    contract_version: str | None = None
    correlation_id: str | None = None
    retrieval_mode: RetrievalMode = RetrievalMode.NOT_APPLICABLE
    metadata: dict[str, str] | None = None
    run_id: str | None = None


class RunManager:
    """Create, advance and finish runs."""

    def __init__(
        self,
        *,
        repository: RunRepositoryPort,
        logger: LoggerPort,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._logger = logger
        self._clock = clock

    # ------------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        if self._clock is not None and callable(self._clock):
            moment = self._clock()
            if not isinstance(moment, datetime):
                raise RunManagerError("clock must return a datetime")
            return moment.astimezone(UTC)
        return datetime.now(UTC)

    def new_run_id(self) -> str:
        """Generate a fresh run id without persisting anything."""
        return generate_run_id()

    def _load(self, run_id: str) -> RunRecord:
        try:
            return self._repository.get_by_id(run_id)
        except RunRepositoryError as exc:
            raise RunManagerError(f"run {run_id!r} does not exist") from exc

    def _replace(self, record: RunRecord) -> RunRecord:
        if record.status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}:
            # A finished run may not be rewritten by a later retry of the same operation.
            stored = self._repository.get_by_id(record.run_id)
            if stored.status in {RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_WARNINGS}:
                raise RunManagerError(
                    f"run {record.run_id!r} is already finished in storage; a new attempt "
                    "requires a new run_id"
                )
        self._repository.update(record)
        return record

    def _transition(
        self,
        record: RunRecord,
        requested: RunStatus,
        *,
        stage: RunStage | None = None,
        retry_count: int | None = None,
        warnings: Sequence[str] = (),
        errors: Sequence[RunError] = (),
        completed_at: datetime | None = None,
    ) -> RunRecord:
        if requested not in ALLOWED_TRANSITIONS[record.status]:
            raise InvalidRunTransitionError(record.run_id, record.status, requested)

        now = completed_at if completed_at is not None else self._now()
        terminal = requested in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_WITH_WARNINGS,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        payload = record.model_dump()
        payload.update(
            {
                "status": requested,
                "current_stage": stage if stage is not None else record.current_stage,
                "retry_count": retry_count if retry_count is not None else record.retry_count,
                "warnings": [*record.warnings, *warnings],
                "errors": [*record.errors, *errors],
                "completed_at": now if terminal else None,
            }
        )
        if requested is RunStatus.RUNNING and record.started_at is None:
            payload["started_at"] = now
        return self._replace(RunRecord.model_validate(payload))

    # ------------------------------------------------------------------ commands

    def start_run(self, request: StartRunRequest) -> RunRecord:
        """Create a run and move it to ``running`` in one deterministic step."""
        run_id = request.run_id or self.new_run_id()
        correlation_id = request.correlation_id or generate_correlation_id()
        if self._repository.exists(run_id):
            raise RunManagerError(
                f"run {run_id!r} already exists; a new attempt requires a new run_id"
            )
        now = self._now()
        record = RunRecord(
            run_id=run_id,
            document_id=request.document_id,
            agent_id=request.agent_id,
            agent_version=request.agent_version,
            contract_version=request.contract_version,
            status=RunStatus.CREATED,
            current_stage=RunStage.REGISTRATION,
            created_at=now,
            correlation_id=correlation_id,
            metadata=dict(request.metadata or {}),
        )
        self._repository.create(record)
        self._logger.bind(run_id=run_id, correlation_id=correlation_id).event(
            RUN_CREATED,
            document_id=request.document_id,
            agent_id=request.agent_id,
            agent_version=request.agent_version,
            contract_version=request.contract_version,
            retrieval_mode=request.retrieval_mode.value,
        )
        running = self._transition(record, RunStatus.RUNNING, stage=RunStage.REGISTRATION)
        self._logger.bind(run_id=run_id, correlation_id=correlation_id).event(
            RUN_STARTED,
            stage=RunStage.REGISTRATION.value,
            status=running.status.value,
        )
        return running

    def get_run(self, run_id: str) -> RunRecord:
        """Load one run."""
        return self._load(run_id)

    def list_runs(self, status: RunStatus | None = None) -> list[RunRecord]:
        """List runs, optionally filtered by status."""
        return self._repository.list_by_status(status)

    def update_stage(self, run_id: str, stage: RunStage) -> RunRecord:
        """Record that the run entered a new stage."""
        record = self._load(run_id)
        if record.is_terminal:
            raise RunManagerError(
                f"run {run_id!r} is terminal ({record.status.value!r}); its stage cannot change"
            )
        payload = record.model_dump()
        payload["current_stage"] = stage
        updated = self._replace(RunRecord.model_validate(payload))
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            STAGE_STARTED, stage=stage.value, status=updated.status.value
        )
        return updated

    def complete_stage(
        self, run_id: str, stage: RunStage, *, detail: str | None = None
    ) -> RunRecord:
        """Record that a stage finished successfully."""
        record = self._load(run_id)
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            STAGE_COMPLETED, stage=stage.value, status=record.status.value, detail=detail
        )
        return record

    def fail_stage(self, run_id: str, stage: RunStage, error: RunError) -> RunRecord:
        """Record a stage failure without finishing the run."""
        record = self._load(run_id)
        payload = record.model_dump()
        payload["errors"] = [*record.errors, error]
        updated = self._replace(RunRecord.model_validate(payload))
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            STAGE_FAILED,
            level=LogLevel.ERROR,
            stage=stage.value,
            error_code=error.code,
            retryable=error.retryable,
        )
        return updated

    def record_error(self, run_id: str, error: RunError) -> RunRecord:
        """Append a normalised error to the run without changing its status."""
        record = self._load(run_id)
        payload = record.model_dump()
        payload["errors"] = [*record.errors, error]
        updated = self._replace(RunRecord.model_validate(payload))
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            STAGE_FAILED,
            level=LogLevel.ERROR,
            stage=error.stage.value,
            error_code=error.code,
            retryable=error.retryable,
        )
        return updated

    def record_retry(self, run_id: str, stage: RunStage, attempt: int) -> RunRecord:
        """Increment the retry counter and log the attempt."""
        record = self._load(run_id)
        payload = record.model_dump()
        payload["retry_count"] = record.retry_count + 1
        updated = self._replace(RunRecord.model_validate(payload))
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            RETRY_STARTED, stage=stage.value, attempt=attempt, retry_count=updated.retry_count
        )
        return updated

    def request_human_review(self, run_id: str, *, reason: str) -> RunRecord:
        """Pause the run until a human decides (status ``waiting_for_human``)."""
        record = self._load(run_id)
        updated = self._transition(record, RunStatus.WAITING_FOR_HUMAN)
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            RUN_WAITING_FOR_HUMAN, reason=reason, status=updated.status.value
        )
        return updated

    def resume_run(self, run_id: str) -> RunRecord:
        """Resume a paused run."""
        record = self._load(run_id)
        return self._transition(record, RunStatus.RUNNING)

    def complete_run(self, run_id: str, *, warnings: Sequence[str] = ()) -> RunRecord:
        """Finish the run successfully (``completed`` or ``completed_with_warnings``)."""
        record = self._load(run_id)
        target = RunStatus.COMPLETED_WITH_WARNINGS if warnings else RunStatus.COMPLETED
        updated = self._transition(record, target, warnings=warnings)
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            RUN_COMPLETED,
            status=updated.status.value,
            stage=(updated.current_stage.value if updated.current_stage else None),
            warnings=len(updated.warnings),
        )
        return updated

    def fail_run(self, run_id: str, error: RunError) -> RunRecord:
        """Finish the run with a normalised error."""
        record = self._load(run_id)
        updated = self._transition(record, RunStatus.FAILED, errors=[error], stage=error.stage)
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            RUN_FAILED,
            level=LogLevel.ERROR,
            error_code=error.code,
            stage=error.stage.value,
            retryable=error.retryable,
        )
        return updated

    def cancel_run(self, run_id: str, *, reason: str) -> RunRecord:
        """Cancel a run that has not finished yet."""
        record = self._load(run_id)
        updated = self._transition(record, RunStatus.CANCELLED)
        self._logger.bind(run_id=run_id, correlation_id=record.correlation_id).event(
            RUN_FAILED, level=LogLevel.ERROR, reason=reason, status=updated.status.value
        )
        return updated
