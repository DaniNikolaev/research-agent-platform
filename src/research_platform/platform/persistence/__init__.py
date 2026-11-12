"""Persistence adapters.

Only the in-memory adapter exists today.  The PostgreSQL adapter (ADR-006) is a later
storage sub-stage and requires the owner's decision on how the local database is operated
(``OI-006``); no SQLAlchemy models or migrations are created before that.
"""

from __future__ import annotations

__all__: list[str] = []
