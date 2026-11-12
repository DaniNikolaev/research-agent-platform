"""Composition root: dependency wiring that is allowed to know every layer.

This package is not an architectural layer — it is the assembly point.  ``app.py`` builds
the adapters and services, and :mod:`runtime_factory` combines them into a LangGraph
runtime.  Only the composition root may import both the application layer and a concrete
agent: doing it anywhere else would invert the intended dependency direction.

Keeping the wiring here (rather than in ``application/runtime``) is a deliberate deviation
from the iteration brief's directory sketch, because the brief's own boundary rule —
"``application`` does not import concrete implementations" — takes precedence.
"""

from __future__ import annotations

from research_platform.composition.runtime_factory import RuntimeFactory

__all__ = ["RuntimeFactory"]
