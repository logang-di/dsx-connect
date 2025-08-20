# dsx_connect/messaging/bus.py (Updated with both sync and async)
from __future__ import annotations
from typing import AsyncIterator, Union, Optional, List
from time import time
import json

# Import both sync and async Redis
import redis
from redis.asyncio import Redis as AsyncRedis

from .topics import Topics


def _t(topic: Topics) -> str:
    return topic.value

_SUBS_ROOT = "dsx:subscribers"


class SyncBus:
    """
    Synchronous Bus implementation using synchronous Redis.
    No async overhead - true sync operations.
    """

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._r: Optional[redis.Redis] = None

    @property
    def redis(self) -> redis.Redis:
        """Lazy Redis connection."""
        if self._r is None:
            self._r = redis.Redis.from_url(self._redis_url, decode_responses=False)
        return self._r

    def close(self):
        """Close Redis connection."""
        if self._r:
            self._r.close()
            self._r = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ----- publish & counts -----
    def publish(self, topic: Topics, payload: Union[bytes, str]) -> int:
        """Synchronous publish."""
        if isinstance(payload, str):
            payload = payload.encode()
        return int(self.redis.publish(_t(topic), payload))

    def publish_json(self, topic: Topics, data: dict) -> int:
        """Convenience method to publish JSON."""
        payload = json.dumps(data, separators=(",", ":"))
        return self.publish(topic, payload)

    def pubsub_numsub(self, topic: Topics) -> int:
        """Get number of subscribers (sync)."""
        res = self.redis.pubsub_numsub(_t(topic))
        if isinstance(res, dict):
            return int(next(iter(res.values()), 0))
        if isinstance(res, list) and res:
            return int(res[0][1])
        return 0

    # ----- presence (identity list via heartbeats) -----
    def _subs_key(self, topic: Topics) -> str:
        return f"{_SUBS_ROOT}:{_t(topic)}"

    def subscriber_heartbeat(self, topic: Topics, subscriber_id: str, ttl_sec: int = 120) -> bool:
        """Send heartbeat (sync)."""
        now = int(time())
        key = self._subs_key(topic)
        self.redis.zadd(key, {subscriber_id: now})
        self.redis.expire(key, ttl_sec * 3)
        return True

    def unsubscribe(self, topic: Topics, subscriber_id: str) -> bool:
        """Remove subscriber (sync)."""
        return bool(self.redis.zrem(self._subs_key(topic), subscriber_id))

    def subscribers(self, topic: Topics, max_age_sec: int = 120) -> List[str]:
        """Get active subscribers (sync)."""
        now = int(time())
        cutoff = now - max_age_sec
        key = self._subs_key(topic)
        self.redis.zremrangebyscore(key, 0, cutoff - 1)
        members = self.redis.zrangebyscore(key, cutoff, now)
        return [m.decode() if isinstance(m, (bytes, bytearray)) else m for m in members]

    def subscriber_count(self, topic: Topics, max_age_sec: int = 120) -> int:
        """Get subscriber count (sync)."""
        return len(self.subscribers(topic, max_age_sec))

    # ----- DLQ operations -----
    def dlq_enqueue(self, queue_name: str, item_json: str, ttl_days: Optional[int] = None) -> bool:
        """Enqueue to DLQ (sync)."""
        try:
            self.redis.rpush(queue_name, item_json)
            if ttl_days and ttl_days > 0:
                self.redis.expire(queue_name, ttl_days * 24 * 3600)
            return True
        except Exception:
            return False

    def dlq_peek(self, queue_name: str, start: int = 0, stop: int = 49) -> List[bytes]:
        """Peek at DLQ items (sync)."""
        return self.redis.lrange(queue_name, start, stop)

    def dlq_length(self, queue_name: str) -> int:
        """Get DLQ length (sync)."""
        return self.redis.llen(queue_name)


