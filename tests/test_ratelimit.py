"""Process-wide UCSC politeness limiter: spacing, queue bound, daily cap."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ucsc_link.exceptions import RateLimitedError
from ucsc_link.ratelimit import RateLimiter


class FakeClock:
    """Deterministic monotonic + wall clock; ``sleep`` advances time."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.wall = datetime(2026, 11, 13, 23, 59, 0, tzinfo=UTC).timestamp()
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.wall

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        self.wall += seconds


def _limiter(clock: FakeClock, **kw: float) -> RateLimiter:
    args = {"min_interval_s": 1.0, "daily_cap": 5000, "max_wait_s": 10.0} | kw
    return RateLimiter(
        min_interval_s=args["min_interval_s"],
        daily_cap=int(args["daily_cap"]),
        max_wait_s=args["max_wait_s"],
        monotonic=clock.monotonic,
        wall=clock.time,
        sleep=clock.sleep,
    )


async def test_first_call_is_immediate_then_spaced_by_min_interval() -> None:
    clock = FakeClock()
    limiter = _limiter(clock)
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()
    assert clock.sleeps == [1.0, 1.0]


async def test_queue_wait_beyond_max_is_rate_limited_with_retry_after() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, min_interval_s=15.0, max_wait_s=10.0)
    await limiter.acquire()
    with pytest.raises(RateLimitedError) as info:
        await limiter.acquire()
    assert info.value.retryable is True
    assert info.value.extra["retry_after_s"] == 15.0
    # A refused call must not consume a slot or a daily hit.
    assert limiter.stats()["requests_today"] == 1


async def test_daily_cap_refuses_until_utc_midnight() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, daily_cap=2, min_interval_s=0.0)
    await limiter.acquire()
    await limiter.acquire()
    with pytest.raises(RateLimitedError) as info:
        await limiter.acquire()
    assert info.value.extra["retry_after_s"] == 60.0  # 23:59:00 -> midnight
    clock.wall += 61  # next UTC day
    await limiter.acquire()
    assert limiter.stats()["requests_today"] == 1


def test_stats_report_configuration() -> None:
    limiter = _limiter(FakeClock())
    stats = limiter.stats()
    assert stats["min_interval_s"] == 1.0
    assert stats["daily_cap"] == 5000
