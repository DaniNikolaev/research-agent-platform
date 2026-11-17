"""Deterministic token-counting adapters.

``HeuristicTokenCounter`` is a *test-grade* estimator: four characters per token.  It is not a
model tokenizer and must never be presented as an exact count; every diagnostic carries
``is_exact=False`` and the counter name.  A future real adapter substitutes an exact tokenizer
behind the same port.
"""

from __future__ import annotations

__all__ = ["HeuristicTokenCounter"]


class HeuristicTokenCounter:
    """Character-based estimate used while no real model is connected."""

    def __init__(self, *, chars_per_token: float = 4.0) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")
        self._chars_per_token = chars_per_token

    @property
    def name(self) -> str:
        """Counter identity recorded in diagnostics."""
        return f"heuristic-chars-per-{self._chars_per_token:g}"

    @property
    def is_exact(self) -> bool:
        """Always false: this is an estimate, not a tokenizer."""
        return False

    def count(self, text: str) -> int:
        """Estimate tokens as ``ceil(len(text) / chars_per_token)``."""
        if not text:
            return 0
        return max(
            1,
            int(len(text) / self._chars_per_token)
            + (1 if len(text) % self._chars_per_token else 0),
        )
