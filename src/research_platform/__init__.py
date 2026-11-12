"""Multi-Agent Research Platform.

Iteration 0 delivers the architecture skeleton only: versioned Pydantic contracts,
JSON Schemas, provider-neutral ports, a typed LangGraph state, and a contract
validator CLI. No runtime workflow, parser, retrieval, provider adapter or storage
backend is implemented in this iteration (see ``docs/iteration_reports``).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
