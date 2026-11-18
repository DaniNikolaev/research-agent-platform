"""Redaction adapter shim.

The redaction *policy* lives in :mod:`research_platform.application.services.redaction`,
because it is pure logic that both the application and the log adapter must share.  This
module keeps the historical import path working for diagnostics tooling.
"""

from __future__ import annotations

from research_platform.application.services.redaction import (
    REDACTED,
    SAFE_NUMERIC_METRIC_KEYS,
    SECRET_KEY_MARKERS,
    RedactedStr,
    Redactor,
)

__all__ = [
    "REDACTED",
    "SAFE_NUMERIC_METRIC_KEYS",
    "SECRET_KEY_MARKERS",
    "RedactedStr",
    "Redactor",
]
