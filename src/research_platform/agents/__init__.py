"""Agents layer: specialised workflows, each with its own versioned contracts.

``agents.agent_1a`` and ``agents.agent_1b`` never import each other's internals.  They
share the ``domain`` contracts and the ``application`` ports and exchange information
only through versioned packages and logical artefact references.
"""

from __future__ import annotations

__all__: list[str] = []
