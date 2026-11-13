"""Async client for the official UCSC Genome Browser JSON API.

Every request: passes the process-wide :class:`RateLimiter`, carries an honest
User-Agent (product/version + project URL, no user data), is restricted to the
configured origin over HTTPS with a hard response-byte cap, and backs off on
429/5xx/timeouts (honouring ``Retry-After``). Successful JSON is cached in-process for
``cache_ttl_s`` (no data snapshot is kept; some UCSC tracks carry use restrictions).

Upstream error bodies are used only to classify the failure; callers see fixed,
server-authored messages (Response-Envelope v1.1 error-message sanitation).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any

import httpx

from ucsc_link import __version__
from ucsc_link.api.url_guard import (
    MAX_REDIRECTS,
    build_origin_allowlist,
    make_url_guard,
    read_capped_response,
)
from ucsc_link.config import UcscSettings
from ucsc_link.exceptions import (
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UpstreamSchemaError,
    UpstreamUnavailableError,
)
from ucsc_link.ratelimit import RateLimiter

USER_AGENT = f"ucsc-link/{__version__} (+https://github.com/berntpopp/ucsc-link)"
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _retry_after(response: httpx.Response | None) -> float | None:
    raw = response.headers.get("retry-after") if response is not None else None
    try:
        return max(0.0, float(raw)) if raw is not None else None
    except ValueError:
        return None


def _classify_400(response: httpx.Response) -> Exception:
    """Map a UCSC 400 to a typed error using the body only as a classifier."""
    try:
        body = str(response.json().get("error", "")).lower()
    except ValueError, AttributeError:
        body = ""
    if "can not find track" in body:
        return NotFoundError(
            "UCSC reports this track does not exist for the requested assembly.",
            field="track",
            recovery_action="reformulate_input",
        )
    if "protected" in body or "restricted" in body:
        return UpstreamUnavailableError(
            "UCSC reports this track as restricted; it cannot be served.",
            retryable=False,
            recovery_action="switch_tool",
        )
    return InvalidInputError(
        "UCSC rejected the request parameters (check chrom, start and end against the "
        "assembly's chromosome size).",
        recovery_action="reformulate_input",
    )


class UcscClient:
    """Rate-limited, cached, origin-guarded GET client for ``api.genome.ucsc.edu``."""

    def __init__(self, settings: UcscSettings, *, limiter: RateLimiter | None = None) -> None:
        """Build the HTTP client, limiter and cache from settings."""
        self.settings = settings
        self.limiter = limiter or RateLimiter(
            min_interval_s=settings.min_interval_s,
            daily_cap=settings.daily_cap,
            max_wait_s=settings.max_wait_s,
        )
        guard = make_url_guard(build_origin_allowlist(settings.base_url))
        self._http = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.request_timeout_s,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            event_hooks={"request": [guard]},
        )
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.upstream_requests = 0
        self.cache_hits = 0

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        hit = self._cache.get(key)
        if hit is None or hit[0] < time.monotonic():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        self.cache_hits += 1
        return hit[1]

    def _cache_put(self, key: str, value: dict[str, Any]) -> None:
        self._cache[key] = (time.monotonic() + self.settings.cache_ttl_s, value)
        while len(self._cache) > self.settings.cache_maxsize:
            self._cache.popitem(last=False)

    async def get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET ``path`` with ``params``; return the JSON object or raise a typed error."""
        key = path + "?" + json.dumps(params, sort_keys=True, default=str)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        # Single-flight: parallel identical calls share one upstream request (politeness).
        task = self._inflight.get(key)
        if task is None:
            task = self._inflight[key] = asyncio.ensure_future(self._fetch(path, params))
            task.add_done_callback(lambda done: self._settle(key, done))
        return await asyncio.shield(task)

    def _settle(self, key: str, task: asyncio.Future[dict[str, Any]]) -> None:
        self._inflight.pop(key, None)
        if not task.cancelled() and task.exception() is None:
            self._cache_put(key, task.result())

    async def _fetch(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        attempts = self.settings.max_retries + 1
        response: httpx.Response | None = None
        for attempt in range(attempts):
            if attempt:
                delay = _retry_after(response) or self.settings.backoff_base_s * 2 ** (attempt - 1)
                await asyncio.sleep(min(delay, 30.0))
            await self.limiter.acquire()
            self.upstream_requests += 1
            try:
                response = await read_capped_response(
                    self._http,
                    "GET",
                    path,
                    params=params,
                    max_bytes=self.settings.max_response_bytes,
                )
            except httpx.TimeoutException, httpx.TransportError:
                response = None
                continue
            if response.status_code not in _RETRY_STATUS:
                break
        if response is None or response.status_code in _RETRY_STATUS:
            if response is not None and response.status_code == 429:
                raise RateLimitedError(
                    "UCSC is rate limiting requests. Retry after retry_after_s seconds.",
                    retryable=True,
                    retry_after_s=_retry_after(response) or 60.0,
                )
            raise UpstreamUnavailableError(
                "The UCSC Genome Browser API is temporarily unavailable.", retryable=True
            )
        if response.status_code == 400:
            raise _classify_400(response)
        if response.status_code not in (200, 206):
            raise UpstreamUnavailableError(
                "The UCSC Genome Browser API returned an unexpected status.", retryable=False
            )
        try:
            data = response.json()
        except ValueError:
            raise UpstreamSchemaError("UCSC returned a non-JSON body.", field="response") from None
        if not isinstance(data, dict):
            raise UpstreamSchemaError("UCSC returned a non-object JSON body.", field="response")
        return data
