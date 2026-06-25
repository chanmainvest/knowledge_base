"""Per-host async rate limiter with jitter and 429 backoff."""
from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from urllib.parse import urlparse

from aiolimiter import AsyncLimiter


class HostRateLimiter:
    """Allow at most one request per `min_interval` seconds per host."""

    def __init__(self, min_interval: float = 3.0, jitter: float = 1.0) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, url: str) -> None:
        host = urlparse(url).hostname or "_"
        async with self._locks[host]:
            now = asyncio.get_running_loop().time()
            wait_for = self._last[host] + self.min_interval - now
            if wait_for > 0:
                await asyncio.sleep(wait_for + random.uniform(0, self.jitter))
            self._last[host] = asyncio.get_running_loop().time()


def per_minute_limiter(rpm: int) -> AsyncLimiter:
    return AsyncLimiter(rpm, 60)
