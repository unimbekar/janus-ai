"""Optional Redis client for shared gateway state."""

from __future__ import annotations

from janus_core.logging import get_logger

logger = get_logger(__name__)


class RedisClient:
    """Thin wrapper so the gateway runs without Redis in tests."""

    def __init__(self, url: str | None) -> None:
        self._url = url
        self._client = None
        if url:
            try:
                import redis.asyncio as redis

                self._client = redis.from_url(url, decode_responses=True)
                logger.info("redis_connected", extra={"url_scheme": url.split(":", 1)[0]})
            except Exception as exc:
                logger.warning("redis_unavailable", extra={"error": str(exc)})
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def incr_with_expiry(self, key: str, ttl_seconds: int) -> int:
        if not self._client:
            return 0
        pipe = self._client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds)
        count, _ = await pipe.execute()
        return int(count)

    async def publish(self, channel: str, message: str) -> None:
        if self._client:
            await self._client.publish(channel, message)

    async def subscribe(self, channel: str):
        if not self._client:
            return None
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub
