"""Sliding-window rate limiting backed by Redis when available."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from gateway_app.redis_client import RedisClient


class RateLimiter:
    def __init__(self, redis: RedisClient, *, limit_per_minute: int) -> None:
        self._redis = redis
        self._limit = limit_per_minute
        self._local: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    async def allow_async(self, key: str) -> bool:
        if self._limit <= 0:
            return True
        if self._redis.enabled:
            count = await self._redis.incr_with_expiry(
                f"janus:rl:{key}:{int(time.time() // 60)}", 120
            )
            return count <= self._limit
        return self._allow_local(key)

    def _allow_local(self, key: str) -> bool:
        now = time.time()
        window = 60.0
        with self._lock:
            q = self._local[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= self._limit:
                return False
            q.append(now)
            return True
