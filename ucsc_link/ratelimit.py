"""Process-wide politeness limiter for the UCSC JSON API.

UCSC publishes two different rules: the REST API help recommends at most one hit per
second, while the general Conditions of Use limit program-driven use to one hit every
15 seconds and 5,000 hits per day. The default here is 1 request/second plus the
5,000/day cap (both env-configurable; set ``UCSC_LINK_UCSC__MIN_INTERVAL_S=15`` for the
strict reading). Every upstream request reserves the next slot; a call whose slot is
further away than ``max_wait_s``, or that would exceed the daily cap, is refused with a
typed ``rate_limited`` error carrying ``retry_after_s`` instead of queueing silently.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ucsc_link.exceptions import RateLimitedError


class RateLimiter:
    """Slot-reserving spacer (min interval between requests) with a UTC-day hit cap."""

    def __init__(
        self,
        *,
        min_interval_s: float,
        daily_cap: int,
        max_wait_s: float,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Configure spacing, the daily cap and the longest acceptable queue wait."""
        self.min_interval_s = min_interval_s
        self.daily_cap = daily_cap
        self.max_wait_s = max_wait_s
        self._monotonic = monotonic
        self._wall = wall
        self._sleep = sleep
        self._next_slot = 0.0
        self._day = ""
        self._count = 0
        self._refused = 0
        self._lock = asyncio.Lock()

    def _utc_now(self) -> datetime:
        return datetime.fromtimestamp(self._wall(), tz=UTC)

    async def acquire(self) -> None:
        """Wait for this request's slot, or raise ``RateLimitedError`` (retryable)."""
        async with self._lock:
            now_utc = self._utc_now()
            day = now_utc.date().isoformat()
            if day != self._day:
                self._day, self._count = day, 0
            if self._count >= self.daily_cap:
                self._refused += 1
                midnight = datetime.combine(
                    now_utc.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC
                )
                raise RateLimitedError(
                    f"Daily UCSC request budget ({self.daily_cap}/UTC day) is exhausted; "
                    "UCSC limits program-driven use. Retry after the UTC day rolls over.",
                    retryable=True,
                    retry_after_s=round((midnight - now_utc).total_seconds(), 1),
                )
            now = self._monotonic()
            slot = max(now, self._next_slot)
            wait = slot - now
            if wait > self.max_wait_s:
                self._refused += 1
                raise RateLimitedError(
                    "The shared UCSC request queue is full (UCSC asks clients to pace "
                    "requests). Retry after retry_after_s seconds.",
                    retryable=True,
                    retry_after_s=round(wait, 1),
                )
            self._next_slot = slot + self.min_interval_s
            self._count += 1
        if wait > 0:
            await self._sleep(wait)

    def stats(self) -> dict[str, Any]:
        """Configuration plus today's usage, for diagnostics."""
        return {
            "min_interval_s": self.min_interval_s,
            "daily_cap": self.daily_cap,
            "max_wait_s": self.max_wait_s,
            "requests_today": self._count,
            "refused_since_start": self._refused,
            "utc_day": self._day or None,
        }
