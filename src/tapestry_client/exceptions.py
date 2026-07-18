"""Client-side exceptions raised by :class:`tapestry_client.TapestryClient`."""

from __future__ import annotations

from typing import Any


class TapestryClientError(Exception):
    """Base class for all Tapestry client errors.

    Attributes:
        status_code: HTTP status returned by Tapestry, or ``None`` for
            transport / local errors.
        code: Machine-readable error code from the response envelope
            (``error.code``), or ``None`` if unavailable.
        message: Human-readable error message.
        payload: Raw decoded response body when available.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.payload = payload


class TapestryAuthError(TapestryClientError):
    """401 or 403 — service token, delegation token, or permission failed."""


class TapestryNotFoundError(TapestryClientError):
    """404 — resource does not exist or is not visible to the caller."""


class TapestryValidationError(TapestryClientError):
    """422 — request failed validation."""


class TapestryConflictError(TapestryClientError):
    """409 — duplicate or conflicting state."""


class TapestryRateLimitError(TapestryClientError):
    """429 — rate limit exceeded.

    Inspect ``retry_after_seconds`` on the instance for the suggested wait.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        payload: dict[str, Any] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code, code=code, payload=payload)
        self.retry_after_seconds = retry_after_seconds


class TapestryServerError(TapestryClientError):
    """5xx — Tapestry returned an internal error."""
