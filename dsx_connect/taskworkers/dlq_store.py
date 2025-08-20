from __future__ import annotations
import time
import json
import hashlib
from typing import Optional, List, Dict
from uuid import uuid4

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from dsx_connect.messaging.bus import Bus
from dsx_connect.messaging.topics import Topics, DLQKeys
from shared.dsx_logging import dsx_logging
from dsx_connect.config import get_config


class DeadLetterItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    queue: str  # Will be set based on queue type
    reason: str
    scan_request: dict
    error_details: str = ""
    retry_count: int = 0
    original_task_id: str = ""
    created_at: float = Field(default_factory=lambda: time.time())
    last_failed_at: float = Field(default_factory=lambda: time.time())
    # helpful for dedup/idempotent replay
    idempotency_key: str | None = None
    meta: dict = Field(default_factory=dict)

    @staticmethod
    def compute_idempotency_key(scan_request: dict) -> str:
        # ex: stable hash of connector_uuid + location
        key_src = f"{scan_request.get('connector_uuid','')}|{scan_request.get('location','')}"
        return hashlib.sha256(key_src.encode()).hexdigest()


# DLQ Queue Types - matches the router's DeadLetterType enum
class DLQQueueType:
    SCAN_REQUEST = "scan_request"
    VERDICT_ACTION = "verdict_action"
    SCAN_RESULT = "scan_result"  # Future: if needed


# DLQ naming convention - uses centralized DLQKeys
def dlq_queue_name(queue_type: str) -> str:
    """Generate DLQ queue name following the established naming pattern."""
    return DLQKeys.queue_name(queue_type)


# ============================================================================
# Async DLQ Operations using Bus/Redis (for FastAPI routes)
# ============================================================================

async def dlq_enqueue(item: DeadLetterItem, ttl_days: Optional[int] = None,
                      bus: Optional[Bus] = None) -> bool:
    """
    Enqueue item to dead letter queue using messaging Bus.

    Args:
        item: Dead letter item to enqueue
        ttl_days: Optional TTL for the queue
        bus: Optional Bus instance (will create if not provided)

    Returns:
        True if successfully enqueued
    """
    if bus is None:
        # For cases where Bus is not available, create Redis connection
        config = get_config()
        redis = Redis.from_url(str(config.redis_url), decode_responses=False)
        try:
            payload = item.model_dump_json()
            await redis.rpush(item.queue, payload)

            if ttl_days and ttl_days > 0:
                await redis.expire(item.queue, ttl_days * 24 * 3600)

            # Notify via pub/sub about new DLQ item
            await _notify_dlq_event(redis, {
                "type": "enqueue",
                "queue_name": item.queue,
                "reason": item.reason,
                "timestamp": time.time()
            })

            return True
        finally:
            await redis.aclose()
    else:
        # Use existing Bus instance (preferred for API routes)
        payload = item.model_dump_json()
        await bus._r.rpush(item.queue, payload)

        if ttl_days and ttl_days > 0:
            await bus._r.expire(item.queue, ttl_days * 24 * 3600)

        # Notify via pub/sub
        await _notify_dlq_event_via_bus(bus, {
            "type": "enqueue",
            "queue_name": item.queue,
            "reason": item.reason,
            "timestamp": time.time()
        })

        return True


# ============================================================================
# Synchronous DLQ Operations for Celery Tasks
# ============================================================================

def dlq_enqueue_sync(item: DeadLetterItem, ttl_days: Optional[int] = None) -> bool:
    """
    Synchronous version of dlq_enqueue for Celery tasks.
    Uses true synchronous Redis - no async overhead.

    Args:
        item: Dead letter item to enqueue
        ttl_days: Optional TTL for the queue

    Returns:
        True if successfully enqueued
    """
    from dsx_connect.messaging.bus import sync_bus_context

    try:
        with sync_bus_context() as bus:
            # Enqueue to DLQ (pure sync operation)
            success = bus.dlq_enqueue(item.queue, item.model_dump_json(), ttl_days)

            if success:
                # Notify about the enqueue event (pure sync operation)
                bus.publish_json(Topics.NOTIFY_DLQ, {
                    "type": "enqueue",
                    "queue_name": item.queue,
                    "reason": item.reason,
                    "timestamp": time.time()
                })

            return success

    except Exception as e:
        dsx_logging.error(f"Failed to enqueue DLQ item: {e}")
        return False


