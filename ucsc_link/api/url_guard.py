"""Outbound-destination guard + response byte cap for the UCSC client (F-10).

The client keeps httpx's redirect machinery (``follow_redirects=True``) — a
manual disable-and-loop would have to re-implement httpx's 301/302/303->GET vs
307/308 method-switch and would silently break POST redirects. Instead a
validating **request** event-hook fires on every outgoing request (including
auto-followed redirect hops) and rejects any URL that is not HTTPS, carries
userinfo, or targets a host outside an allowlist **derived from the configured
UCSC base URL** (never hardcoded, so an operator base-URL override still
works). A streamed byte cap fails closed on an over-large body — it never
truncates, because a truncated JSON landscape is unparseable.

Both :class:`DisallowedURLError` and :class:`ResponseTooLargeError` are
**non-retryable** and deliberately do NOT subclass ``httpx.TimeoutException`` /
``httpx.TransportError``, so the client's retry loop (``api/client.py``) never
retries them; they subclass :class:`UpstreamUnavailableError` so the MCP envelope
classifies them (``upstream_unavailable``, ``retryable=False``) with a fixed,
body-free message.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

from ucsc_link.exceptions import UpstreamUnavailableError

#: Maximum redirect hops httpx will follow before erroring (each hop is validated).
MAX_REDIRECTS = 5
HTTP_POLICY_ERROR = "Outbound HTTP policy rejected the request."
Origin = tuple[str, int]


class DisallowedURLError(UpstreamUnavailableError):
    """An outbound request/redirect hop targeted a non-allowlisted URL. NON-RETRYABLE."""

    def __init__(self, message: str = HTTP_POLICY_ERROR) -> None:
        """Build a non-retryable, envelope-classified destination-guard error."""
        super().__init__(HTTP_POLICY_ERROR, retryable=False, recovery_action="switch_tool")


class ResponseTooLargeError(UpstreamUnavailableError):
    """An upstream response body exceeded the hard byte cap. NON-RETRYABLE."""

    def __init__(self, message: str = HTTP_POLICY_ERROR) -> None:
        """Build a non-retryable, envelope-classified over-cap error."""
        super().__init__(HTTP_POLICY_ERROR, retryable=False, recovery_action="switch_tool")


def build_origin_allowlist(*base_urls: str) -> frozenset[Origin]:
    """Derive exact normalized ``(host, effective_port)`` origins from config."""
    origins: set[Origin] = set()
    for url in base_urls:
        parsed = urlsplit(url)
        host = parsed.hostname
        if host:
            try:
                port = parsed.port
            except ValueError:
                continue
            origins.add((host.lower(), 443 if port is None else port))
    return frozenset(origins)


RequestHook = Callable[[httpx.Request], Awaitable[None]]


def make_url_guard(allowed_origins: frozenset[Origin]) -> RequestHook:
    """Build an httpx request hook: HTTPS, no userinfo, exact normalized origin."""

    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError()
        # ``url.userinfo`` is the raw bytes (``b''`` when absent), so this also
        # rejects the empty ``:@`` form (username==password=="" but userinfo==b':')
        # that a ``username or password`` check would miss. Subsumes both.
        if url.userinfo:
            raise DisallowedURLError()
        try:
            port = url.port
        except ValueError:
            raise DisallowedURLError() from None
        origin = ((url.host or "").lower(), 443 if port is None else port)
        if origin not in allowed_origins:
            raise DisallowedURLError()

    return _guard


async def read_capped_response(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int,
    **kwargs: Any,
) -> httpx.Response:
    """Stream a request, enforce a hard byte cap, return the fully-read response.

    The client's request event-hook validates every hop (incl. redirects) before
    any body is read; :class:`ResponseTooLargeError` aborts a body that exceeds
    ``max_bytes`` before it is decoded/parsed (fail-closed, never truncate). Both
    guard exceptions are non-retryable and are not ``httpx.TransportError``
    subclasses, so the caller's retry loop does not retry them. A transient
    timeout/transport fault still surfaces as its native httpx type (retryable).
    """
    total = 0
    chunks: list[bytes] = []
    async with client.stream(method, url, **kwargs) as response:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLargeError()
            chunks.append(chunk)
        status_code = response.status_code
        request = response.request
    # Rebuild a fully-read Response from the already-decoded bytes so the caller's
    # status mapping + .json() work unchanged. Other headers are dropped: copying
    # Content-Encoding onto already-decoded bytes would corrupt .content.
    # Retry-After is the one header the caller's backoff honours (429/503).
    keep = {k: v for k, v in response.headers.items() if k.lower() == "retry-after"}
    return httpx.Response(
        status_code=status_code, content=b"".join(chunks), request=request, headers=keep
    )
