"""Adversarial tests for the outbound-destination guard + response cap (F-10).

ucsc-link's client follows redirects; without a destination constraint an
upstream (or a MITM) could redirect a hop to an arbitrary host/scheme, or return
an unbounded body. These tests pin the guard: every hop must be https, carry no
userinfo, and target the exact host derived from the configured base URL, and a
body over the cap fails closed. Critically, the guard exceptions must be
NON-retryable (not httpx.TimeoutException/TransportError) so the client's retry
loop never retries them. Research use only; not clinical decision support.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ucsc_link.api.client import UcscClient
from ucsc_link.api.url_guard import (
    HTTP_POLICY_ERROR,
    DisallowedURLError,
    ResponseTooLargeError,
    build_origin_allowlist,
    make_url_guard,
)
from ucsc_link.config import UcscSettings

BASE = "https://api.genome.ucsc.edu"
HOST = "api.genome.ucsc.edu"
PATH = "/list/chromosomes"
ALLOWED = frozenset({(HOST, 443)})


# -- allowlist derivation -------------------------------------------------------


def test_build_origin_allowlist_normalizes_host_and_effective_port() -> None:
    assert build_origin_allowlist(BASE, "https://EXAMPLE.com:443/x") == frozenset(
        {(HOST, 443), ("example.com", 443)}
    )
    assert build_origin_allowlist("https://example.com:8443/x") == frozenset(
        {("example.com", 8443)}
    )


# -- request event-hook (unit) --------------------------------------------------


async def test_guard_allows_allowlisted_https() -> None:
    guard = make_url_guard(ALLOWED)
    await guard(httpx.Request("GET", f"{BASE}{PATH}"))  # must not raise


async def test_guard_rejects_non_https() -> None:
    guard = make_url_guard(ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", f"http://{HOST}{PATH}"))


async def test_guard_rejects_userinfo() -> None:
    guard = make_url_guard(ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", f"https://user:pass@{HOST}{PATH}"))


async def test_guard_rejects_empty_userinfo() -> None:
    # The empty ``:@`` userinfo form must be rejected too (recipe uniformity):
    # httpx parses ``https://:@api.genome.ucsc.edu/`` to ``url.userinfo == b':'``
    # while ``url.username`` and ``url.password`` are both ``""`` -- a
    # ``username or password`` check would MISS it. The guard tests the raw
    # ``url.userinfo`` bytes, so any non-empty userinfo is rejected.
    guard = make_url_guard(ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", f"https://:@{HOST}{PATH}"))
    # A clean allowlisted URL (no userinfo) still passes.
    await guard(httpx.Request("GET", f"https://{HOST}{PATH}"))


async def test_guard_rejects_cross_host() -> None:
    guard = make_url_guard(ALLOWED)
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://evil.example.com/status/"))


async def test_guard_requires_exact_normalized_origin_not_just_host() -> None:
    guard = make_url_guard(frozenset({("example.com", 8443)}))
    await guard(httpx.Request("GET", "https://EXAMPLE.com:8443/status/"))
    with pytest.raises(DisallowedURLError):
        await guard(httpx.Request("GET", "https://example.com/status/"))


def test_guard_exceptions_are_non_retryable_and_not_transport_errors() -> None:
    # The retry loop catches (TimeoutException, TransportError); the guard MUST NOT
    # be caught by it, or a validation/cap failure would be retried 3x.
    for exc_type in (DisallowedURLError, ResponseTooLargeError):
        assert not issubclass(exc_type, (httpx.TimeoutException, httpx.TransportError))
    for exc in (DisallowedURLError("x"), ResponseTooLargeError("y")):
        assert exc.retryable is False
        assert exc.error_code == "upstream_unavailable"
        assert str(exc) == HTTP_POLICY_ERROR


# -- integration through the real client + retry loop ---------------------------


def _client(*, max_retries: int = 3, max_response_bytes: int | None = None) -> UcscClient:
    settings = UcscSettings(min_interval_s=0.0, backoff_base_s=0.0, max_retries=max_retries)
    if max_response_bytes is not None:
        settings.max_response_bytes = max_response_bytes
    return UcscClient(settings)


@respx.mock
async def test_cross_host_redirect_is_refused_and_not_retried() -> None:
    """A 302 to a non-allowlisted host raises and is NOT retried by the retry loop."""
    route = respx.get(f"{BASE}{PATH}").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example.com/x"})
    )
    client = _client(max_retries=3)
    with pytest.raises(DisallowedURLError):
        await client.get_json(PATH, {})
    assert route.call_count == 1  # guard fired once; the retry loop did not re-issue it
    await client.aclose()


@respx.mock
async def test_non_https_redirect_downgrade_is_refused() -> None:
    """A 302 downgrading to http (even same host) raises."""
    respx.get(f"{BASE}{PATH}").mock(
        return_value=httpx.Response(302, headers={"Location": f"http://{HOST}{PATH}"})
    )
    client = _client(max_retries=3)
    with pytest.raises(DisallowedURLError):
        await client.get_json(PATH, {})
    await client.aclose()


@respx.mock
async def test_over_cap_response_is_refused_and_not_retried() -> None:
    """A body over the byte cap fails closed (never truncates) and is not retried."""
    big = b'{"status": "' + b"S" * 5000 + b'"}'
    route = respx.get(f"{BASE}{PATH}").mock(return_value=httpx.Response(200, content=big))
    client = _client(max_retries=3, max_response_bytes=1024)
    with pytest.raises(ResponseTooLargeError):
        await client.get_json(PATH, {})
    assert route.call_count == 1
    await client.aclose()


@respx.mock
async def test_happy_path_through_capped_stream_unchanged() -> None:
    """A normal (no-redirect, under-cap) response still parses correctly."""
    respx.get(f"{BASE}{PATH}").mock(return_value=httpx.Response(200, json={"status": "SUCCESS"}))
    client = _client()
    assert await client.get_json(PATH, {}) == {"status": "SUCCESS"}
    await client.aclose()
