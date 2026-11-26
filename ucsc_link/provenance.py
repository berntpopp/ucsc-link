"""Upstream fetch times behind the current tool call.

The oldest one is the envelope's ``_meta.retrieved_at`` (Response-Envelope Standard v1.2):
the data plane notes each fetch, a cache hit notes the time of the fetch that filled the
entry, and the MCP envelope collects them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

_FETCH_TIMES: ContextVar[list[float] | None] = ContextVar("ucsc_fetch_times", default=None)


def note_fetch(at: float) -> None:
    """Record that data used by the current tool call was fetched at Unix time ``at``."""
    times = _FETCH_TIMES.get()
    if times is not None:
        times.append(at)


async def with_fetch_times[T](call: Callable[[], Awaitable[T]]) -> tuple[T, list[float]]:
    """Run ``call``; return its result with the fetch times noted while it ran."""
    times: list[float] = []
    token = _FETCH_TIMES.set(times)
    try:
        return await call(), times
    finally:
        _FETCH_TIMES.reset(token)
