"""Context-size accounting port.

The extraction workflow must refuse an oversized context instead of silently truncating the
document.  Counting tokens is model-specific, so it lives behind a port: the fake adapter uses
a deterministic heuristic that is explicitly *not* a model tokenizer, and a future real adapter
can plug in an exact tokenizer without touching the workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["ContextBudget", "ContextBudgetError", "TokenCounterPort"]


class ContextBudgetError(ValueError):
    """The prepared context does not fit the configured model window."""


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Measured context against the window of one model profile."""

    counter_name: str
    is_exact: bool
    context_tokens: int
    output_reserve_tokens: int
    safety_reserve_tokens: int
    input_budget_tokens: int

    @property
    def fits(self) -> bool:
        """Whether the context fits the budget."""
        return self.context_tokens <= self.input_budget_tokens

    @property
    def overflow_tokens(self) -> int:
        """How many tokens exceed the budget (zero when it fits)."""
        return max(0, self.context_tokens - self.input_budget_tokens)


@runtime_checkable
class TokenCounterPort(Protocol):
    """Estimate the size of a text for a model window."""

    @property
    def name(self) -> str:
        """Counter identity recorded in diagnostics."""
        ...

    @property
    def is_exact(self) -> bool:
        """Whether the count is an exact tokenizer result (false for heuristics)."""
        ...

    def count(self, text: str) -> int:
        """Number of tokens (or a documented estimate) for ``text``."""
        ...
