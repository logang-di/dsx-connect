from fastapi import APIRouter

from dsx_connect.utils.logging import dsx_logging
from dsx_connect.models.connector_models import ScanRequestModel
from dsx_connect.config import ConfigManager
from dsx_connect.models.constants import DSXConnectAPIEndpoints
from dsx_connect.taskqueue.celery_app import celery_app
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum

router = APIRouter()


@router.post(DSXConnectAPIEndpoints.SCAN_REQUEST, description="Queue a scan request.")
async def post_scan_request(scan_request_info: ScanRequestModel) -> StatusResponse:
    try:
        dsx_logging.debug(f"Queuing scan task {scan_request_info.location}")
        result = celery_app.send_task(
            ConfigManager.get_config().taskqueue.scan_request_task,
            queue=ConfigManager.get_config().taskqueue.scan_request_queue,
            args=[scan_request_info.dict()])
        return StatusResponse(
            status=StatusResponseEnum.SUCCESS,
            description=f"Scan task queued for connector: {scan_request_info.connector_url}",
            message=f"Scan task ID: {result.id}")
    except Exception as celery_error:
        dsx_logging.error(f"Celery task error: {celery_error}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            description="Failed to queue scan task",
            message=str(celery_error))
