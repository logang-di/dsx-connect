# dsx_connect/app/routers/dead_letter.py
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Optional, Any, Dict, List

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import BaseModel
from redis.asyncio import Redis

from shared.dsx_logging import dsx_logging
from shared.routes import (
    API_PREFIX_V1,
    DSXConnectAPI,
    DeadLetterPath,
    route_name,
    Action,
    route_path,
)
from dsx_connect.messaging.topics import Topics, NS
from dsx_connect.taskworkers.names import Tasks
from dsx_connect.taskworkers.celery_app import celery_app


# ------------------------------------------------------------------------------
# Router
# ------------------------------------------------------------------------------
router = APIRouter(
    prefix=route_path(API_PREFIX_V1, DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX),
    tags=["dead-letter"],
)

# ------------------------------------------------------------------------------
# Types & helpers
# ------------------------------------------------------------------------------
class DeadLetterType(str, Enum):
    scan_request = "scan_request"
    verdict_action = "verdict_action"

# New, unified DLQ list namespace: "{NS}:dlq:<type>"
_DLQ_LIST_BASE = f"{NS}:dlq"

def dlq_key(q: DeadLetterType) -> str:
    return f"{_DLQ_LIST_BASE}:{q.value}"

def default_task_for(q: DeadLetterType) -> str:
    return {
        DeadLetterType.scan_request: Tasks.REQUEST,
        DeadLetterType.verdict_action: Tasks.VERDICT,
    }[q]

def _need_redis(request: Request) -> Redis:
    r: Optional[Redis] = getattr(request.app.state, "redis", None)
    if r is None:
        raise HTTPException(status_code=503, detail="redis_unavailable")
    return r

def _json_loads_safe(s: str | bytes) -> dict[str, Any]:
    try:
        if isinstance(s, (bytes, bytearray)):
            s = s.decode("utf-8", errors="replace")
        return json.loads(s)
    except Exception:
        return {"raw": s}

async def _queue_stats(r: Redis, key: str) -> Dict[str, Any]:
    try:
        exists = bool(await r.exists(key))
        length = int(await r.llen(key)) if exists else 0
        ttl = await r.ttl(key) if exists else -2  # -2 no key, -1 no expiry
        return {
            "queue_name": key,
            "exists": exists,
            "length": length,
            "ttl_seconds": int(ttl if ttl is not None else -1),
        }
    except Exception as e:
        return {"error": str(e), "queue_name": key}

async def _queue_items(r: Redis, key: str, start: int, end: int) -> Dict[str, Any]:
    """LRANGE slice (inclusive end)."""
    try:
        total = int(await r.llen(key))
        raw_items = await r.lrange(key, start, end)
        items = [_json_loads_safe(x) for x in raw_items]
        return {
            "queue_name": key,
            "total_length": total,
            "returned_count": len(items),
            "start_index": start,
            "end_index": end,
            "items": items,
        }
    except Exception as e:
        return {"error": str(e), "queue_name": key}

async def _notify_dlq(r: Redis, event: dict) -> None:
    try:
        await r.publish(Topics.NOTIFY_DLQ, json.dumps(event, separators=(",", ":")))
    except Exception as e:
        dsx_logging.warning(f"DLQ notify failed: {e}")

async def _requeue_from_dead_letter(
        r: Redis, queue_name: str, max_items: Optional[int], celery_task_name: str
) -> Dict[str, Any]:
    """
    Pop up to max_items from the DLQ (left/oldest) and re-submit to Celery.
    On Celery send failure, the item is pushed back (right side).
    """
    requeued = 0
    failed = 0

    try:
        to_process = int(await r.llen(queue_name))
        if to_process == 0:
            return {"queue_name": queue_name, "requeued_count": 0, "remaining_count": 0, "failed_count": 0}

        limit = to_process if max_items is None else min(to_process, int(max_items))

        for _ in range(limit):
            payload = await r.lpop(queue_name)
            if payload is None:
                break
            data = _json_loads_safe(payload)
            try:
                celery_app.send_task(celery_task_name, args=[data])
                requeued += 1
            except Exception as e:
                failed += 1
                try:
                    await r.rpush(queue_name, payload)
                except Exception:
                    dsx_logging.error(f"Failed to restore DLQ item after Celery error: {e}")

        remaining = int(await r.llen(queue_name))
        return {
            "queue_name": queue_name,
            "requeued_count": requeued,
            "remaining_count": remaining,
            "failed_count": failed,
        }

    except Exception as e:
        return {"error": str(e), "queue_name": queue_name}

