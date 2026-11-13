"""Shared fixtures: a real UcscService over a respx-mocked UCSC JSON API.

Unit tests are network-free. ``mocked_ucsc`` serves the recorded fixtures in
``tests/fixtures/ucsc/`` (captured from https://api.genome.ucsc.edu on 2026-11-13);
``facade`` wires the service into a real FastMCP instance and ``call_tool`` drives it
through an in-memory ``fastmcp.Client``.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest
import respx

from ucsc_link.api.client import UcscClient
from ucsc_link.config import UcscSettings
from ucsc_link.mcp import metrics
from ucsc_link.mcp.facade import create_ucsc_mcp
from ucsc_link.mcp.service_adapters import set_ucsc_service
from ucsc_link.service import UcscService

FX = pathlib.Path(__file__).parent / "fixtures" / "ucsc"
BASE = "https://api.genome.ucsc.edu"


def load(name: str) -> Any:
    """Load a recorded UCSC JSON fixture by file stem."""
    return json.loads((FX / f"{name}.json").read_text())


def fast_settings() -> UcscSettings:
    """Upstream settings with politeness spacing disabled so tests run instantly."""
    return UcscSettings(min_interval_s=0.0, backoff_base_s=0.0)


def _track_route(request: httpx.Request) -> httpx.Response:
    params = dict(request.url.params)
    key = (params.get("genome"), params.get("track"))
    table = {
        ("hg38", "rmsk"): "track_hg38_rmsk_htt",
        ("hg38", "genomicSuperDups"): "track_hg38_genomicSuperDups_gba",
        ("hg38", "phyloP100way"): "track_hg38_phyloP100way_partial",
        ("hg38", "varaico"): "track_hg38_varaico_brca1",
        ("hg38", "spliceVarDb"): "track_hg38_spliceVarDb_brca1",
        ("hg38", "cCREregistry"): "track_hg38_cCREregistry_shh",
    }
    name = table.get(key)
    if name is None:
        return httpx.Response(400, json=load("err_unknown_track"))
    payload = load(name)
    return httpx.Response(206 if payload.get("maxItemsLimit") else 200, json=payload)


@pytest.fixture(autouse=True)
def _reset_metrics() -> Iterator[None]:
    """Keep the process-wide metrics collector clean between tests."""
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def mocked_ucsc() -> Iterator[respx.MockRouter]:
    """A respx router serving the recorded UCSC fixtures."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        router.get("/list/chromosomes", params={"genome": "hg38"}).mock(
            return_value=httpx.Response(200, json=load("chroms_hg38"))
        )
        router.get("/list/chromosomes", params={"genome": "hg19"}).mock(
            return_value=httpx.Response(200, json=load("chroms_hg19"))
        )
        router.get("/getData/track").mock(side_effect=_track_route)
        router.get("/getData/sequence").mock(
            return_value=httpx.Response(200, json=load("seq_hg38_chr4"))
        )
        router.get("/search").mock(return_value=httpx.Response(200, json=load("search_hg38_brca1")))
        yield router


@pytest.fixture
async def ucsc_service(mocked_ucsc: respx.MockRouter) -> AsyncIterator[UcscService]:
    """A real UcscService over the respx-mocked client."""
    client = UcscClient(fast_settings())
    try:
        yield UcscService(client)
    finally:
        await client.aclose()


@pytest.fixture
def facade(ucsc_service: UcscService) -> Iterator[Any]:
    """A FastMCP facade with the fixture service injected; cleans up after."""
    mcp = create_ucsc_mcp(service_factory=lambda: ucsc_service)
    yield mcp
    set_ucsc_service(None)


@pytest.fixture
def call_tool() -> Callable[[Any, str, dict[str, Any]], Awaitable[Any]]:
    """Call a tool through an in-memory ``fastmcp.Client``; return the structured dict."""
    from fastmcp import Client

    async def _call(mcp: Any, name: str, args: dict[str, Any]) -> Any:
        async with Client(mcp) as client:
            res = await client.call_tool(name, args, raise_on_error=False)
        if isinstance(res.structured_content, dict):
            return res.structured_content
        return res.data

    return _call
