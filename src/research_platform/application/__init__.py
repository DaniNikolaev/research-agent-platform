"""Application layer: use-case contracts and infrastructure-independent ports.

The application layer may depend on ``domain`` and on the abstract ports declared in
``application.ports``.  It must not import concrete infrastructure
(``platform``, provider SDKs, Docling, SQLAlchemy).

Iteration 0 defines ports only; no use case is implemented yet.
"""

from __future__ import annotations

__all__: list[str] = []
