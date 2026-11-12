"""Observability adapter home (empty in Iteration 0).

Planned: structured logging with a run/trace context, local trace export, token and cost
accounting.  The privacy rule is already part of the configuration contract: prompt and
document content are sent to a tracing backend only when the owner explicitly enables it
(``privacy.allow_prompt_content_in_trace``).
"""

from __future__ import annotations

__all__: list[str] = []
