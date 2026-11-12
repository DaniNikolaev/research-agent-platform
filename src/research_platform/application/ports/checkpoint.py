"""Checkpoint provider port.

LangGraph owns the checkpoint format; the platform only decides *which* saver is used and
how a run maps to a LangGraph ``thread_id``.  The saver object is therefore typed as
``object`` here — the application layer must not depend on a concrete LangGraph class, and
the agent/runtime layer is where the saver is handed to ``StateGraph.compile``.

In-memory checkpointing is for tests and local demonstration only.  Durable checkpointing
(PostgreSQL or a dedicated store) belongs to the storage sub-stage and requires the owner's
decision on local database operation (``OI-006``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["CheckpointProviderPort"]


@runtime_checkable
class CheckpointProviderPort(Protocol):
    """Provide a LangGraph-compatible checkpoint saver."""

    def create_saver(self) -> object:
        """Create (or return) a checkpointer instance for one runtime.

        The returned object is passed to ``StateGraph.compile(checkpointer=...)``.
        """
        ...

    def thread_id_for_run(self, run_id: str) -> str:
        """Map a run id to the LangGraph ``thread_id``.

        The mapping is one-to-one: two runs must never share a checkpoint namespace.
        """
        ...

    def close(self) -> None:
        """Release resources held by the provider."""
        ...
