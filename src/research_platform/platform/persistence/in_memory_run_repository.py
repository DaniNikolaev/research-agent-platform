"""Thread-safe in-memory implementation of :class:`RunRepositoryPort`.

Scope: unit tests, integration tests, the smoke workflow and local demonstrations.  It is
**not** a production store — data disappears with the process, and the adapter says so in
its name.

Behaviour that the port requires and this adapter keeps:

* ``create`` refuses an existing ``run_id`` (a new attempt is a new id);
* a terminal record cannot be replaced by a non-terminal one (no resurrection);
* records are copied in and out, so a caller cannot mutate stored state by accident.
"""

from __future__ import annotations

import threading

from research_platform.application.ports.run_repository import RunRepositoryError
from research_platform.domain.runs.models import RunRecord, RunStatus

__all__ = ["InMemoryRunRepository"]

_TERMINAL = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_WITH_WARNINGS,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)


class InMemoryRunRepository:
    """Process-local run storage."""

    backend_name = "in_memory"
    is_durable = False

    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ writing

    def create(self, record: RunRecord) -> None:
        """Store a new run.

        Raises:
            ValueError: the ``run_id`` already exists.
        """
        with self._lock:
            if record.run_id in self._records:
                raise ValueError(f"run {record.run_id!r} already exists; run ids are never reused")
            self._records[record.run_id] = record.model_copy(deep=True)

    def update(self, record: RunRecord) -> None:
        """Replace an existing record.

        Raises:
            RunRepositoryError: the run does not exist.
            ValueError: the stored run is terminal and the replacement is not the same
                terminal state (a finished run is never resurrected or relabelled).
        """
        with self._lock:
            stored = self._records.get(record.run_id)
            if stored is None:
                raise RunRepositoryError(
                    f"run {record.run_id!r} does not exist; use create() for a new run"
                )
            if stored.status in _TERMINAL and record.status is not stored.status:
                raise ValueError(
                    f"run {record.run_id!r} is terminal ({stored.status.value!r}); a new "
                    "attempt requires a new run_id"
                )
            self._records[record.run_id] = record.model_copy(deep=True)

    # ------------------------------------------------------------------ reading

    def get_by_id(self, run_id: str) -> RunRecord:
        """Load one run.

        Raises:
            RunRepositoryError: the run does not exist.
        """
        with self._lock:
            stored = self._records.get(run_id)
            if stored is None:
                raise RunRepositoryError(f"run {run_id!r} does not exist")
            return stored.model_copy(deep=True)

    def exists(self, run_id: str) -> bool:
        """Whether the run is stored."""
        with self._lock:
            return run_id in self._records

    def list_by_status(self, status: RunStatus | None = None) -> list[RunRecord]:
        """List runs, optionally filtered by status, ordered by creation time."""
        with self._lock:
            records = [
                record.model_copy(deep=True)
                for record in self._records.values()
                if status is None or record.status is status
            ]
        records.sort(key=lambda item: (item.created_at, item.run_id))
        return records

    # -------------------------------------------------------------------- admin

    def count(self) -> int:
        """Number of stored runs (diagnostics)."""
        with self._lock:
            return len(self._records)

    def clear(self) -> None:
        """Drop every record — tests only."""
        with self._lock:
            self._records.clear()

    def close(self) -> None:
        """No resources are held; the data simply stops being reachable."""
        pass
