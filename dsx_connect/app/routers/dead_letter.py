import json
import time
import asyncio
from typing import Optional, List, Dict, Any

import redis
from fastapi import APIRouter, Query, Request, HTTPException, Body
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from dsx_connect.config import ConfigManager
from dsx_connect.common.endpoint_names import DSXConnectAPIEndpoints
from dsx_connect.models.dead_letter import DeadLetterItem
from dsx_connect.celery_app.celery_app import celery_app
from dsx_connect.utils.app_logging import dsx_logging
from dsx_connect.utils.redis_manager import redis_manager, RedisQueueNames

# Get config instance
config = ConfigManager.get_config()

router = APIRouter(prefix=DSXConnectAPIEndpoints.ADMIN_DEAD_LETTER_QUEUE_PREFIX, tags=["admin"])


# ===== PYDANTIC MODELS =====

class QueueStatsResponse(BaseModel):
    """Response model for queue statistics"""
    queue_name: str
    queue_type: str
    length: int
    ttl_seconds: int
    exists: bool
    sample_items: Optional[List[Dict[str, Any]]] = None


class DeadLetterSummaryResponse(BaseModel):
    """Response model for dead letter queue summary"""
    total_items: int
    active_queues: int
    queues: Dict[str, QueueStatsResponse]


class RequeueRequest(BaseModel):
    """Request model for requeuing dead letter items"""
    queue_name: RedisQueueNames = Field(description="Dead letter queue to operate on")
    max_items: Optional[int] = Field(default=100, ge=1, le=1000, description="Maximum items to process")
    target_task: Optional[str] = Field(None, description="Celery task name to send items to")


class RequeueResponse(BaseModel):
    """Response model for requeue operations"""
    success: bool
    queue_type: str
    queue_name: str
    requeued_count: int
    remaining_count: int
    failed_count: Optional[int] = 0


class ClearQueueResponse(BaseModel):
    """Response model for clear queue operations"""
    success: bool
    queue_type: str
    queue_name: str
    cleared_count: int


class DeadLetterItemsResponse(BaseModel):
    """Response model for getting dead letter items"""
    queue_type: str
    queue_name: str
    total_length: int
    returned_count: int
    start_index: int
    end_index: int
    items: List[Dict[str, Any]]


class RequeueAllDetail(BaseModel):
    queue_type: str
    queue_name: str
    requeued_count: int
    remaining_count: int


class RequeueAllResponse(BaseModel):
    success: bool
    results: List[RequeueAllDetail]


# ===== API ENDPOINTS =====

@router.get("/stats", response_model=DeadLetterSummaryResponse)
async def get_dead_letter_stats():
    """Get comprehensive stats for all dead letter queues"""
    try:
        # Get stats for all queues
        all_stats = redis_manager.get_all_dead_letter_stats()

        # Get detailed items from each queue
        detailed_stats = {}
        total_items = 0

        for queue_type, stats in all_stats.items():
            if "error" in stats:
                # Handle error case
                detailed_stats[queue_type] = QueueStatsResponse(
                    queue_name=f"error_{queue_type}",
                    queue_type=queue_type,
                    length=0,
                    ttl_seconds=-1,
                    exists=False
                )
                continue

            queue_stats = QueueStatsResponse(**stats)

            if stats.get("length", 0) > 0:
                # Get sample items for analysis
                try:
                    queue_enum = RedisQueueNames[queue_type]
                    items_data = redis_manager.get_dead_letter_items(queue_enum, 0, 5)
                    queue_stats.sample_items = items_data.get("items", [])
                except (KeyError, Exception) as e:
                    dsx_logging.warning(f"Could not get sample items for {queue_type}: {e}")
                    queue_stats.sample_items = []

                total_items += stats.get("length", 0)

            detailed_stats[queue_type] = queue_stats

        return DeadLetterSummaryResponse(
            total_items=total_items,
            active_queues=len([q for q in all_stats.values() if q.get("length", 0) > 0]),
            queues=detailed_stats
        )

    except Exception as e:
        dsx_logging.error(f"Failed to get dead letter stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get dead letter stats: {str(e)}")


@router.get("/stats/{queue_type}", response_model=QueueStatsResponse)
async def get_queue_specific_stats(queue_type: str):
    """Get detailed stats for a specific queue type"""
    try:
        # Validate and convert to enum
        try:
            queue_enum = RedisQueueNames[queue_type.upper()]
        except KeyError:
            valid_types = [q.name for q in RedisQueueNames]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid queue type: {queue_type}. Valid types: {valid_types}"
            )

        stats = redis_manager.get_dead_letter_queue_stats(queue_enum)

        if "error" in stats:
            raise HTTPException(status_code=500, detail=stats["error"])

        response = QueueStatsResponse(**stats)

        if stats.get("length", 0) > 0:
            # Get more detailed items for this specific queue
            items_data = redis_manager.get_dead_letter_items(queue_enum, 0, 20)
            response.sample_items = items_data.get("items", [])

        return response

    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to get stats for {queue_type}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


@router.get("/items/{queue_type}", response_model=DeadLetterItemsResponse)
async def get_dead_letter_items(
        queue_type: str,
        start: int = Query(0, ge=0, description="Start index"),
        end: int = Query(10, ge=0, le=100, description="End index")
):
    """Get items from dead letter queue for inspection"""
    try:
        # Validate and convert to enum
        try:
            queue_enum = RedisQueueNames[queue_type.upper()]
        except KeyError:
            valid_types = [q.name for q in RedisQueueNames]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid queue type: {queue_type}. Valid types: {valid_types}"
            )

        if end < start:
            raise HTTPException(status_code=400, detail="End index must be >= start index")

        items_data = redis_manager.get_dead_letter_items(queue_enum, start, end)

        if "error" in items_data:
            raise HTTPException(status_code=500, detail=items_data["error"])

        return DeadLetterItemsResponse(**items_data)

    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to get items from {queue_type}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get items: {str(e)}")


