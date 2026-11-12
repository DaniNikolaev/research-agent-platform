"""Run repository port.

Owns run identity and lifecycle persistence.  The repository stores whole records; the
*transition rules* belong to the domain state machine and are enforced by the
``RunManager`` service, not by the adapter.  An adapter must therefore accept any record
that the domain considers valid and must not invent its own status logic.

Rules every implementation keeps:

* ``run_id`` is unique; creating an existing id is a conflict, never an overwrite;
* a completed run is never resurrected — a new attempt is a new ``run_id``;
* records are returned as immutable snapshots, so a caller cannot mutate stored state by
  accident.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_platform.domain.runs.models import RunRecord, RunStatus

__all__ = ["RunRepositoryError", "RunRepositoryPort"]


class RunRepositoryError(LookupError):
    """Raised when a requested run does not exist."""


@runtime_checkable
class RunRepositoryPort(Protocol):
    """Create, read and update run records."""

    def create(self, record: RunRecord) -> None:
        """Persist a new run.

        Raises:
            ValueError: a run with the same ``run_id`` already exists.
        """
        ...

    def get_by_id(self, run_id: str) -> RunRecord:
        """Load a run record.

        Raises:
            RunRepositoryError: the run does not exist.
        """
        ...

    def update(self, record: RunRecord) -> None:
        """Replace an existing run record with a newer snapshot.

        Raises:
            RunRepositoryError: the run does not exist (use :meth:`create`).
            ValueError: the replacement belongs to another ``run_id`` or was already
                terminal in storage (immutability of finished runs).
        """
        ...

    def exists(self, run_id: str) -> bool:
        """Whether a run with this id is stored."""
        ...

    def list_by_status(self, status: RunStatus | None = None) -> list[RunRecord]:
        """List stored runs, optionally filtered by status, ordered by creation time."""
        ...

    def close(self) -> None:
        """Release resources held by the adapter (no-op for in-memory storage)."""
        ...