async def _clear_dead_letter_queue(r: Redis, key: str) -> Dict[str, Any]:
    try:
        # delete returns number of keys removed (0 or 1 here)
        removed = int(await r.delete(key))
        # We also return how many list items were removed by capturing length first if you want;
        # for now, mirror old behavior.
        return {"queue_name": key, "cleared_count": removed}
    except Exception as e:
        return {"error": str(e), "queue_name": key}

# ------------------------------------------------------------------------------
# Response models
# ------------------------------------------------------------------------------
class QueueStatsResponse(BaseModel):
    queue_name: str
    queue_type: str
    length: int
    ttl_seconds: int
    exists: bool
    sample_items: Optional[list[dict[str, Any]]] = None

class DeadLetterSummaryResponse(BaseModel):
    total_items: int
    active_queues: int
    queues: dict[str, QueueStatsResponse]

class RequeueResponse(BaseModel):
    success: bool
    queue_type: str
    queue_name: str
    requeued_count: int
    remaining_count: int
    failed_count: Optional[int] = 0

class ClearQueueResponse(BaseModel):
    success: bool
    queue_type: str
    queue_name: str
    cleared_count: int

class DeadLetterItemsResponse(BaseModel):
    queue_type: str
    queue_name: str
    total_length: int
    returned_count: int
    start_index: int
    end_index: int
    items: list[dict[str, Any]]

class RequeueAllDetail(BaseModel):
    queue_type: str
    queue_name: str
    requeued_count: int
    remaining_count: int

class RequeueAllResponse(BaseModel):
    success: bool
    results: list[RequeueAllDetail]

# ------------------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------------------
@router.get(
    route_path(DeadLetterPath.STATS),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.STATS, Action.STATS),
    response_model=DeadLetterSummaryResponse,
    status_code=status.HTTP_200_OK,
)
async def get_dead_letter_stats(request: Request):
    r = _need_redis(request)
    try:
        detailed: Dict[str, QueueStatsResponse] = {}
        total = 0
        active = 0

        for qtype in DeadLetterType:
            key = dlq_key(qtype)
            stats = await _queue_stats(r, key)
            if "error" in stats:
                dsx_logging.error(f"Stats error for {qtype.value}: {stats['error']}")
                detailed[qtype.value] = QueueStatsResponse(
                    queue_name=key, queue_type=qtype.value, length=0, ttl_seconds=-1, exists=False
                )
                continue

            length = int(stats.get("length", 0))
            resp = QueueStatsResponse(
                queue_name=key,
                queue_type=qtype.value,
                length=length,
                ttl_seconds=int(stats.get("ttl_seconds", -1)),
                exists=bool(stats.get("exists", True)),
            )

            if length > 0:
                try:
                    sample = await _queue_items(r, key, 0, 5)
                    resp.sample_items = sample.get("items", [])
                except Exception as e:
                    dsx_logging.warning(f"Sample items failed for {qtype.value}: {e}")
                    resp.sample_items = []
                active += 1
                total += length

            detailed[qtype.value] = resp

        return DeadLetterSummaryResponse(total_items=total, active_queues=active, queues=detailed)
    except Exception as e:
        dsx_logging.error(f"Failed to get dead letter stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get dead letter stats: {e}")