class AsyncBus:
    """
    Async Bus implementation using async Redis.
    This is your existing Bus class, renamed for clarity.
    """

    def __init__(self, client: AsyncRedis):
        self._r = client

    # ----- publish & counts -----
    async def publish(self, topic: Topics, payload: Union[bytes, str]) -> int:
        if isinstance(payload, str):
            payload = payload.encode()
        return int(await self._r.publish(_t(topic), payload))

    async def publish_json(self, topic: Topics, data: dict) -> int:
        """Convenience method to publish JSON."""
        payload = json.dumps(data, separators=(",", ":"))
        return await self.publish(topic, payload)

    async def pubsub_numsub(self, topic: Topics) -> int:
        res = await self._r.pubsub_numsub(_t(topic))
        if isinstance(res, dict):
            return int(next(iter(res.values()), 0))
        if isinstance(res, list) and res:
            return int(res[0][1])
        return 0

    # ----- presence (identity list via heartbeats) -----
    def _subs_key(self, topic: Topics) -> str:
        return f"{_SUBS_ROOT}:{_t(topic)}"

    async def subscriber_heartbeat(self, topic: Topics, subscriber_id: str, ttl_sec: int = 120) -> bool:
        now = int(time())
        key = self._subs_key(topic)
        await self._r.zadd(key, {subscriber_id: now})
        await self._r.expire(key, ttl_sec * 3)
        return True

    async def unsubscribe(self, topic: Topics, subscriber_id: str) -> bool:
        return bool(await self._r.zrem(self._subs_key(topic), subscriber_id))

    async def subscribers(self, topic: Topics, max_age_sec: int = 120) -> List[str]:
        now = int(time())
        cutoff = now - max_age_sec
        key = self._subs_key(topic)
        await self._r.zremrangebyscore(key, 0, cutoff - 1)
        members = await self._r.zrangebyscore(key, cutoff, now)
        return [m.decode() if isinstance(m, (bytes, bytearray)) else m for m in members]

    async def subscriber_count(self, topic: Topics, max_age_sec: int = 120) -> int:
        return len(await self.subscribers(topic, max_age_sec))

    def start_heartbeat_task(self, topic: Topics, subscriber_id: str, ttl_sec: int = 120):
        import asyncio
        interval = max(1, ttl_sec // 2)
        async def _beat():
            while True:
                await self.subscriber_heartbeat(topic, subscriber_id, ttl_sec)
                await asyncio.sleep(interval)
        return asyncio.create_task(_beat())

    # ----- async listener -----
    async def listen(self, topic: Topics) -> AsyncIterator[bytes]:
        """
        Subscribe to a Redis pub/sub topic and yield each published payload.
        """
        p = self._r.pubsub()
        await p.subscribe(_t(topic))
        try:
            async for msg in p.listen():
                if msg and msg.get("type") == "message":
                    yield msg["data"]
        finally:
            try:
                await p.unsubscribe(_t(topic))
            finally:
                await p.close()


# For backward compatibility, keep Bus as AsyncBus
Bus = AsyncBus


# ============================================================================
# Factory Functions
# ============================================================================

def create_sync_bus(redis_url: str) -> SyncBus:
    """Create synchronous bus for Celery tasks."""
    return SyncBus(redis_url)


def create_async_bus(redis_client: AsyncRedis) -> AsyncBus:
    """Create async bus for FastAPI routes."""
    return AsyncBus(redis_client)


# ============================================================================
# Context Managers
# ============================================================================

class sync_bus_context:
    """Context manager for one-off synchronous bus usage."""

    def __init__(self, redis_url: Optional[str] = None):
        if redis_url is None:
            from dsx_connect.config import get_config
            redis_url = str(get_config().redis_url)
        self.bus = SyncBus(redis_url)

    def __enter__(self) -> SyncBus:
        return self.bus

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.bus.close()


# ============================================================================
# Global Instance Management
# ============================================================================

_sync_bus_instance: Optional[SyncBus] = None

def get_sync_bus() -> SyncBus:
    """Get shared synchronous bus instance."""
    global _sync_bus_instance
    if _sync_bus_instance is None:
        from dsx_connect.config import get_config
        _sync_bus_instance = SyncBus(str(get_config().redis_url))
    return _sync_bus_instance

def close_sync_bus():
    """Close shared synchronous bus instance."""
    global _sync_bus_instance
    if _sync_bus_instance:
        _sync_bus_instance.close()
        _sync_bus_instance = None


# For testing
if __name__ == "__main__":
    import time

    # Test sync bus
    with sync_bus_context("redis://localhost:6379") as sync_bus:
        # Publish
        count = sync_bus.publish(Topics.NOTIFY_DLQ, "test message")
        print(f"Sync publish subscribers: {count}")

        # DLQ operations
        success = sync_bus.dlq_enqueue("test:queue", '{"test": "data"}', ttl_days=1)
        print(f"Sync DLQ enqueue: {success}")

        items = sync_bus.dlq_peek("test:queue", 0, 10)
        print(f"Sync DLQ peek: {len(items)} items")