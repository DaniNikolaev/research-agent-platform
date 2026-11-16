"""Deterministic language detection.

The detector answers "which script dominates the running text?" — nothing more.  It is a
technical hint for OCR language selection and extraction prompts in later iterations, not a
scientific claim, and it says ``unknown`` when the evidence is too thin instead of guessing.

Rules:

* characters that carry no language signal (digits, punctuation, formulas, symbols) are
  excluded;
* Cyrillic and Latin shares are computed over letters only;
* at least :data:`MIN_ANALYSED_LETTERS` letters must be present, otherwise ``unknown``;
* a document is ``ru``/``en`` only when one script holds at least
  :data:`DECISION_SHARE` of the letters; anything closer to a tie is ``unknown``.

The result records the method, the analysed length and a confidence derived from the
observed share — not a fabricated probability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["MIN_ANALYSED_LETTERS", "LanguageDetection", "LanguageDetector"]

MIN_ANALYSED_LETTERS: Final = 200
DECISION_SHARE: Final = 0.80
METHOD_CHARACTER_RATIO: Final = "character_ratio"
METHOD_NOT_DETECTED: Final = "not_detected"


@dataclass(frozen=True, slots=True)
class LanguageDetection:
    """Result of a detection attempt."""

    language: str
    method: str
    confidence: float | None
    analysed_letters: int
    cyrillic_share: float
    latin_share: float
    warnings: tuple[str, ...] = ()


class LanguageDetector:
    """Character-ratio detector (no model, no network, no LLM)."""

    def __init__(
        self,
        *,
        min_letters: int = MIN_ANALYSED_LETTERS,
        decision_share: float = DECISION_SHARE,
    ) -> None:
        if min_letters < 1:
            raise ValueError("min_letters must be positive")
        if not 0.5 < decision_share <= 1.0:
            raise ValueError("decision_share must be in (0.5, 1.0]")
        self._min_letters = min_letters
        self._decision_share = decision_share

    def detect(self, text: str) -> LanguageDetection:
        """Classify the dominant script of ``text``."""
        cyrillic = 0
        latin = 0
        for char in text:
            if not char.isalpha():
                continue
            code = ord(char)
            if 0x0400 <= code <= 0x04FF:
                cyrillic += 1
            elif 0x0041 <= code <= 0x024F:
                latin += 1
        total = cyrillic + latin
        if total == 0:
            return LanguageDetection(
                language="unknown",
                method=METHOD_NOT_DETECTED,
                confidence=None,
                analysed_letters=0,
                cyrillic_share=0.0,
                latin_share=0.0,
                warnings=("no letters found; language cannot be determined",),
            )
        cyrillic_share = cyrillic / total
        latin_share = latin / total
        if total < self._min_letters:
            return LanguageDetection(
                language="unknown",
                method=METHOD_NOT_DETECTED,
                confidence=None,
                analysed_letters=total,
                cyrillic_share=cyrillic_share,
                latin_share=latin_share,
                warnings=(
                    f"only {total} letters available (minimum {self._min_letters}); "
                    "language left unknown",
                ),
            )
        if cyrillic_share >= self._decision_share:
            return LanguageDetection(
                language="ru",
                method=METHOD_CHARACTER_RATIO,
                confidence=round(cyrillic_share, 4),
                analysed_letters=total,
                cyrillic_share=cyrillic_share,
                latin_share=latin_share,
            )
        if latin_share >= self._decision_share:
            return LanguageDetection(
                language="en",
                method=METHOD_CHARACTER_RATIO,
                confidence=round(latin_share, 4),
                analysed_letters=total,
                cyrillic_share=cyrillic_share,
                latin_share=latin_share,
            )
        return LanguageDetection(
            language="unknown",
            method=METHOD_CHARACTER_RATIO,
            confidence=round(max(cyrillic_share, latin_share), 4),
            analysed_letters=total,
            cyrillic_share=cyrillic_share,
            latin_share=latin_share,
            warnings=(
                f"mixed script text (cyrillic {cyrillic_share:.2f}, latin {latin_share:.2f}); "
                "language left unknown",
            ),
        )
