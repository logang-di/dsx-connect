from typing import Optional, Literal, Any

from celery.result import AsyncResult
from fastapi import APIRouter, Request, Response, Header, Path
from celery.exceptions import CeleryError, TimeoutError as CeleryTimeoutError
from kombu.exceptions import OperationalError as BrokerOperationalError
from pydantic import BaseModel
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from shared.dsx_logging import dsx_logging
from shared.routes import (API_PREFIX_V1, DSXConnectAPI, ScanPath,
                           api_path, Action, route_name, route_path)
from dsx_connect.models.connector_models import ScanRequestModel
from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.names import Tasks, Queues
from shared.status_responses import StatusResponse, StatusResponseEnum


class ScanRequestStatus(BaseModel):
    task_id: str
    state: Literal["PENDING", "RECEIVED", "STARTED", "RETRY", "FAILURE", "SUCCESS"]
    status: StatusResponseEnum  # SUCCESS/ERROR for your API contract
    result: Optional[Any] = None  # include only on SUCCESS (if you store a result)
    error: Optional[str] = None  # include only on FAILURE


router = APIRouter(prefix=route_path(API_PREFIX_V1))


@router.post(route_path(DSXConnectAPI.SCAN_PREFIX.value, ScanPath.REQUEST.value),
             name=route_name(DSXConnectAPI.SCAN_PREFIX, ScanPath.REQUEST, Action.CREATE),
             description="Queue a scan request.",
             status_code=202,
             response_model=StatusResponse)
async def post_scan_request(
        scan_request_info: ScanRequestModel,
        response: Response,
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")) -> StatusResponse:
    try:
        dsx_logging.debug(f"Queuing scan task {scan_request_info.location}")

        # (Optional) Idempotency: if provided, you can de-dupe here using Redis before send_task.
        # Skipping the storage logic for brevity; pattern shown for header plumb-through.

        result = celery_app.send_task(
            Tasks.REQUEST,
            queue=Queues.REQUEST,
            args=[scan_request_info.model_dump()],)

        # Location header: /api/v1/dsx-connect/scan-request/{task_id}
        task_id = result.id
        response.headers["Location"] = api_path(DSXConnectAPI.SCAN_PREFIX, ScanPath.REQUEST, task_id)

        return StatusResponse(
            status=StatusResponseEnum.SUCCESS,
            description=f"Scan task queued for connector: {scan_request_info.connector_url}",
            message=f"Scan task ID: {result.id}")

    except (BrokerOperationalError, RedisConnectionError) as e:
        # Broker/Redis unreachable -> 503 Service Unavailable
        dsx_logging.exception("Broker/Redis connection error during send_task")
        response.status_code = 503
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            description="Task broker unavailable",
            message=str(e),
        )
    except (RedisTimeoutError, CeleryTimeoutError) as e:
        # Timeouts talking to broker/backend -> 504 Gateway Timeout
        dsx_logging.exception("Timeout talking to broker/backend during send_task")
        response.status_code = 504
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            description="Timed out queuing scan task",
            message=str(e),
        )
    except CeleryError as e:
        # Celery-level issues (serialization, routing, etc.) -> 502 Bad Gateway
        dsx_logging.exception("Celery error during send_task")
        response.status_code = 502
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            description="Failed to queue scan task (celery error)",
            message=str(e),
        )
    except Exception as e:
        # Unknown failure -> 500 Internal Server Error
        dsx_logging.exception("Unexpected error queuing scan task")
        response.status_code = 500
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            description="Failed to queue scan task",
            message=str(e),
        )


@router.get(
    route_path(DSXConnectAPI.SCAN_PREFIX.value, ScanPath.REQUEST.value, "{task_id}"),
    name=route_name(DSXConnectAPI.SCAN_PREFIX, ScanPath.REQUEST, Action.STATUS),
    response_model=ScanRequestStatus,
    description="Poll the status of a previously queued scan task."
)
async def get_scan_status(task_id: str = Path(...), response: Response = None) -> ScanRequestStatus:
    ar = AsyncResult(task_id, app=celery_app)

    # Map Celery state to your API status
    if ar.state in {"PENDING", "RECEIVED", "STARTED", "RETRY"}:
        # Optional: hint clients to back off a bit
        if response is not None:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Retry-After"] = "2"
        return ScanRequestStatus(task_id=task_id, state=ar.state, status=StatusResponseEnum.SUCCESS)

    if ar.state == "SUCCESS":
        # If your worker returns a value, expose it here (safe to omit if not used)
        return ScanRequestStatus(task_id=task_id, state="SUCCESS", status=StatusResponseEnum.SUCCESS, result=ar.result)

    # FAILURE (and unknowns) -> include error info if present
    err_msg = None
    try:
        err_msg = str(ar.result) if ar.result else None
    except Exception:
        pass
    return ScanRequestStatus(task_id=task_id, state="FAILURE", status=StatusResponseEnum.ERROR, error=err_msg)