@router.get(
    route_path(DeadLetterPath.STATS_ONE),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.STATS_ONE, Action.GET),
    response_model=QueueStatsResponse,
)
async def get_queue_specific_stats(request: Request, queue_type: DeadLetterType = Path(...)):
    r = _need_redis(request)
    try:
        key = dlq_key(queue_type)
        stats = await _queue_stats(r, key)
        if "error" in stats:
            raise HTTPException(status_code=500, detail=stats["error"])

        length = int(stats.get("length", 0))
        resp = QueueStatsResponse(
            queue_name=key,
            queue_type=queue_type.value,
            length=length,
            ttl_seconds=int(stats.get("ttl_seconds", -1)),
            exists=bool(stats.get("exists", True)),
        )
        if length > 0:
            sample = await _queue_items(r, key, 0, 20)
            resp.sample_items = sample.get("items", [])
        return resp
    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to get stats for {queue_type.value}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {e}")

@router.get(
    route_path(DeadLetterPath.ITEMS),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.ITEMS, Action.LIST),
    response_model=DeadLetterItemsResponse,
)
async def get_dead_letter_items(
        request: Request,
        queue_type: DeadLetterType = Path(...),
        start: int = Query(0, ge=0),
        end: int = Query(10, ge=0, le=100),
):
    r = _need_redis(request)
    try:
        if end < start:
            raise HTTPException(status_code=400, detail="End index must be >= start index")
        key = dlq_key(queue_type)
        data = await _queue_items(r, key, start, end)
        if "error" in data:
            raise HTTPException(status_code=500, detail=data["error"])
        return DeadLetterItemsResponse(
            queue_type=queue_type.value,
            queue_name=data.get("queue_name", key),
            total_length=int(data.get("total_length", 0)),
            returned_count=int(data.get("returned_count", 0)),
            start_index=int(data.get("start_index", start)),
            end_index=int(data.get("end_index", end)),
            items=data.get("items", []),
        )
    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to get items from {queue_type.value}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get items: {e}")

@router.post(
    route_path(DeadLetterPath.REQUEUE),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.REQUEUE, Action.REQUEUE),
    response_model=RequeueAllResponse,
)
async def requeue_all_dead_letters(request: Request):
    r = _need_redis(request)
    ok = True
    results: List[RequeueAllDetail] = []
    for qtype in DeadLetterType:
        key = dlq_key(qtype)
        celery_task = str(default_task_for(qtype))
        try:
            res = await _requeue_from_dead_letter(r, queue_name=key, max_items=None, celery_task_name=celery_task)
            if "error" in res:
                ok = False
                dsx_logging.error(f"Error requeueing {qtype.value}: {res['error']}")
                results.append(RequeueAllDetail(queue_type=qtype.value, queue_name=key, requeued_count=0, remaining_count=res.get("remaining_count", 0)))  # noqa: E501
            else:
                results.append(RequeueAllDetail(queue_type=qtype.value, queue_name=key, requeued_count=int(res.get("requeued_count", 0)), remaining_count=int(res.get("remaining_count", 0))))  # noqa: E501
                await _notify_dlq(r, {"type": "requeue", "queue_type": qtype.value, "queue_name": key, "timestamp": time.time()})  # noqa: E501
        except Exception as e:
            ok = False
            dsx_logging.error(f"Exception requeueing {qtype.value}: {e}", exc_info=True)
            results.append(RequeueAllDetail(queue_type=qtype.value, queue_name=key, requeued_count=0, remaining_count=0))
    return RequeueAllResponse(success=ok, results=results)

