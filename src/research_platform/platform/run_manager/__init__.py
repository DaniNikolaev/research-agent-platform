"""Run Manager adapter home (empty in Iteration 0).

Iteration 1 implements run creation, idempotent resumption and status transitions over
``RunRepositoryPort``, plus the checkpoint store.  The rules it must keep are already
fixed by the contract: a new attempt is a new ``run_id``, a completed run is never
overwritten, and repeating the same transition is idempotent.
"""

from __future__ import annotations

__all__: list[str] = []
