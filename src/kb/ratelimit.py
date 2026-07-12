"""Per-host async rate limiter with jitter and 429 backoff."""
from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from urllib.parse import urlparse

from aiolimiter import AsyncLimiter


class HostRateLimiter:
    """Allow at most one request per ``min_interval`` seconds per host.

    If a caller reports a 429 (Too Many Requests) for a host via
    :meth:`report_429`, the limiter applies an exponential backoff: subsequent
    :meth:`wait` calls for that host are delayed by an increasing penalty
    (capped) that decays back to the baseline interval after a cooldown with
    no further 429s. This adapts to YouTube's per-IP rate limiting without
    needing to know the exact limits in advance."""

    def __init__(self, min_interval: float = 3.0, jitter: float = 1.0,
                 max_backoff: float = 120.0, backoff_step: float = 10.0) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self.max_backoff = max_backoff
        self.backoff_step = backoff_step
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Per-host extra penalty (seconds) added on top of min_interval after a
        # 429. Grows by backoff_step each hit, decays by min_interval each
        # successful wait. Capped at max_backoff.
        self._penalty: dict[str, float] = defaultdict(float)

    async def wait(self, url: str) -> None:
        host = urlparse(url).hostname or "_"
        async with self._locks[host]:
            now = asyncio.get_running_loop().time()
            penalty = self._penalty[host]
            interval = self.min_interval + penalty
            wait_for = self._last[host] + interval - now
            if wait_for > 0:
                await asyncio.sleep(wait_for + random.uniform(0, self.jitter))
            self._last[host] = asyncio.get_running_loop().time()
            # Decay the penalty: a successful wait (no new 429) reduces it.
            if penalty > 0:
                self._penalty[host] = max(0.0, penalty - self.min_interval)

    def report_429(self, url: str) -> float:
        """Signal that a request to this host got 429'd. Increases the
        per-host penalty exponentially so the next wait() backs off longer.
        Returns the new total penalty (interval) for logging."""
        host = urlparse(url).hostname or "_"
        current = self._penalty[host]
        # Exponential: double the current penalty or add one step, whichever is
        # larger. First hit = backoff_step, second ≈ 2×, etc.
        new_penalty = min(self.max_backoff, max(self.backoff_step, current * 2))
        self._penalty[host] = new_penalty
        return self.min_interval + new_penalty


def per_minute_limiter(rpm: int) -> AsyncLimiter:
    return AsyncLimiter(rpm, 60)
