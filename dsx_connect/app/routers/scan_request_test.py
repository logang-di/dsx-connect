from io import BytesIO

import httpx
import requests

from fastapi import APIRouter, BackgroundTasks

from dsx_connect.dsxa_client.verdict_models import DPAVerdictEnum
from dsx_connect.common.endpoint_names import DSXConnectAPIEndpoints, ConnectorEndpoints
from dsx_connect.dsxa_client.dsxa_client import DSXAClient, DSXAScanRequest
from dsx_connect.models.connector_models import ScanRequestModel
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum

from dsx_connect.config import ConfigManager

from dsx_connect.utils.app_logging import dsx_logging

router = APIRouter()


async def process_scan_request(scan_request_info: ScanRequestModel) -> StatusResponse:
    dsx_logging.info(
        f'Processing scan_request_test on {scan_request_info} from connector {scan_request_info.connector_url}')

    headers = {}
    # TODO there needs to be a better way to define what the API call should be, but for now this works
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f'{scan_request_info.connector_url}{ConnectorEndpoints.READ_FILE}',
            json=scan_request_info.dict()
        )

    bytes_content = None
    if response.status_code == 200:
        bytes_content = BytesIO(response.content)  # Store binary response in BytesIO
        bytes_content.seek(0)
        dsx_logging.debug(f"Received {bytes_content.getbuffer().nbytes} bytes")
    else:
        dsx_logging.error(f"Error response: {response.status_code}")
        return StatusResponse(status=StatusResponseEnum.ERROR,
                              message=f'Did not receive file from /read_file',
                              description=f'Status code returned: {response.status_code}')

    # scan the file
    dsxa_client = DSXAClient(scan_binary_url=ConfigManager.get_config().scanner.scan_binary_url)
    dpa_verdict = await dsxa_client.scan_binary_async(scan_request=
                                                      DSXAScanRequest(binary_data=bytes_content,
                                                                      metadata_info=f"file-tag:{scan_request_info.metainfo}"))

    if dpa_verdict.verdict == DPAVerdictEnum.MALICIOUS:
        dsx_logging.info('Verdict MALICIOUS - calling item_action on connector')
        # TODO there needs to be a better way to define what the API call should be, but fornow this works
        response = requests.post(f'{scan_request_info.connector_url}/item_action',
                                 headers=headers,
                                 json=scan_request_info.dict(),
                                 verify=False)

    return StatusResponse(status=StatusResponseEnum.SUCCESS,
                          message=f'Scan_task_test processes for: {scan_request_info}')


@router.post(DSXConnectAPIEndpoints.SCAN_REQUEST_TEST,
             description="Used for testing scan request workflow without the need for "
                         "queues, and celery_app processors.  Cycles through "
                         "the entire workflow "
                         "scan_request --> connector.read_file --> connector.item_action",
             )
async def post_scan_request_test(scan_request_info: ScanRequestModel,
                                 background_tasks: BackgroundTasks):
    dsx_logging.info(f'Enqueuing scan_request_test for {scan_request_info}')
    background_tasks.add_task(process_scan_request, scan_request_info)
    # Return immediately while the processing is done in the background
    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f'Scan request queued for: {scan_request_info}',
        description='Processing in background...'
    )
