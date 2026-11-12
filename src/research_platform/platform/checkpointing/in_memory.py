"""In-memory checkpoint provider built on LangGraph's ``InMemorySaver``.

What it gives the platform:

* a real checkpointer object that ``StateGraph.compile(checkpointer=...)`` accepts;
* one isolated checkpoint namespace per run (``thread_id`` derived from ``run_id``);
* the ability to inspect state after a run and to resume inside the same process.

What it does **not** give:

* durability — the state dies with the process;
* cross-process resume;
* any guarantee about concurrent writers beyond in-process locking.

That is enough for tests and local demonstration, and it is documented as such.
"""

from __future__ import annotations

import threading

from langgraph.checkpoint.memory import InMemorySaver

__all__ = ["InMemoryCheckpointProvider"]

_THREAD_PREFIX = "thread_"


class InMemoryCheckpointProvider:
    """Process-local checkpoint provider."""

    backend_name = "memory"
    is_durable = False

    def __init__(self) -> None:
        self._saver = InMemorySaver()
        self._lock = threading.RLock()
        self._threads: set[str] = set()
        self._closed = False

    def create_saver(self) -> object:
        """Return the shared LangGraph saver for this runtime."""
        if self._closed:
            raise RuntimeError("checkpoint provider is closed")
        return self._saver

    def thread_id_for_run(self, run_id: str) -> str:
        """Deterministic, one-to-one mapping from run id to LangGraph thread id."""
        if not run_id:
            raise ValueError("run_id must not be empty")
        thread_id = f"{_THREAD_PREFIX}{run_id}"
        with self._lock:
            self._threads.add(thread_id)
        return thread_id

    def known_threads(self) -> tuple[str, ...]:
        """Threads handed out by this provider (diagnostics and tests)."""
        with self._lock:
            return tuple(sorted(self._threads))

    def close(self) -> None:
        """Drop the saver; checkpoints become unreachable."""
        with self._lock:
            self._closed = True
            self._threads.clear()
