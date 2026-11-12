# Agent 1A — extraction workflow

Agent 1A turns **one** scientific article (PDF or TXT, RU or EN) into a source-grounded
card plus an immutable handoff package for Agent 1B.

Responsibility split:

| Part | Owner |
|---|---|
| Parsing, chunking, retrieval, routing, validation, export | Python (deterministic) |
| Field-level semantic extraction inside a bounded node | LLM via the Model Gateway |
| Independent verification of the result | Agent 1B (separate workflow) |

Iteration 0 delivers the contracts, not the workflow:

| Contract | Module |
|---|---|
| Handoff package v2 | `contracts/package.py` |
| Typed LangGraph state | `contracts/graph_state.py` |
| v1 → v2 adapter interface | `contracts/adapter.py` |
| Domain card (27 fields) | `research_platform.domain.cards.models` |
| Evidence candidate/record | `research_platform.domain.evidence.models` |
| Validation report | `research_platform.domain.validation.models` |

Not implemented here, by scope: the 17 graph nodes of ADD §8.2, Docling/PyMuPDF parsing,
quality gate, OCR, chunking, embeddings, retrieval, reconciliation, the deterministic
Evidence Resolver, and export. Each will land in its own iteration with its own tests.

Non-goals worth repeating (ADD §4.3): Agent 1A never judges scientific novelty, never
"fixes" the authors' conclusions, never treats an empty retrieval result as
`not_found`, and never declares a card semantically verified.
