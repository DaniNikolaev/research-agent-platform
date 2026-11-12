# Agent 1A graph package — placeholder

The LangGraph workflow of Agent 1A is implemented in **Iteration 3**, not here.

Planned contents (from ADD §8.2): `workflow.py` (graph assembly), `nodes/` (one module
per graph node), `edges.py` (conditional routing owned by code), `prompts/` (versioned
prompt templates).

Already fixed by Iteration 0:

| Contract | Location |
|---|---|
| Typed state | `research_platform.agents.agent_1a.contracts.graph_state` |
| Handoff package | `research_platform.agents.agent_1a.contracts.package` |
| Output card | `research_platform.domain.cards.models` |
| Evidence | `research_platform.domain.evidence.models` |

Rules that the future graph must respect:

1. routing, retries and stop conditions are decided by Python, never by a model;
2. the state carries logical artefact URIs, never document text or binaries;
3. retries are counted per stage class and bounded by
   `WorkflowSettings.retry_limits`;
4. a node re-execution must not create a duplicate package or artefact;
5. `waiting_for_human` is the only pause status; how a human approves the quality gate
   is still an open question (ADR-010).
