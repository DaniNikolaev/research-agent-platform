"""Checkpoint observer used to emit ``checkpoint_saved`` after a confirmed write.

The wrapper subclasses the official SQLite adapter and only *after* ``super().put`` /
``super().put_writes`` returns successfully does it emit the event.  It never touches the
checkpoint tables, never changes serialisation and never swallows a storage error, so the event
means "the adapter confirmed the write", not "a Python node finished".
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import ChannelVersions, Checkpoint, CheckpointMetadata
from langgraph.checkpoint.sqlite import SqliteSaver

from research_platform.application.ports.logging import LoggerPort

__all__ = ["ObservedSqliteSaver"]


class ObservedSqliteSaver(SqliteSaver):
    """SQLite saver that reports successful checkpoint writes to the existing logger."""

    def __init__(self, conn: Any, *, logger: LoggerPort | None = None, **kwargs: Any) -> None:
        super().__init__(conn, **kwargs)
        self._event_logger = logger

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _thread_id(config: RunnableConfig) -> str | None:
        configurable = config.get("configurable") if isinstance(config, dict) else None
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")
            return str(thread_id) if thread_id else None
        return None

    def _emit(self, config: RunnableConfig, checkpoint: Checkpoint | None) -> None:
        logger = self._event_logger
        if logger is None:
            return
        thread_id = self._thread_id(config)
        checkpoint_id = str(checkpoint.get("id")) if isinstance(checkpoint, dict) else None
        bound = logger.bind(run_id=thread_id, correlation_id=None) if thread_id else logger
        bound.event(
            "checkpoint_saved",
            stage="checkpoint",
            thread_id=thread_id or "unknown",
            checkpoint_id=checkpoint_id or "unknown",
            saver="sqlite",
        )

    # ------------------------------------------------------------------- writes

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Persist a checkpoint, then report the confirmed write."""
        result = super().put(config, checkpoint, metadata, new_versions)
        self._emit(config, checkpoint)
        return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist pending writes; the checkpoint event is emitted by ``put`` only."""
        super().put_writes(config, writes, task_id, task_path)