# ============================================================================
# Convenience Functions for Task Workers (Synchronous)
# ============================================================================

def enqueue_scan_request_dlq(scan_request: dict, error: Exception,
                             task_id: str, retry_count: int, reason: str) -> bool:
    """Convenience function for scan request DLQ (synchronous for Celery)."""
    item = DeadLetterItem(
        queue=DLQKeys.SCAN_REQUEST,
        reason=f"scan_request_{reason}",
        scan_request=scan_request,
        error_details=str(error),
        retry_count=retry_count,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request)
    )

    config = get_config()
    ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
    return dlq_enqueue_sync(item, ttl_days=ttl_days)


def enqueue_verdict_action_dlq(scan_request: dict, verdict: dict, error: Exception,
                               task_id: str, retry_count: int, reason: str) -> bool:
    """Convenience function for verdict action DLQ (synchronous for Celery)."""
    item = DeadLetterItem(
        queue=DLQKeys.VERDICT_ACTION,
        reason=f"verdict_action_{reason}",
        scan_request=scan_request,
        error_details=str(error),
        retry_count=retry_count,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request),
        meta={
            "verdict": verdict,
            "failure_stage": "verdict_action"
        }
    )

    config = get_config()
    ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
    return dlq_enqueue_sync(item, ttl_days=ttl_days)


def enqueue_scan_result_dlq(scan_request: dict, verdict: dict, item_action: dict,
                            error: Exception, task_id: str, retry_count: int, reason: str) -> bool:
    """Convenience function for scan result DLQ (synchronous for Celery)."""
    item = DeadLetterItem(
        queue=DLQKeys.SCAN_RESULT,
        reason=f"scan_result_{reason}",
        scan_request=scan_request,
        error_details=str(error),
        retry_count=retry_count,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request),
        meta={
            "verdict": verdict,
            "item_action": item_action,
            "failure_stage": "scan_result_syslog"
        }
    )

    config = get_config()
    ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
    return dlq_enqueue_sync(item, ttl_days=ttl_days)


# ============================================================================
# Async Convenience Functions for FastAPI Routes
# ============================================================================

async def enqueue_scan_request_dlq_async(scan_request: dict, error: Exception,
                                         task_id: str, retry_count: int, reason: str,
                                         bus: Optional[Bus] = None) -> bool:
    """Async convenience function for FastAPI routes."""
    item = DeadLetterItem(
        queue=DLQKeys.SCAN_REQUEST,
        reason=f"scan_request_{reason}",
        scan_request=scan_request,
        error_details=str(error),
        retry_count=retry_count,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request)
    )

    config = get_config()
    ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
    return await dlq_enqueue(item, ttl_days=ttl_days, bus=bus)


# ============================================================================
# Helper Functions (both sync and async)
# ============================================================================

async def _notify_dlq_event(redis: Redis, event: dict):
    """Notify about DLQ events via pub/sub."""
    try:
        payload = json.dumps(event, separators=(",", ":"))
        await redis.publish(Topics.NOTIFY_DLQ.value, payload)
    except Exception as e:
        dsx_logging.warning(f"Failed to notify DLQ event: {e}")


async def _notify_dlq_event_via_bus(bus: Bus, event: dict):
    """Notify about DLQ events via Bus."""
    try:
        payload = json.dumps(event, separators=(",", ":"))
        await bus.publish(Topics.NOTIFY_DLQ, payload)
    except Exception as e:
        dsx_logging.warning(f"Failed to notify DLQ event via bus: {e}")


# Keep the rest of the async functions for API route compatibility...
async def dlq_peek(queue_name: str, start: int = 0, stop: int = 49,
                   bus: Optional[Bus] = None) -> List[DeadLetterItem]:
    """Async peek function for API routes."""
    # ... existing implementation ...
    pass

# ... other async functions ...


async def dlq_peek(queue_name: str, start: int = 0, stop: int = 49,
                   bus: Optional[Bus] = None) -> List[DeadLetterItem]:
    """
    Peek at items in DLQ without removing them.

    Args:
        queue_name: Name of the DLQ queue
        start: Start index
        stop: Stop index
        bus: Optional Bus instance

    Returns:
        List of DeadLetterItem objects
    """
    if bus is None:
        config = get_config()
        redis = Redis.from_url(str(config.redis_url), decode_responses=False)
        try:
            return await _peek_items(redis, queue_name, start, stop)
        finally:
            await redis.aclose()
    else:
        return await _peek_items(bus._r, queue_name, start, stop)


