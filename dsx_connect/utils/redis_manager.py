from enum import Enum
import redis
from functools import wraps
import asyncio
from typing import Optional, Union, Dict, Any
import json
import time
from dsx_connect.config import config
from dsx_connect.utils.app_logging import dsx_logging


class RedisQueueNames(str, Enum):
    """Enum for all Redis queue names - ensures consistency across API and tasks"""
    DLQ_SCAN_FILE = "dead_letter_queue:read_file_or_scan_file"
    DLQ_VERDICT_ACTION = "dead_letter_queue:verdict_action"

    @classmethod
    def get_for_task(cls, task_name: str) -> 'RedisQueueNames':
        """Get appropriate DLQ based on task name"""
        task_mapping = {
            config.taskqueue.scan_request_task: cls.DLQ_SCAN_FILE,
            config.taskqueue.verdict_action_task: cls.DLQ_VERDICT_ACTION,
            # Add more as needed
        }
        return task_mapping.get(task_name, cls.DLQ_SCAN_FILE)  # Default fallback


class RedisChannelNames(str, Enum):
    """Enum for Redis pub/sub channels"""
    DLQ_NOTIFICATIONS = "notifications:dead_letter_notifications"
    CONNECTOR_REGISTERED = "notifications:connector_registered"
    SCAN_RESULTS = "notifications:scan_results"


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
                max_connections=10,
                socket_connect_timeout=5,
                socket_keepalive=True,
                decode_responses=False
            )
        return self._pool

    def get_client(self) -> redis.Redis:
        """Get a Redis client from the connection pool"""
        return redis.Redis(connection_pool=self.pool)

    # ===== EXISTING METHODS - Updated to use enums =====
    def publish_connector_registration(self, connector_data: dict) -> int:
        """Publish connector registration synchronously"""
        client = self.get_client()
        try:
            return client.publish(RedisChannelNames.CONNECTOR_REGISTERED.value, json.dumps(connector_data))
        finally:
            client.close()

    def publish_scan_result(self, scan_result_data: dict) -> int:
        """Publish scan result synchronously"""
        client = self.get_client()
        try:
            return client.publish(RedisChannelNames.SCAN_RESULTS.value, json.dumps(scan_result_data))
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

    def add_to_dead_letter_queue(self,
                                 queue_name: RedisQueueNames,  # Use enum directly
                                 item_data: Union[str, Dict[str, Any]],
                                 ttl_days: int = 7) -> bool:
        """
        Add item to specified dead letter queue

        Args:
            queue_name: RedisQueueNames enum value
            item_data: Serialized JSON string or dict to serialize
            ttl_days: Days to keep the queue (default 7)

        Returns:
            bool: Success status
        """
        client = self.get_client()

        try:
            # Serialize if needed
            if isinstance(item_data, dict):
                serialized_data = json.dumps(item_data)
            else:
                serialized_data = item_data

            # Use enum value directly as queue name
            queue_key = queue_name.value

            # Add to queue (LPUSH adds to head, RPOP removes from tail = FIFO)
            client.lpush(queue_key, serialized_data)

            # Set expiration for the entire queue
            client.expire(queue_key, 86400 * ttl_days)

            # Publish notification for real-time monitoring
            notification_data = {
                "queue_type": queue_name.name,  # Use enum name for type
                "queue_name": queue_key,        # Use enum value for actual queue name
                "timestamp": time.time(),
                "action": "added"
            }
            client.publish(RedisChannelNames.DLQ_NOTIFICATIONS.value, json.dumps(notification_data))

            dsx_logging.info(f"Added item to {queue_key}")
            return True

        except Exception as e:
            dsx_logging.error(f"Failed to add item to {queue_name.value}: {e}")
            return False
        finally:
            client.close()

    def add_task_failure_to_dlq(self,
                                task_name: str,
                                dead_letter_item,
                                ttl_days: int = 7) -> bool:
        """Add task failure to appropriate DLQ based on task context"""

        # Get appropriate queue for this task
        queue_name = RedisQueueNames.get_for_task(task_name)

        # Handle different input types
        if hasattr(dead_letter_item, 'model_dump_json'):
            item_data = dead_letter_item.model_dump_json()
        elif isinstance(dead_letter_item, dict):
            item_data = json.dumps(dead_letter_item)
        else:
            item_data = str(dead_letter_item)

        dsx_logging.info(f"Adding {task_name} failure to {queue_name.name} DLQ")

        return self.add_to_dead_letter_queue(
            queue_name=queue_name,  # Pass enum directly
            item_data=item_data,
            ttl_days=ttl_days
        )

    def get_dead_letter_queue_stats(self, queue_name: RedisQueueNames) -> Dict[str, Any]:
        """Get statistics for a specific dead letter queue"""
        client = self.get_client()

        try:
            queue_key = queue_name.value
            length = client.llen(queue_key)
            ttl = client.ttl(queue_key)

            return {
                "queue_name": queue_key,
                "queue_type": queue_name.name,
                "length": length,
                "ttl_seconds": ttl,
                "exists": length > 0
            }
        except Exception as e:
            dsx_logging.error(f"Failed to get stats for {queue_name.value}: {e}")
            return {"error": str(e)}
        finally:
            client.close()

    def get_all_dead_letter_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all dead letter queues"""
        return {
            queue.name: self.get_dead_letter_queue_stats(queue)
            for queue in RedisQueueNames
        }

    def requeue_from_dead_letter(self,
                                 queue_name: RedisQueueNames,
                                 max_items: int | None = 100,
                                 celery_task_name: str = None) -> Dict[str, Any]:
        """Requeue items from dead letter queue back to processing"""
        client = self.get_client()

        # If caller asked for “all”, grab the pending_count from stats
        if max_items is None:
            stats = self.get_dead_letter_queue_stats(queue_name)
            max_items = stats.get("length", 0)

        try:
            queue_key = queue_name.value
            requeued_items = []
            successful_requeues = 0
            failed_requeues = 0

            for _ in range(max_items):
                # Get item from tail (FIFO order)
                item = client.rpop(queue_key)
                if not item:
                    break

                item_str = item.decode() if isinstance(item, bytes) else item
                requeued_items.append(item_str)

            # If celery task specified, send items to processing
            if celery_task_name and requeued_items:
                from dsx_connect.celery_app.celery_app import celery_app

                for item in requeued_items:
                    try:
                        item_data = json.loads(item)

                        # Extract scan_request from different formats
                        scan_request_dict = None

                        if isinstance(item_data, dict):
                            if 'scan_request' in item_data:
                                # DeadLetterItem format - extract the scan_request
                                scan_request_dict = item_data['scan_request']
                                failure_reason = item_data.get('failure_reason', 'unknown')
                                location = scan_request_dict.get('location', 'unknown')
                                dsx_logging.info(f"Requeuing DeadLetterItem: '{failure_reason}' for {location}")
                            elif 'location' in item_data and 'connector_url' in item_data:
                                # Direct ScanRequestModel format
                                scan_request_dict = item_data
                                location = scan_request_dict.get('location', 'unknown')
                                dsx_logging.info(f"Requeuing direct scan request for {location}")
                            else:
                                dsx_logging.error(f"Unknown DLQ item format. Available keys: {list(item_data.keys())}")
                                failed_requeues += 1
                                # Put back in queue
                                client.lpush(queue_key, item)
                                continue

                        # Validate that we have a proper scan_request
                        if scan_request_dict:
                            required_fields = ['location', 'metainfo', 'connector_url']
                            missing_fields = [field for field in required_fields if field not in scan_request_dict]

                            if missing_fields:
                                dsx_logging.error(f"Scan request missing required fields: {missing_fields}")
                                dsx_logging.error(f"Available fields: {list(scan_request_dict.keys())}")
                                failed_requeues += 1
                                client.lpush(queue_key, item)
                                continue

                            # CRITICAL: Send task with proper structure
                            task_result = celery_app.send_task(
                                name=celery_task_name,  # Full task name
                                args=[scan_request_dict],  # Pass ONLY scan_request as first argument
                                queue=config.taskqueue.scan_request_queue  # Target queue
                            )

                            successful_requeues += 1
                            dsx_logging.debug(f"Successfully requeued to {celery_task_name} with task_id {task_result.id}")
                        else:
                            dsx_logging.error("Could not extract scan_request from DLQ item")
                            failed_requeues += 1
                            client.lpush(queue_key, item)

                    except json.JSONDecodeError as e:
                        dsx_logging.error(f"Invalid JSON in DLQ item: {e}")
                        failed_requeues += 1
                        client.lpush(queue_key, item)
                    except Exception as e:
                        dsx_logging.error(f"Failed to requeue item: {e}")
                        failed_requeues += 1
                        client.lpush(queue_key, item)

            result = {
                "queue_type": queue_name.name,
                "queue_name": queue_key,
                "requeued_count": successful_requeues,
                "failed_count": failed_requeues,
                "remaining_count": client.llen(queue_key),
                "items": [] if celery_task_name else requeued_items
            }

            dsx_logging.info(f"Requeue summary: {successful_requeues} successful, {failed_requeues} failed, "
                             f"{result['remaining_count']} remaining in {queue_key}")
            return result

        except Exception as e:
            dsx_logging.error(f"Failed to requeue from {queue_name.value}: {e}")
            return {"error": str(e)}
        finally:
            client.close()

    def clear_dead_letter_queue(self, queue_name: RedisQueueNames) -> Dict[str, Any]:
        """Clear all items from specified dead letter queue"""
        client = self.get_client()

        try:
            queue_key = queue_name.value
            count_before = client.llen(queue_key)
            client.delete(queue_key)

            dsx_logging.warning(f"Cleared {count_before} items from {queue_key}")

            return {
                "queue_type": queue_name.name,
                "queue_name": queue_key,
                "cleared_count": count_before,
                "success": True
            }
        except Exception as e:
            dsx_logging.error(f"Failed to clear {queue_name.value}: {e}")
            return {"error": str(e)}
        finally:
            client.close()

    def get_dead_letter_items(self,
                              queue_name: RedisQueueNames,
                              start: int = 0,
                              end: int = 10) -> Dict[str, Any]:
        """Get items from dead letter queue for inspection"""
        client = self.get_client()

        try:
            queue_key = queue_name.value
            items = client.lrange(queue_key, start, end)
            parsed_items = []

            for item in items:
                try:
                    item_str = item.decode() if isinstance(item, bytes) else item
                    parsed_items.append(json.loads(item_str))
                except json.JSONDecodeError:
                    parsed_items.append({"raw_data": item_str, "parse_error": True})

            return {
                "queue_type": queue_name.name,
                "queue_name": queue_key,
                "total_length": client.llen(queue_key),
                "returned_count": len(parsed_items),
                "start_index": start,
                "end_index": end,
                "items": parsed_items
            }
        except Exception as e:
            dsx_logging.error(f"Failed to get items from {queue_name.value}: {e}")
            return {"error": str(e)}
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