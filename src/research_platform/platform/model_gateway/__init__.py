"""Provider adapter home (empty in Iteration 0).

A concrete adapter — for DeepSeek, OpenAI, Anthropic or an aggregator — is not
implemented and not selected: the provider is an open decision of the project owner
(``docs/decisions/ADR-005-model-gateway.md``, status ``Deferred``).

When an adapter is added, it must:

* implement ``research_platform.application.ports.model_gateway.protocols``
  (``ChatModelPort`` / ``StructuredOutputModelPort`` / ``EmbeddingModelPort``);
* translate every provider failure through
  ``normalize_provider_error`` instead of leaking an SDK exception;
* never read a credential from a request (credentials come from a secret reference
  resolved by the composition root);
* never retry, never sleep, never choose a fallback model — those decisions belong to
  the workflow.

Test doubles (``FakeChatModel``) live in ``tests/fixtures/fakes.py`` and are not part of
the package, so a fake can never be mistaken for production capability.
"""

from __future__ import annotations

__all__: list[str] = []
