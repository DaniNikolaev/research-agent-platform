"""Checkpointing adapters.

Only the in-memory provider exists.  It uses LangGraph's own ``InMemorySaver``, so the
checkpoint format stays LangGraph's, and the platform only decides which saver is used and
how a run maps to a ``thread_id``.

Durable checkpointing (PostgreSQL or a dedicated store) is a later storage/runtime sub-stage.
"""

from __future__ import annotations

from research_platform.platform.checkpointing.in_memory import InMemoryCheckpointProvider

__all__ = ["InMemoryCheckpointProvider"]
