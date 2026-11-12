"""Central redaction policy for logs and diagnostics (application-level).

Every log record and every diagnostic string passes through :class:`Redactor` before it
leaves the process.  Redaction is defence in depth: the platform also refuses to put
secrets into contracts, so a redaction hit means a caller made a mistake — it is masked,
never propagated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Final

from pydantic import StringConstraints

__all__ = [
    "REDACTED",
    "SECRET_KEY_MARKERS",
    "RedactedStr",
    "Redactor",
]

REDACTED: Final = "***REDACTED***"

SECRET_KEY_MARKERS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "api-key",
    "secret",
    "password",
    "passwd",
    "token",
    "authorization",
    "credential",
    "bearer",
    "dsn",
    "private_key",
)

RedactedStr = Annotated[str, StringConstraints(max_length=4000)]

_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Provider API keys that follow a well-known prefix shape.
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}\b"),
    re.compile(r"\b(?:gsk|pk|api)[-_][A-Za-z0-9]{16,}\b"),
    # Authorization headers.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{6,}"),
    # key=value / key: value pairs for secret-looking names.
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|token)\b\s*[=:]\s*[^\s,;\"']{3,}"),
    # Credentials embedded in a connection string or URL.
    re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^:/@\s]+:[^@\s]+@"),
    # Long base64-ish blobs commonly used as keys (kept conservative: 40+ chars).
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
)

_CREDENTIAL_IN_URI: Final = re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^:/@\s]+:[^@\s]+@")


class Redactor:
    """Replace secret-looking values with a fixed placeholder."""

    def __init__(
        self,
        *,
        patterns: tuple[re.Pattern[str], ...] | None = None,
        extra_markers: tuple[str, ...] = (),
    ) -> None:
        self._patterns = patterns if patterns is not None else _VALUE_PATTERNS
        self._markers = (*SECRET_KEY_MARKERS, *(marker.lower() for marker in extra_markers))

    def is_secret_key(self, key: str) -> bool:
        """Whether a field name declares secret content."""
        lowered = key.lower()
        return any(marker in lowered for marker in self._markers)

    def redact_text(self, text: str) -> str:
        """Mask secret-looking fragments inside free text."""
        redacted = text
        for pattern in self._patterns:
            if pattern is _CREDENTIAL_IN_URI:
                redacted = pattern.sub(rf"\1{REDACTED}@", redacted)
            else:
                redacted = pattern.sub(REDACTED, redacted)
        return redacted

    def redact_value(self, key: str, value: Any) -> Any:
        """Mask a value when its key declares secret content, else redact the text."""
        if self.is_secret_key(key):
            return REDACTED
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def redact_mapping(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """Redact a shallow mapping (nested mappings are redacted recursively)."""
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, Mapping):
                result[key] = self.redact_mapping(value)
            elif isinstance(value, list | tuple):
                result[key] = [
                    self.redact_mapping(item)
                    if isinstance(item, Mapping)
                    else self.redact_value(key, item)
                    for item in value
                ]
            else:
                result[key] = self.redact_value(key, value)
        return result
