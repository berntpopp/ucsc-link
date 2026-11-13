"""MCP envelope boundary: success/_meta injection and structured errors.

Tools return a plain dict; :func:`run_mcp_tool` injects ``success`` and ``_meta``
on success, and converts any exception into a structured error dict (returned,
never raised) so the LLM sees a typed failure rather than an opaque masked
message.

Every ``_meta`` (success AND error) carries ``tool``, ``request_id``, ``elapsed_ms``,
``source = "ucsc"``, ``capabilities_version``, ``next_commands`` and
``unsafe_for_clinical_use = True`` (the fleet-standard research-use disclaimer).
Data provenance (track data time, retrieval time, attribution) is in the payload.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastmcp.tools.tool import ToolResult
from pydantic import ValidationError as PydanticValidationError

from ucsc_link.exceptions import UcscError
from ucsc_link.mcp import metrics
from ucsc_link.mcp._sanitize import sanitize_message
from ucsc_link.mcp.next_commands import cmd, default_error_next_commands

logger = logging.getLogger(__name__)

_RETRYABLE = {"rate_limited", "upstream_unavailable"}

#: The fleet's closed six-value error taxonomy (Response-Envelope Standard v1).
#: The default/catch-all code is ``internal``; ``UcscError`` subclasses each
#: pin one. No path may emit a code outside this set.
_TAXONOMY = frozenset(
    {
        "invalid_input",
        "not_found",
        "ambiguous_query",
        "upstream_unavailable",
        "rate_limited",
        "internal",
    }
)

#: Map any legacy/off-enum code onto the closed six-value enum. Applied at the
#: single classification chokepoint so no path -- typed exception, McpToolError,
#: or fallback -- can surface a code outside :data:`_TAXONOMY`.
_CANON: dict[str, str] = {
    "data_unavailable": "upstream_unavailable",
    "internal_error": "internal",
}


def _canonical_code(code: str) -> str:
    """Return the canonical fleet error code (map legacy, then validate)."""
    canon = _CANON.get(code, code)
    return canon if canon in _TAXONOMY else "internal"


#: Structured error fields lifted from ``UcscError.extra`` onto the envelope.
_EXTRA_FIELDS = ("field", "hint", "allowed_values", "candidates", "retry_after_s")

#: A tool body returns either a plain success dict (``success``/``_meta`` injected
#: here) or, on error, a :class:`ToolResult` flagged ``is_error=True`` that carries
#: the structured error envelope. The ``is_error`` flag is REQUIRED by
#: Response-Envelope Standard v1 so a client that branches on MCP ``isError`` sees
#: the failure (a plain error dict is ``isError=false`` -- a silent success).
type ToolReturn = dict[str, Any] | ToolResult


@dataclass
class McpErrorContext:
    """Per-call context so envelopes can name the failing tool and recovery."""

    tool_name: str
    fallback: dict[str, Any] | None = field(default=None)
    arguments: dict[str, Any] = field(default_factory=dict)


class McpToolError(Exception):
    """Raised inside a tool body to emit a specific error code/message."""

    def __init__(self, error_code: str, message: str, **extra: object) -> None:
        """Store an error code, client-safe message, and structured extras."""
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.extra: dict[str, object] = dict(extra)


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


def _capabilities_version() -> str | None:
    """Cached discovery-contract hash for the ``_meta`` echo (never raises).

    ``ucsc_link.mcp.capabilities`` is built in a later task; guard the import
    so the envelope still works (omitting the field) before it exists.
    """
    try:
        from ucsc_link.mcp.capabilities import (  # type: ignore[import-untyped, import-not-found, unused-ignore]
            capabilities_version,
        )

        version: object = capabilities_version()
    except Exception:  # pragma: no cover - the _meta echo must never break a tool
        return None
    return str(version) if version else None


#: Map a pydantic error ``type`` to a FIXED reason so no caller-supplied input
#: value (which the pydantic ``msg`` can echo) reaches the caller-visible frame.
_PYDANTIC_REASONS: dict[str, str] = {
    "missing": "the argument is required",
    "int_parsing": "expected an integer",
    "int_type": "expected an integer",
    "float_parsing": "expected a number",
    "string_type": "expected a string",
    "bool_parsing": "expected a boolean",
    "list_type": "expected a list",
    "greater_than": "the value is below the allowed minimum",
    "greater_than_equal": "the value is below the allowed minimum",
    "less_than": "the value is above the allowed maximum",
    "less_than_equal": "the value is above the allowed maximum",
    "enum": "the value is not one of the allowed options",
    "extra_forbidden": "the argument is not recognized",
}


def _safe_message(exc: BaseException) -> str:
    # Strip forbidden control/zero-width/bidi/NUL code points from every
    # caller-visible message (upstream bodies are severed at the API client, so
    # the text here is server/developer-authored; this is the defensive backstop).
    return sanitize_message(str(exc) or exc.__class__.__name__)


def _classify(exc: BaseException) -> tuple[str, str]:
    """Return ``(error_code, client_safe_message)`` for an exception.

    Any :class:`UcscError` subclass maps to its declared ``error_code`` (and
    surfaces its own message). ``McpToolError`` carries an explicit code. Pydantic
    binding errors become ``invalid_input``; anything else is ``internal``.
    """
    if isinstance(exc, McpToolError):
        return _canonical_code(exc.error_code), exc.message
    if isinstance(exc, UcscError):
        code = _canonical_code(getattr(exc, "error_code", "internal"))
        if code in ("rate_limited", "upstream_unavailable"):
            # Don't leak upstream detail; give a stable, actionable message.
            generic = {
                "rate_limited": "Upstream rate limit hit. Retry shortly.",
                "upstream_unavailable": "The UCSC Genome Browser API is temporarily unavailable.",
            }[code]
            return code, exc.message or generic
        return code, _safe_message(exc)
    if isinstance(exc, PydanticValidationError):
        first = exc.errors()[0]
        # The pydantic ``msg`` can echo the caller's input value; use a FIXED reason
        # keyed on the error ``type`` instead. The ``loc`` (argument name) is
        # caller-controlled, so code-point-strip it before echoing.
        loc = sanitize_message(".".join(str(p) for p in first["loc"]) or "input")
        reason = _PYDANTIC_REASONS.get(str(first.get("type", "")), "the value is not valid")
        return "invalid_input", sanitize_message(f"Invalid input -- `{loc}`: {reason}.")
    return "internal", "An internal error occurred. The request was not completed."


def classify_exception(exc: BaseException) -> tuple[str, str]:
    """Public per-item classifier: ``(error_code, client-safe message)``.

    Batch tools catch typed exceptions per item and need the same taxonomy the
    error envelope applies, without building a whole envelope. Delegates to the
    shared classifier so single-item and batch error shaping never diverge.
    """
    return _classify(exc)


def _recovery_action(error_code: str, retryable: bool, explicit: str | None) -> str:
    """Derive the hint from ``retryable`` so the two can never contradict.

    ``retry_backoff`` iff retryable; otherwise the raiser's hint, else a per-code default.
    """
    if retryable:
        return "retry_backoff"
    if explicit and explicit != "retry_backoff":
        return explicit
    if error_code in {"invalid_input", "not_found", "ambiguous_query"}:
        return "reformulate_input"
    return "switch_tool"


def _new_meta(tool_name: str) -> dict[str, Any]:
    """Seed a ``_meta`` block with the trace essentials + the universal invariants.

    ``unsafe_for_clinical_use`` is the fleet-standard per-call research-use disclaimer.
    """
    return {
        "tool": tool_name,
        "request_id": _request_id(),
        "source": "ucsc",
        "unsafe_for_clinical_use": True,
    }


def _error_envelope(exc: BaseException, context: McpErrorContext) -> dict[str, Any]:
    error_code, message = _classify(exc)

    # Pull retryable / recovery_action / extra straight off a UcscError; fall
    # back to per-code defaults for non-typed (or McpToolError) failures.
    retryable = bool(getattr(exc, "retryable", error_code in _RETRYABLE))
    recovery_action = _recovery_action(error_code, retryable, getattr(exc, "recovery_action", None))
    extra: dict[str, Any] = dict(getattr(exc, "extra", {}) or {})

    envelope: dict[str, Any] = {
        "success": False,
        "error_code": error_code,
        # Defensive backstop: no forbidden code points reach the caller, whatever
        # path built the message (rate-limit/upstream generics, ambiguous-query, etc.).
        "message": sanitize_message(message),
        "retryable": retryable,
        "recovery_action": recovery_action,
        "_meta": _new_meta(context.tool_name),
    }

    # Surface curated structured hints (field/hint/allowed_values/candidates).
    for key in _EXTRA_FIELDS:
        if key in extra and extra[key] is not None:
            envelope[key] = extra[key]

    if isinstance(extra.get("next_commands"), list) and extra["next_commands"]:
        envelope["_meta"]["next_commands"] = extra["next_commands"]
    elif context.fallback is not None:
        envelope["_meta"]["next_commands"] = [context.fallback]
    else:
        envelope["_meta"]["next_commands"] = default_error_next_commands(
            context.tool_name, error_code, context.arguments
        )
    return envelope


def build_arg_error_envelope(
    *,
    tool_name: str,
    loc: str,
    error_type: str,
    valid_params: list[str],
    signature: str,
    suggestion: str | None,
    constraints: tuple[list[str], str] | None = None,
) -> dict[str, Any]:
    """Standard invalid-input envelope for an argument-binding failure.

    When ``constraints`` is supplied the failure is an invalid *value* on a known
    argument, so ``allowed_values`` carries the valid range/enum (not the list of
    argument *names*) and the message states the constraint.
    """
    # ``loc`` is a caller-controlled argument NAME (an unknown/misspelled arg on the
    # ``unexpected_keyword_argument`` path). Code-point-strip it before echoing it
    # into either the message or the ``field`` value so it cannot smuggle
    # control/zero-width/bidi/NUL characters into the frame.
    loc = sanitize_message(loc)
    if constraints is not None:
        allowed, human = constraints
        message = f"Invalid value for argument `{loc}` of {tool_name}: {human}."
        meta = _new_meta(tool_name)
        meta["next_commands"] = [cmd("get_server_capabilities")]
        return {
            "success": False,
            "error_code": "invalid_input",
            "message": sanitize_message(message),
            "retryable": False,
            "recovery_action": "reformulate_input",
            "field": loc,
            "allowed_values": allowed,
            "hint": signature,
            "_meta": meta,
        }
    if error_type in ("missing", "missing_argument"):
        head = f"Missing required argument `{loc}` for {tool_name}."
    elif error_type == "unexpected_keyword_argument":
        head = f"Unknown argument `{loc}` for {tool_name}."
    else:
        head = f"Invalid value for argument `{loc}` of {tool_name}."
    dym = f" Did you mean `{suggestion}`?" if suggestion else ""
    message = f"{head}{dym} Valid argument names are listed in allowed_values."
    meta = _new_meta(tool_name)
    meta["next_commands"] = [cmd("get_server_capabilities")]
    return {
        "success": False,
        "error_code": "invalid_input",
        "message": sanitize_message(message),
        "retryable": False,
        "recovery_action": "reformulate_input",
        "field": loc,
        "allowed_values": valid_params,
        "hint": signature,
        "_meta": meta,
    }


def _stamp_capabilities_version(meta: dict[str, Any]) -> None:
    """Add the cached capabilities_version to a ``_meta`` block when available."""
    version = _capabilities_version()
    if version:
        meta["capabilities_version"] = version


async def run_mcp_tool(
    tool_name: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    context: McpErrorContext | None = None,
) -> ToolReturn:
    """Execute a tool body, returning the success dict or an ``is_error`` ToolResult.

    On success the raw dict is returned (FastMCP injects it as ``structuredContent``).
    On failure the structured envelope is wrapped in ``ToolResult(..., is_error=True)``
    so the MCP ``isError`` flag is set -- a plain error dict would be ``isError=false``
    and read by the client as a successful call (Response-Envelope Standard v1).
    """
    ctx = context or McpErrorContext(tool_name=tool_name)
    start = time.perf_counter()
    try:
        result = await call()
        elapsed = int((time.perf_counter() - start) * 1000)
        if isinstance(result, dict):
            existing_meta: dict[str, Any] = result.pop("_meta", None) or {}
            success = bool(result.setdefault("success", True))
            meta = {**existing_meta, **_new_meta(tool_name), "elapsed_ms": elapsed}
            _stamp_capabilities_version(meta)
            result["_meta"] = meta
            metrics.record(tool_name, elapsed, ok=success)
        return result
    except Exception as exc:  # broad catch is the error-boundary contract
        elapsed = int((time.perf_counter() - start) * 1000)
        envelope = _error_envelope(exc, ctx)
        envelope["_meta"]["elapsed_ms"] = elapsed
        _stamp_capabilities_version(envelope["_meta"])
        metrics.record(tool_name, elapsed, ok=False)
        logger.warning(
            "mcp_tool_error tool=%s code=%s exc=%s",
            tool_name,
            envelope["error_code"],
            exc.__class__.__name__,
        )
        return ToolResult(structured_content=envelope, is_error=True)