@router.post("/requeue", response_model=RequeueAllResponse)
async def requeue_all_dead_letters():
    """
    Requeue all items from every dead-letter queue into their respective tasks.
    """
    # map each DLQ enum to the task name it should trigger
    task_mapping = {
        RedisQueueNames.DLQ_SCAN_FILE: config.taskqueue.scan_request_task,
        RedisQueueNames.DLQ_VERDICT_ACTION: config.taskqueue.verdict_action_task,
    }

    overall_success = True
    results: List[RequeueAllDetail] = []

    for queue_enum, celery_task in task_mapping.items():
        try:
            result = redis_manager.requeue_from_dead_letter(
                queue_name = queue_enum,
                max_items = None,
                celery_task_name = celery_task,
            )
            if "error" in result:
                overall_success = False
                dsx_logging.error(f"Error requeueing {queue_enum.name}: {result['error']}")
                # report zero requeues on error
                results.append(RequeueAllDetail(
                    queue_type=result.get("queue_type", queue_enum.name),
                    queue_name=result.get("queue_name", queue_enum.name),
                    requeued_count=0,
                    remaining_count=result.get("remaining_count", 0),
                ))
            else:
                results.append(RequeueAllDetail(
                    queue_type=result["queue_type"],
                    queue_name=result["queue_name"],
                    requeued_count=result["requeued_count"],
                    remaining_count=result["remaining_count"],
                ))
        except Exception as e:
            overall_success = False
            dsx_logging.error(f"Exception requeueing {queue_enum.name}: {e}", exc_info=True)
            results.append(RequeueAllDetail(
                queue_type=queue_enum.name,
                queue_name=queue_enum.name,
                requeued_count=0,
                remaining_count=0,
            ))

    return RequeueAllResponse(success=overall_success, results=results)


@router.post("/requeue/{queue_type}", response_model=RequeueResponse)
async def requeue_specific_queue(
        queue_type: str,
        max_items: int = Query(100, ge=1, le=1000, description="Maximum items to requeue"),
        target_task: Optional[str] = Query(None, description="Celery task name to send items to")
):
    """Requeue items from a specific dead letter queue (alternative endpoint)"""
    try:
        # Validate and convert to enum
        try:
            queue_enum = RedisQueueNames[queue_type.upper()]
        except KeyError:
            valid_types = [q.name for q in RedisQueueNames]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid queue type: {queue_type}. Valid types: {valid_types}"
            )

        request = RequeueRequest(
            queue_name=queue_enum,
            max_items=max_items,
            target_task=target_task
        )

        return await requeue_dead_letters(request)

    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to requeue {queue_type}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to requeue: {str(e)}")


@router.delete("/clear/{queue_type}", response_model=ClearQueueResponse)
async def clear_specific_queue(queue_type: str):
    """Clear all items from a specific dead letter queue"""
    try:
        # Validate and convert to enum
        try:
            queue_enum = RedisQueueNames[queue_type.upper()]
        except KeyError:
            valid_types = [q.name for q in RedisQueueNames]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid queue type: {queue_type}. Valid types: {valid_types}"
            )

        result = redis_manager.clear_dead_letter_queue(queue_enum)

        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])

        return ClearQueueResponse(
            success=True,
            queue_type=result["queue_type"],
            queue_name=result["queue_name"],
            cleared_count=result["cleared_count"]
        )

    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to clear {queue_type}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear queue: {str(e)}")


@router.delete("/clear", response_model=List[ClearQueueResponse])
async def clear_all_dead_letter_queues():
    """Clear all dead letter queues (use with extreme caution!)"""
    try:
        results = []

        for queue_enum in RedisQueueNames:
            try:
                result = redis_manager.clear_dead_letter_queue(queue_enum)

                if "error" in result:
                    dsx_logging.error(f"Failed to clear {queue_enum.name}: {result['error']}")
                    continue

                results.append(ClearQueueResponse(
                    success=True,
                    queue_type=result["queue_type"],
                    queue_name=result["queue_name"],
                    cleared_count=result["cleared_count"]
                ))
            except Exception as e:
                dsx_logging.error(f"Failed to clear {queue_enum.name}: {e}")
                continue

        if not results:
            raise HTTPException(status_code=500, detail="Failed to clear any queues")

        dsx_logging.warning(f"Cleared all dead letter queues: {[r.queue_type for r in results]}")
        return results

    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to clear all dead letter queues: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear queues: {str(e)}")


# ===== HEALTH CHECK =====

@router.get("/health")
async def dead_letter_health_check():
    """Health check for dead letter queue system"""
    try:
        # Test Redis connection
        client = redis_manager.get_client()
        client.ping()
        client.close()

        # Get basic stats
        stats = redis_manager.get_all_dead_letter_stats()
        total_items = sum(s.get("length", 0) for s in stats.values() if "error" not in s)

        return {
            "status": "healthy",
            "redis_connected": True,
            "total_dead_letter_items": total_items,
            "available_queues": list(RedisQueueNames.__members__.keys())
        }

    except Exception as e:
        dsx_logging.error(f"Dead letter health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "redis_connected": False
        }
