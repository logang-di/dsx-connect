import redis
from functools import wraps
import asyncio
from typing import Optional
import json
from dsx_connect.config import config


class RedisManager:
    """Centralized Redis management with sync operations"""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._pool: Optional[redis.ConnectionPool] = None

    @property
    def pool(self):
        if self._pool is None:
            self._pool = redis.ConnectionPool.from_url(
                self.redis_url,
                max_connections=10,  # Reasonable for your use case
                socket_connect_timeout=5,
                socket_keepalive=True,
                decode_responses=False
            )
        return self._pool

    def get_client(self) -> redis.Redis:
        """Get a Redis client from the connection pool"""
        return redis.Redis(connection_pool=self.pool)

    def publish_connector_registration(self, connector_data: dict) -> int:
        """Publish connector registration synchronously"""
        client = self.get_client()
        try:
            return client.publish("connector_registered", json.dumps(connector_data))
        finally:
            client.close()

    def publish_scan_result(self, scan_result_data: dict) -> int:
        """Publish scan result synchronously"""
        client = self.get_client()
        try:
            return client.publish("scan_results", json.dumps(scan_result_data))
        finally:
            client.close()

    def set_connector(self, uuid: str, data: dict, ttl: int = 10) -> bool:
        """Set connector data with TTL"""
        client = self.get_client()
        try:
            return client.set(f"dsx:connector:{uuid}", json.dumps(data), ex=ttl)
        finally:
            client.close()

    def delete_connector(self, uuid: str) -> int:
        """Delete connector data"""
        client = self.get_client()
        try:
            return client.delete(f"dsx:connector:{uuid}")
        finally:
            client.close()


# Global instance
redis_manager = RedisManager(config.redis_url)


def run_in_executor(func):
    """Decorator to run sync functions in async context"""

    @wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args, **kwargs)

    return wrapper
