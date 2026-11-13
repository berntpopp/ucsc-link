"""UCSC JSON API client: honest User-Agent, caching, backoff and typed errors."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from tests.conftest import BASE, fast_settings, load
from ucsc_link import __version__
from ucsc_link.api.client import UcscClient
from ucsc_link.exceptions import (
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UpstreamSchemaError,
    UpstreamUnavailableError,
)

PARAMS = {"genome": "hg38", "track": "rmsk", "chrom": "chr4", "start": 1, "end": 2}


@pytest.fixture
async def client() -> UcscClient:
    c = UcscClient(fast_settings())
    yield c  # type: ignore[misc]
    await c.aclose()


@respx.mock(base_url=BASE)
async def test_sends_honest_user_agent_and_returns_json(
    respx_mock: respx.MockRouter, client: UcscClient
) -> None:
    route = respx_mock.get("/getData/track").mock(
        return_value=httpx.Response(200, json=load("track_hg38_rmsk_htt"))
    )
    data = await client.get_json("/getData/track", PARAMS)
    assert data["track"] == "rmsk"
    ua = route.calls.last.request.headers["user-agent"]
    assert ua.startswith(f"ucsc-link/{__version__}") and "github.com/berntpopp/ucsc-link" in ua


@respx.mock(base_url=BASE)
async def test_partial_206_is_a_success(respx_mock: respx.MockRouter, client: UcscClient) -> None:
    respx_mock.get("/getData/track").mock(
        return_value=httpx.Response(206, json=load("track_hg38_phyloP100way_partial"))
    )
    data = await client.get_json("/getData/track", PARAMS)
    assert data["maxItemsLimit"] is True


@respx.mock(base_url=BASE)
async def test_identical_calls_hit_upstream_once(
    respx_mock: respx.MockRouter, client: UcscClient
) -> None:
    route = respx_mock.get("/getData/track").mock(
        return_value=httpx.Response(200, json=load("track_hg38_rmsk_htt"))
    )
    await client.get_json("/getData/track", PARAMS)
    await client.get_json("/getData/track", dict(PARAMS))
    assert route.call_count == 1


@respx.mock(base_url=BASE)
async def test_unknown_track_400_is_not_found(
    respx_mock: respx.MockRouter, client: UcscClient
) -> None:
    respx_mock.get("/getData/track").mock(
        return_value=httpx.Response(400, json=load("err_unknown_track"))
    )
    with pytest.raises(NotFoundError) as info:
        await client.get_json("/getData/track", PARAMS)
    assert info.value.extra["field"] == "track"
    assert "can not find" not in info.value.message  # upstream body is never echoed


@respx.mock(base_url=BASE)
async def test_other_400_is_invalid_input_with_fixed_message(
    respx_mock: respx.MockRouter, client: UcscClient
) -> None:
    respx_mock.get("/getData/sequence").mock(
        return_value=httpx.Response(400, json=load("err_badcoord"))
    )
    with pytest.raises(InvalidInputError) as info:
        await client.get_json("/getData/sequence", {"genome": "hg38", "chrom": "chr1"})
    assert "248956500" not in info.value.message


@respx.mock(base_url=BASE)
async def test_429_retries_then_rate_limited_with_retry_after(
    respx_mock: respx.MockRouter, client: UcscClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("ucsc_link.api.client.asyncio.sleep", _sleep)
    route = respx_mock.get("/getData/track").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"})
    )
    with pytest.raises(RateLimitedError) as info:
        await client.get_json("/getData/track", PARAMS)
    assert route.call_count == 1 + fast_settings().max_retries
    assert info.value.extra["retry_after_s"] == 7.0
    assert slept == [7.0, 7.0]  # backoff honours Retry-After


@respx.mock(base_url=BASE)
async def test_503_then_success_recovers(respx_mock: respx.MockRouter, client: UcscClient) -> None:
    respx_mock.get("/getData/track").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=load("track_hg38_rmsk_htt"))]
    )
    data = await client.get_json("/getData/track", PARAMS)
    assert data["track"] == "rmsk"


@respx.mock(base_url=BASE)
async def test_persistent_timeout_is_retryable_upstream_unavailable(
    respx_mock: respx.MockRouter, client: UcscClient
) -> None:
    respx_mock.get("/getData/track").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(UpstreamUnavailableError) as info:
        await client.get_json("/getData/track", PARAMS)
    assert info.value.retryable is True


@respx.mock(base_url=BASE)
async def test_non_json_success_is_schema_error(
    respx_mock: respx.MockRouter, client: UcscClient
) -> None:
    respx_mock.get("/getData/track").mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(UpstreamSchemaError):
        await client.get_json("/getData/track", PARAMS)


async def test_rate_limit_refusal_propagates_before_any_request() -> None:
    settings = fast_settings()
    settings.daily_cap = 1
    c = UcscClient(settings)
    with respx.mock(base_url=BASE) as router:
        route = router.get("/getData/track").mock(
            return_value=httpx.Response(200, json=load("track_hg38_rmsk_htt"))
        )
        await c.get_json("/getData/track", PARAMS)
        with pytest.raises(RateLimitedError):
            await c.get_json("/getData/track", {**PARAMS, "end": 3})
        assert route.call_count == 1
    await c.aclose()


@respx.mock(base_url=BASE)
async def test_concurrent_identical_calls_share_one_upstream_request(
    respx_mock: respx.MockRouter, client: UcscClient
) -> None:
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)  # let the other callers start while this one is in flight
        return httpx.Response(200, json=load("track_hg38_rmsk_htt"))

    route = respx_mock.get("/getData/track").mock(side_effect=slow)
    results = await asyncio.gather(*(client.get_json("/getData/track", PARAMS) for _ in range(3)))
    assert route.call_count == 1
    assert all(r["track"] == "rmsk" for r in results)
