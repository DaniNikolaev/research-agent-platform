"""Durable SQLite checkpointing for the local runtime.

Uses the official ``langgraph-checkpoint-sqlite`` adapter; the platform never writes the
checkpoint tables itself.  One provider owns the connections it opened and closes them on
shutdown, so the runtime can be rebuilt against the same file and resume a known ``run_id``.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

__all__ = ["SQLiteCheckpointProvider"]


class SQLiteCheckpointProvider:
    """Create SQLite-backed savers sharing one database file."""

    def __init__(self, *, database_path: Path, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._database_path = Path(database_path)
        self._timeout_seconds = timeout_seconds
        self._connections: list[sqlite3.Connection] = []

    @property
    def database_path(self) -> Path:
        """Absolute path of the checkpoint database."""
        return self._database_path

    @property
    def kind(self) -> str:
        """Backend name recorded in diagnostics."""
        return "sqlite"

    def create_saver(self) -> SqliteSaver:
        """Open (or create) the database and return a saver over a dedicated connection."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self._database_path,
            timeout=self._timeout_seconds,
            check_same_thread=False,
        )
        saver = SqliteSaver(connection)
        self._connections.append(connection)
        return saver

    def thread_id_for_run(self, run_id: str) -> str:
        """One thread per run: distinct runs never share checkpoint state."""
        return run_id

    def health(self) -> dict[str, str]:
        """Diagnostics for ``platform check``."""
        return {
            "kind": self.kind,
            "database": str(self._database_path),
            "exists": "true" if self._database_path.exists() else "false",
        }

    def close(self) -> None:
        """Close every connection this provider opened."""
        while self._connections:
            connection = self._connections.pop()
            with contextlib.suppress(sqlite3.Error):  # pragma: no cover - defensive
                connection.close()