async def dlq_get_by_id(queue_name: str, item_id: str,
                        bus: Optional[Bus] = None) -> Optional[DeadLetterItem]:
    """
    Get specific DLQ item by ID.

    Args:
        queue_name: Name of the DLQ queue
        item_id: ID of the item to find
        bus: Optional Bus instance

    Returns:
        DeadLetterItem if found, None otherwise
    """
    if bus is None:
        config = get_config()
        redis = Redis.from_url(str(config.redis_url), decode_responses=False)
        try:
            return await _get_item_by_id(redis, queue_name, item_id)
        finally:
            await redis.aclose()
    else:
        return await _get_item_by_id(bus._r, queue_name, item_id)


async def dlq_delete_by_id(queue_name: str, item_id: str,
                           bus: Optional[Bus] = None) -> int:
    """
    Delete specific DLQ item by ID.

    Args:
        queue_name: Name of the DLQ queue
        item_id: ID of the item to delete
        bus: Optional Bus instance

    Returns:
        Number of items removed
    """
    if bus is None:
        config = get_config()
        redis = Redis.from_url(str(config.redis_url), decode_responses=False)
        try:
            return await _delete_item_by_id(redis, queue_name, item_id)
        finally:
            await redis.aclose()
    else:
        return await _delete_item_by_id(bus._r, queue_name, item_id)


async def dlq_requeue(item: DeadLetterItem, task_name: str,
                      bus: Optional[Bus] = None) -> str:
    """
    Re-enqueue the original request to the normal Celery task.

    Args:
        item: DLQ item to requeue
        task_name: Celery task name to send to
        bus: Optional Bus instance

    Returns:
        Task ID of the requeued task
    """
    from dsx_connect.taskworkers.celery_app import celery_app

    try:
        # Send to Celery
        result = celery_app.send_task(task_name, args=[item.scan_request])

        # Notify about requeue
        event = {
            "type": "requeue",
            "queue_name": item.queue,
            "item_id": item.id,
            "task_id": result.id,
            "timestamp": time.time()
        }

        if bus is not None:
            await _notify_dlq_event_via_bus(bus, event)
        else:
            config = get_config()
            redis = Redis.from_url(str(config.redis_url), decode_responses=False)
            try:
                await _notify_dlq_event(redis, event)
            finally:
                await redis.aclose()

        return result.id

    except Exception as e:
        dsx_logging.error(f"Failed to requeue DLQ item {item.id}: {e}")
        raise


# ============================================================================
# Helper Functions
# ============================================================================

async def _peek_items(redis: Redis, queue_name: str, start: int, stop: int) -> List[DeadLetterItem]:
    """Helper to peek at DLQ items."""
    try:
        rows = await redis.lrange(queue_name, start, stop)
        out: List[DeadLetterItem] = []

        for raw in rows:
            try:
                out.append(DeadLetterItem.model_validate_json(raw))
            except Exception as e:
                dsx_logging.warning(f"Skipping malformed DLQ entry: {e}")

        return out
    except Exception as e:
        dsx_logging.error(f"Failed to peek DLQ items from {queue_name}: {e}")
        return []


async def _get_item_by_id(redis: Redis, queue_name: str, item_id: str) -> Optional[DeadLetterItem]:
    """Helper to find DLQ item by ID."""
    try:
        # Naive scan - could be optimized with a separate hash for O(1) lookups
        rows = await redis.lrange(queue_name, 0, -1)

        for raw in rows:
            try:
                item = DeadLetterItem.model_validate_json(raw)
                if item.id == item_id:
                    return item
            except Exception:
                continue

        return None
    except Exception as e:
        dsx_logging.error(f"Failed to get DLQ item {item_id} from {queue_name}: {e}")
        return None


async def _delete_item_by_id(redis: Redis, queue_name: str, item_id: str) -> int:
    """Helper to delete DLQ item by ID."""
    try:
        rows = await redis.lrange(queue_name, 0, -1)
        removed = 0
        pipeline = redis.pipeline()

        for raw in rows:
            try:
                item = DeadLetterItem.model_validate_json(raw)
                if item.id == item_id:
                    # LREM by raw payload
                    pipeline.lrem(queue_name, 1, raw)
                    removed += 1
            except Exception:
                continue

        if removed > 0:
            await pipeline.execute()

        return removed
    except Exception as e:
        dsx_logging.error(f"Failed to delete DLQ item {item_id} from {queue_name}: {e}")
        return 0


