"""Custom exceptions for ucsc-link.

Each subclass carries a stable ``error_code`` from the fleet's 6-code wire taxonomy.
The data plane (``api/``, ``ratelimit``, ``service``) raises these; the MCP plane
(``mcp/envelope.py::run_mcp_tool``) catches each and converts it into a
**returned** structured error (it reads ``error_code``, ``retryable``,
``recovery_action`` and the ``extra`` dict). Errors are never raised to the
client.
"""

from __future__ import annotations


class UcscError(Exception):
    """Base exception for all ucsc-link data/client/service errors."""

    error_code = "internal"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        recovery_action: str | None = None,
        **extra: object,
    ) -> None:
        """Store a human-readable message plus structured envelope hints.

        Args:
            message: Human-readable error message.
            retryable: Whether the caller may retry the same call.
            recovery_action: Optional machine hint (e.g. ``"switch_tool"``).
            **extra: Arbitrary structured fields surfaced on the error envelope
                (stored on ``self.extra``); e.g. ``field``, ``hint``, ``candidates``.
        """
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.recovery_action = recovery_action
        self.extra: dict[str, object] = dict(extra)


class InvalidInputError(UcscError):
    """A tool/service argument failed validation before any lookup ran."""

    error_code = "invalid_input"


class NotFoundError(UcscError):
    """A lookup returned nothing for an otherwise valid identifier."""

    error_code = "not_found"


class AmbiguousQueryError(UcscError):
    """A query matched several records and cannot be resolved unambiguously."""

    error_code = "ambiguous_query"

    def __init__(
        self,
        message: str,
        *,
        candidates: list[object],
        retryable: bool = False,
        recovery_action: str | None = None,
        **extra: object,
    ) -> None:
        """Store the ambiguous candidates so the envelope can surface them."""
        super().__init__(
            message,
            retryable=retryable,
            recovery_action=recovery_action,
            candidates=candidates,
            **extra,
        )
        self.candidates = candidates


class RateLimitedError(UcscError):
    """The process-wide UCSC politeness budget (or UCSC itself, HTTP 429) refused a call."""

    error_code = "rate_limited"


class UpstreamUnavailableError(UcscError):
    """UCSC is temporarily unavailable (5xx / timeout / transport failure)."""

    error_code = "upstream_unavailable"


class UpstreamSchemaError(UpstreamUnavailableError):
    """UCSC returned HTTP success with a payload outside its documented contract."""

    def __init__(self, message: str, *, field: str) -> None:
        """Create a non-retryable, typed schema failure naming the invalid field."""
        super().__init__(message, retryable=False, field=field)


class InternalError(UcscError):
    """An unexpected internal error (the catch-all default code)."""

    error_code = "internal"
