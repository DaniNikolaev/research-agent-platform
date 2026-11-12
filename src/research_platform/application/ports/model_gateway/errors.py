"""Normalised model error hierarchy.

Every provider failure is translated into exactly one of these classes, so that the
workflow - not the adapter - decides whether to retry.  Adapters report *what
happened*; they never retry, never sleep and never choose a fallback model.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar, Final

__all__ = [
    "ModelAuthError",
    "ModelContextOverflowError",
    "ModelError",
    "ModelMalformedResponseError",
    "ModelProviderUnavailableError",
    "ModelRateLimitError",
    "ModelTimeoutError",
    "ModelUnknownError",
    "ModelUnsupportedCapabilityError",
    "normalize_provider_error",
]


class ModelError(Exception):
    """Base class of the normalised model error hierarchy."""

    code: ClassVar[str] = "model_error"
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        request_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider_id = provider_id
        self.model_id = model_id
        self.request_id = request_id
        self._retryable = self.default_retryable if retryable is None else retryable

    @property
    def retryable(self) -> bool:
        """Whether the workflow may retry the same logical operation."""
        return self._retryable

    def to_dict(self) -> dict[str, Any]:
        """Serialisable form for logs and run metadata (never contains secrets)."""
        return {
            "code": self.code,
            "message": self.message,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "request_id": self.request_id,
            "retryable": self.retryable,
        }


class ModelAuthError(ModelError):
    """Credential missing, invalid, or lacking permission."""

    code = "auth_error"
    default_retryable = False


class ModelRateLimitError(ModelError):
    """Provider throttled the request (HTTP 429 or equivalent)."""

    code = "rate_limited"
    default_retryable = True


class ModelTimeoutError(ModelError):
    """The request exceeded the timeout budget."""

    code = "timeout"
    default_retryable = True


class ModelContextOverflowError(ModelError):
    """The input does not fit the model context window."""

    code = "context_overflow"
    default_retryable = False


class ModelUnsupportedCapabilityError(ModelError):
    """The requested capability is not supported by the selected model."""

    code = "unsupported_capability"
    default_retryable = False


class ModelMalformedResponseError(ModelError):
    """The provider answered, but the payload could not be parsed or validated."""

    code = "malformed_response"
    default_retryable = True


class ModelProviderUnavailableError(ModelError):
    """Transport-level failure: connection refused, DNS failure, 5xx gateway error."""

    code = "provider_unavailable"
    default_retryable = True


class ModelUnknownError(ModelError):
    """An unclassified provider failure; treated as non-retryable by default."""

    code = "unknown"
    default_retryable = False


_RETRYABLE_BY_TYPE: Final[dict[type[BaseException], type[ModelError]]] = {
    TimeoutError: ModelTimeoutError,
    ConnectionError: ModelProviderUnavailableError,
    PermissionError: ModelAuthError,
    json.JSONDecodeError: ModelMalformedResponseError,
    UnicodeDecodeError: ModelMalformedResponseError,
    ValueError: ModelMalformedResponseError,
    KeyError: ModelMalformedResponseError,
}


def normalize_provider_error(
    exc: BaseException,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
    request_id: str | None = None,
) -> ModelError:
    """Translate a provider/transport exception into the normalised hierarchy.

    A ``ModelError`` is returned unchanged (already normalised).  Everything else is
    matched from the most specific type to the most general one, so that
    ``json.JSONDecodeError`` is not swallowed by its ``ValueError`` base class.
    """
    if isinstance(exc, ModelError):
        return exc

    for exc_type, error_type in _RETRYABLE_BY_TYPE.items():
        if isinstance(exc, exc_type):
            return error_type(
                str(exc) or exc_type.__name__,
                provider_id=provider_id,
                model_id=model_id,
                request_id=request_id,
            )

    return ModelUnknownError(
        str(exc) or type(exc).__name__,
        provider_id=provider_id,
        model_id=model_id,
        request_id=request_id,
    )