async def _notify_dlq_event(redis: Redis, event: dict):
    """Notify about DLQ events via pub/sub."""
    try:
        payload = json.dumps(event, separators=(",", ":"))
        await redis.publish(Topics.NOTIFY_DLQ.value, payload)
    except Exception as e:
        dsx_logging.warning(f"Failed to notify DLQ event: {e}")


async def _notify_dlq_event_via_bus(bus: Bus, event: dict):
    """Notify about DLQ events via Bus."""
    try:
        payload = json.dumps(event, separators=(",", ":"))
        await bus.publish(Topics.NOTIFY_DLQ, payload)
    except Exception as e:
        dsx_logging.warning(f"Failed to notify DLQ event via bus: {e}")


# ============================================================================
# Convenience Functions for Task Workers
# ============================================================================

async def enqueue_scan_request_dlq(scan_request: dict, error: Exception,
                                   task_id: str, retry_count: int, reason: str) -> bool:
    """Convenience function for scan request DLQ."""
    item = DeadLetterItem(
        queue=DLQKeys.SCAN_REQUEST,
        reason=f"scan_request_{reason}",
        scan_request=scan_request,
        error_details=str(error),
        retry_count=retry_count,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request)
    )

    config = get_config()
    ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
    return await dlq_enqueue(item, ttl_days=ttl_days)


async def enqueue_verdict_action_dlq(scan_request: dict, verdict: dict, error: Exception,
                                     task_id: str, retry_count: int, reason: str) -> bool:
    """Convenience function for verdict action DLQ."""
    item = DeadLetterItem(
        queue=DLQKeys.VERDICT_ACTION,
        reason=f"verdict_action_{reason}",
        scan_request=scan_request,
        error_details=str(error),
        retry_count=retry_count,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request),
        meta={
            "verdict": verdict,
            "failure_stage": "verdict_action"
        }
    )

    config = get_config()
    ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
    return await dlq_enqueue(item, ttl_days=ttl_days)


async def enqueue_scan_result_dlq(scan_request: dict, verdict: dict, item_action: dict,
                                  error: Exception, task_id: str, retry_count: int, reason: str) -> bool:
    """Convenience function for scan result DLQ."""
    item = DeadLetterItem(
        queue=DLQKeys.SCAN_RESULT,
        reason=f"scan_result_{reason}",
        scan_request=scan_request,
        error_details=str(error),
        retry_count=retry_count,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request),
        meta={
            "verdict": verdict,
            "item_action": item_action,
            "failure_stage": "scan_result_syslog"
        }
    )

    config = get_config()
    ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
    return await dlq_enqueue(item, ttl_days=ttl_days)


# ============================================================================
# Configuration Support
# ============================================================================

def get_dlq_config() -> Dict[str, any]:
    """Get DLQ configuration for debugging/monitoring."""
    config = get_config()
    return {
        "queue_naming_pattern": f"{DLQKeys.DLQ_BASE}:<type>",
        "available_queues": DLQKeys.all_queues(),
        "queue_types": [DLQQueueType.SCAN_REQUEST, DLQQueueType.VERDICT_ACTION, DLQQueueType.SCAN_RESULT],
        "ttl_days": getattr(config.workers, 'dlq_expire_after_days', 30),
        "redis_url": str(config.redis_url),
        "notification_topic": Topics.NOTIFY_DLQ.value
    }


# For testing/debugging
if __name__ == "__main__":
    import asyncio

    async def test_dlq():
        # Test configuration
        config = get_dlq_config()
        print("DLQ Configuration:")
        for key, value in config.items():
            print(f"  {key}: {value}")

        # Test queue naming
        print("\nQueue Names:")
        for queue_type in [DLQQueueType.SCAN_REQUEST, DLQQueueType.VERDICT_ACTION, DLQQueueType.SCAN_RESULT]:
            print(f"  {queue_type}: {DLQKeys.queue_name(queue_type)}")

        print("\nPredefined Queue Constants:")
        for queue_name in DLQKeys.all_queues():
            print(f"  {queue_name}")

    asyncio.run(test_dlq())