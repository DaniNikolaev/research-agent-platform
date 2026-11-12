"""Infrastructure layer: adapters that implement ``application.ports``.

Nothing in this package is imported by ``domain``.  In Iteration 0 the sub-packages
are placeholders with a written purpose, because the iteration explicitly forbids
working storage backends, provider adapters and a run manager:

``contracts``
    JSON Schema export and the contract registry (implemented in this iteration).
``model_gateway``
    Home of future provider adapters (DeepSeek/OpenAI/Anthropic/aggregator).  The
    provider-neutral protocols and DTOs already live in
    ``research_platform.application.ports.model_gateway``.
``artifact_store``
    Home of the local content-addressed artifact store (Iteration 1).
``run_manager``
    Home of the run lifecycle implementation over ``RunRepositoryPort`` (Iteration 1).
``observability``
    Home of structured logging, trace context and usage accounting (Iteration 1).
"""

from __future__ import annotations

__all__: list[str] = []