@router.post(
    route_path(DeadLetterPath.REQUEUE_ONE),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.REQUEUE_ONE, Action.REQUEUE),
    response_model=RequeueResponse,
)
async def requeue_specific_queue(
        request: Request,
        queue_type: DeadLetterType = Path(...),
        max_items: int = Query(100, ge=1, le=1000),
        target_task: Optional[str] = Query(None),
):
    r = _need_redis(request)
    try:
        key = dlq_key(queue_type)
        celery_task = target_task or str(default_task_for(queue_type))
        res = await _requeue_from_dead_letter(r, queue_name=key, max_items=max_items, celery_task_name=celery_task)
        if "error" in res:
            raise HTTPException(status_code=500, detail=res["error"])
        await _notify_dlq(
            r,
            {"type": "requeue", "queue_type": queue_type.value, "queue_name": key, "requeued_count": int(res.get("requeued_count", 0)), "timestamp": time.time()},  # noqa: E501
        )
        return RequeueResponse(
            success=True,
            queue_type=queue_type.value,
            queue_name=key,
            requeued_count=int(res.get("requeued_count", 0)),
            remaining_count=int(res.get("remaining_count", 0)),
            failed_count=int(res.get("failed_count", 0)),
        )
    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to requeue {queue_type.value}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to requeue: {e}")

@router.delete(
    route_path(DeadLetterPath.CLEAR_ONE),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.CLEAR_ONE, Action.CLEAR),
    response_model=ClearQueueResponse,
)
async def clear_specific_queue(request: Request, queue_type: DeadLetterType = Path(...)):
    r = _need_redis(request)
    try:
        key = dlq_key(queue_type)
        res = await _clear_dead_letter_queue(r, key)
        if "error" in res:
            raise HTTPException(status_code=500, detail=res["error"])
        await _notify_dlq(r, {"type": "clear", "queue_type": queue_type.value, "queue_name": key, "timestamp": time.time()})  # noqa: E501
        return ClearQueueResponse(success=True, queue_type=queue_type.value, queue_name=key, cleared_count=int(res.get("cleared_count", 0)))  # noqa: E501
    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to clear {queue_type.value}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear queue: {e}")

@router.delete(
    route_path(DeadLetterPath.CLEAR),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.CLEAR, Action.CLEAR),
    response_model=list[ClearQueueResponse],
)
async def clear_all_dead_letter_queues(request: Request):
    r = _need_redis(request)
    try:
        out: list[ClearQueueResponse] = []
        for qtype in DeadLetterType:
            key = dlq_key(qtype)
            try:
                res = await _clear_dead_letter_queue(r, key)
                if "error" in res:
                    dsx_logging.error(f"Failed to clear {qtype.value}: {res['error']}")
                    continue
                out.append(ClearQueueResponse(success=True, queue_type=qtype.value, queue_name=key, cleared_count=int(res.get("cleared_count", 0))))  # noqa: E501
                await _notify_dlq(r, {"type": "clear", "queue_type": qtype.value, "queue_name": key, "timestamp": time.time()})  # noqa: E501
            except Exception as e:
                dsx_logging.error(f"Failed to clear {qtype.value}: {e}")
                continue
        if not out:
            raise HTTPException(status_code=500, detail="Failed to clear any queues")
        dsx_logging.warning(f"Cleared all dead letter queues: {[r.queue_type for r in out]}")
        return out
    except HTTPException:
        raise
    except Exception as e:
        dsx_logging.error(f"Failed to clear all dead letter queues: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to clear queues: {e}")

@router.get(
    route_path(DeadLetterPath.HEALTH),
    name=route_name(DSXConnectAPI.ADMIN_DEAD_LETTER_QUEUE_PREFIX, DeadLetterPath.HEALTH, Action.HEALTH),
    status_code=status.HTTP_200_OK,
)
async def dead_letter_health_check(request: Request):
    r = _need_redis(request)
    try:
        await r.ping()
        total = 0
        for qtype in DeadLetterType:
            stats = await _queue_stats(r, dlq_key(qtype))
            if "error" not in stats:
                total += int(stats.get("length", 0))
        return {
            "status": "healthy",
            "redis_connected": True,
            "total_dead_letter_items": total,
            "available_queues": [q.value for q in DeadLetterType],
        }
    except Exception as e:
        dsx_logging.error(f"Dead letter health check failed: {e}")
        return {"status": "unhealthy", "error": str(e), "redis_connected": False}
