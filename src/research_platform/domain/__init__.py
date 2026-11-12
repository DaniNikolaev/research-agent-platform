"""Domain layer: platform-wide contracts that carry no infrastructure knowledge.

Rules enforced by ``tests/unit/test_import_boundaries.py``:

* ``domain`` must not import ``application``, ``platform``, ``agents``, ``config``,
  ``cli``, LangChain, LangGraph, SQLAlchemy, Docling, or any provider SDK;
* ``domain`` may depend on the standard library and Pydantic only.

Sub-packages:

``documents``
    Document identity, media types, language and immutable source provenance.
``cards``
    Agent 1A domain card: 9 sections and the 27 legacy-compatible content fields.
``evidence``
    Evidence candidates (LLM proposals) and canonical evidence records.
``runs``
    Run identity, run statuses, run metadata, errors and artifact references.
``validation``
    Technical validation reports and the semantic-verification placeholder.
"""

from __future__ import annotations

__all__: list[str] = []
