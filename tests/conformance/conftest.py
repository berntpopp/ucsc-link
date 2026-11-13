"""Repository-local binding for the canonical HTTP-policy v1 suite."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import httpx
import pytest

from ucsc_link.api.url_guard import (
    MAX_REDIRECTS,
    DisallowedURLError,
    ResponseTooLargeError,
    build_origin_allowlist,
    make_url_guard,
    read_capped_response,
)


class _HttpPolicyAdapter:
    def __init__(self) -> None:
        self._guard = make_url_guard(build_origin_allowlist("https://allowed.example/api"))

    def allow(self, url: str) -> object:
        return asyncio.run(self._guard(httpx.Request("GET", url)))

    def request(self, url: str, redirects: list[str], max_redirects: int) -> None:
        async def _request() -> None:
            destinations = iter(redirects)

            async def _handler(request: httpx.Request) -> httpx.Response:
                try:
                    return httpx.Response(302, headers={"Location": next(destinations)})
                except StopIteration:
                    return httpx.Response(200, json={"status": "SUCCESS"})

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(_handler),
                follow_redirects=True,
                max_redirects=max_redirects,
                event_hooks={"request": [self._guard]},
            ) as client:
                try:
                    await client.get(url)
                except httpx.TooManyRedirects:
                    raise DisallowedURLError() from None

        asyncio.run(_request())

    def read_decoded(self, chunks: Iterable[bytes], cap: int) -> None:
        async def _read() -> None:
            async def _handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"".join(chunks))

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(_handler), event_hooks={"request": [self._guard]}
            ) as client:
                await read_capped_response(
                    client, "GET", "https://allowed.example/resource", max_bytes=cap
                )

        asyncio.run(_read())

    @staticmethod
    def is_non_retryable(error: Exception) -> bool:
        return (
            isinstance(error, (DisallowedURLError, ResponseTooLargeError)) and not error.retryable
        )

    @staticmethod
    def public_message(error: Exception) -> str:
        return str(error)


@pytest.fixture
def http_policy_adapter() -> _HttpPolicyAdapter:
    """Bind v1 conformance cases to MetaDome's production policy helpers."""
    assert MAX_REDIRECTS <= 5
    return _HttpPolicyAdapter()
