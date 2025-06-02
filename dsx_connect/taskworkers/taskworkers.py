"""Task worker module for dsx-connect.

This module defines Celery tasks for processing scan requests, verdicts, and scan result storage
in the dsx-connect system. It integrates with external services via HTTP to fetch file content,
scan binaries for malware, perform actions based on verdicts, and store results persistently.
The tasks are designed to run synchronously in a Celery worker process.

The primary task, `scan_request_task`, fetches file content, scans it, and dispatches to
`verdict_action_task` for action-taking and `scan_result_task` for persistent storage.
`verdict_action_task` processes verdicts and executes actions if needed. `scan_result_task`
stores scan results in a database.

Usage:
    Run as a Celery worker from the dsx-connect root directory:
    ```bash
    celery -A dsx_connect.taskqueue.celery_app worker --loglevel=info -Q scan_request_queue,verdict_action_queue,scan_result_queue
    ```
    Or run standalone for debugging:
    ```bash
    python taskworkers/taskworkers.py
    ```

Dependencies:
    - httpx: For synchronous HTTP requests.
    - celery: For task queue management.
    - dsx_connect: Internal models, config, and client utilities.
"""
import threading
import unicodedata
from io import BytesIO
from typing import Dict, Optional

import httpx
from celery.signals import worker_process_init
from pydantic import ValidationError

from dsx_connect.database.scan_stats_worker import ScanStatsWorker
from dsx_connect.dsxa_client.verdict_models import DPAVerdictEnum, DPAVerdictModel2
from dsx_connect.models.constants import ConnectorEndpoints
from dsx_connect.database.scan_results_base_db import ScanResultsBaseDB
from dsx_connect.database.scan_stats_base_db import ScanStatsBaseDB
from dsx_connect.dsxa_client.dsxa_client import DSXAClient, DSXAScanRequest
from dsx_connect.models.connector_models import ScanRequestModel
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum
from dsx_connect.models.scan_models import ScanResultModel, ScanResultStatusEnum, ScanStatsModel
from dsx_connect.taskqueue.celery_app import celery_app
from dsx_connect.config import DatabaseConfig, ConfigDatabaseType
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.config import ConfigManager


# Shared client pools and scan client per worker process
_connector_clients: Dict[str, httpx.Client] = {}
# Lock for thread-safe access to the client pool
_client_pool_lock = threading.Lock()
_redis_client = None
_scan_results_db: Optional[ScanResultsBaseDB] = None  # Assuming initialized via database_scan_results_factory
_scan_stats_db: Optional[ScanStatsBaseDB] = None  # Assuming initialized via database_scan_stats_factory
_scan_stats_worker: Optional[ScanStatsWorker] = None  # Assuming initialized via passing _scan_stats_db

config = ConfigManager.reload_config()

def get_connector_client(connector_url: str) -> httpx.Client:
    """
    Retrieve or create an httpx.Client for the given connector_url.

    Args:
        connector_url (str): The URL of the connector.

    Returns:
        httpx.Client: The HTTP client for the connector.
    """
    global _connector_clients
    with _client_pool_lock:
        if connector_url not in _connector_clients:
            _connector_clients[connector_url] = httpx.Client(verify=False, timeout=30)
            dsx_logging.debug(f"Created new httpx.Client for {connector_url}")
        return _connector_clients[connector_url]


@worker_process_init.connect
def init_worker(**kwargs):
    """Initialize shared httpx.Client for scan requests and empty connector client pool."""
    global _connector_clients
    global _scan_results_db
    global _scan_stats_db
    global _scan_stats_worker
    _connector_clients = {}
    dsx_logging.debug("Initialized shared httpx.Client for scan requests and empty connector pool")

    from dsx_connect.database.database_factory import database_scan_results_factory
    db_config = DatabaseConfig()
    _scan_results_db = database_scan_results_factory(
        database_type=db_config.type,
        database_loc=db_config.loc,
        retain=db_config.retain,
        collection_name="scan_results"
    )
    dsx_logging.info(f"Initialized scan results database of type {db_config.type} at {db_config.loc}")

    from dsx_connect.database.database_factory import database_scan_stats_factory
    from dsx_connect.database.scan_stats_worker import ScanStatsWorker
    _scan_stats_db = database_scan_stats_factory(
        database_type=ConfigDatabaseType.TINYDB,
        database_loc=db_config.scan_stats_db,
        collection_name="scan_stats"
    )
    _scan_stats_worker = ScanStatsWorker(_scan_stats_db)
    dsx_logging.debug("Initialized shared httpx.Client and database")

    # By initializing syslog inside init_worker, each worker process gets its own syslog handler, ensuring thread/process
    # safety in the event there is more than one worker/concurrency
    # Importing log_chain and calling init_syslog_handler at the module level could trigger side effects (e.g.,
    # network I/O to connect to the syslog server) during import, which might fail if the environment isn’t ready
    # (e.g., no network, invalid config). Deferring this to init_worker ensures it happens when the worker
    # is fully operational.
    from dsx_connect.utils.log_chain import init_syslog_handler
    init_syslog_handler(syslog_host="localhost", syslog_port=514)


@celery_app.task(name=config.taskqueue.scan_request_task)
def scan_request_task(scan_request_dict: dict) -> dict:
    """
    Process a scan request by fetching file content and scanning it for malware.

    This task retrieves file content from a connector URL, scans it using the DSXAClient,
    and sends the verdict (DPAVerdict2) to the verdict queue. It uses a single httpx.Client
    instance for all HTTP requests within the task to optimize connection reuse.

    Args:
        scan_request_dict: A dictionary containing scan request details, conforming to
            ScanRequestModel (e.g., {"location": "file.txt", "metainfo": "test",
            "connector_url": "http://example.com"}).

    Returns:
        dict: A StatusResponse dictionary indicating success or failure.

    Raises:
        None: All exceptions are caught and converted to error responses.
    """
    task_id = scan_request_task.request.id if hasattr(scan_request_task, 'request') else None
    dsx_logging.debug(f"Process task id: {task_id}")

    # 1. Validate and parse scan request
    try:
        scan_request = ScanRequestModel(**scan_request_dict)
        dsx_logging.debug(f"Processing scan request for {scan_request.location} with {scan_request.connector_url}")
    except ValidationError as e:
        dsx_logging.error(f"Failed to validate scan request: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Invalid scan request data",
            description=f"Failed celery task id in id field.  {str(e)}",
            id=task_id
        ).model_dump()

    # 2. Fetch file content from connector
    try:
        client = get_connector_client(scan_request.connector_url)
        response = client.post(
            f'{scan_request.connector_url}{ConnectorEndpoints.READ_FILE}',
            json=scan_request.model_dump()
        )
        response.raise_for_status()  # Raises HTTPError for 4xx/5xx responses
        bytes_content = BytesIO(response.content)
        bytes_content.seek(0)
        dsx_logging.debug(f"Received {bytes_content.getbuffer().nbytes} bytes")
    except httpx.HTTPError as e:
        dsx_logging.error(f"Failed to fetch file from connector: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Failed to fetch file from connector",
            description=f"HTTP error: {str(e)}",
            id=task_id
        ).model_dump()
    except Exception as e:
        dsx_logging.error(f"Unexpected error while fetching file: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Unexpected error while fetching file",
            description=str(e),
            id=task_id
        ).model_dump()

    # 3. Scan the file with DSXAClient
    dsxa_client = DSXAClient(scan_binary_url=config.scanner.scan_binary_url)
    try:
        safe_meta = unicodedata.normalize("NFKD", scan_request.metainfo).encode("ascii", "ignore").decode("ascii")
        metadata_info = f"file-tag:{safe_meta}"
        if task_id:
            metadata_info += f",task-id:{task_id}"
        dpa_verdict = dsxa_client.scan_binary(
            scan_request=DSXAScanRequest(
                binary_data=bytes_content,
                metadata_info=metadata_info
            )
        )
        dsx_logging.debug(f"Verdict: {dpa_verdict.verdict}")
    except Exception as e:
        dsx_logging.error(f"Scan failed: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message=f"Failed to scan file {scan_request.location}",
            description=str(e),
            id=task_id
        ).model_dump()

    # 4. Send verdict to verdict queue with original task_id
    try:
        # TODO - do we need to send benign verdicts if nothing going to happen with it?
        # Send to verdict_action_queue for action-taking
        task1 = celery_app.send_task(
            config.taskqueue.verdict_action_task,
            queue=config.taskqueue.verdict_action_queue,
            args=[scan_request_dict, dpa_verdict.model_dump(), task_id]
        )
        dsx_logging.debug(f"Sent verdict for {scan_request.location} to {config.taskqueue.verdict_action_queue} with task_id {task1.id}")

        # Send to scan_result_queue for persistent storage
        task2 = celery_app.send_task(
            config.taskqueue.scan_result_task,
            queue=config.taskqueue.scan_result_queue,
            args=[scan_request_dict, dpa_verdict.model_dump(), task_id]
        )
        dsx_logging.debug(f"Sent scan result for {scan_request.location} to {config.taskqueue.scan_result_queue} with task_id {task2.id}")

    except Exception as e:
        dsx_logging.error(f"Scan or queue dispatch failed: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message=f"Failed to send scan result to queue {config.taskqueue.verdict_action_queue} and/or {config.taskqueue.scan_result_queue}",
            description=str(e),
            id=task_id
        ).model_dump()

    # 5. Return success response
    dsx_logging.info(f"Scan completed for {scan_request.location}")
    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Scan completed for {scan_request.location}",
        description=f"Complete scan information: {scan_request}; sent verdict to verdict queue with task_id: {task1.id}; verdict {dpa_verdict}",
        id=task_id
    ).model_dump()


@celery_app.task(name=config.taskqueue.verdict_action_task)
def verdict_action_task(scan_request_dict: dict, verdict_dict: dict, original_task_id: str = None) -> dict:
    """
    Process a scan verdict and perform actions if the verdict is MALICIOUS with sufficient severity.

    This task processes the verdict, logs it, and calls item_action on the connector if the verdict
    is MALICIOUS and the severity meets the configured threshold.

    Args:
        scan_request_dict: A dictionary containing scan request details (ScanRequestModel).
        verdict_dict: A dictionary containing verdict details (DPAVerdictModel2).
        original_task_id: The task id associated with the scan request that produced this verdict

    Returns:
        dict: A StatusResponse dictionary indicating success or failure.

    Raises:
        None: All exceptions are caught and converted to error responses.
    """
    verdict_task_id = verdict_action_task.request.id if hasattr(verdict_action_task, 'request') else None
    dsx_logging.debug(f"Processing verdict task {verdict_task_id} from origin scan task {original_task_id}")
    # 1. Validate and parse scan request and verdict
    try:
        scan_request = ScanRequestModel(**scan_request_dict)
        verdict = DPAVerdictModel2(**verdict_dict)
        dsx_logging.debug(f"Processing {scan_request} for scan verdict: {verdict}")
    except ValidationError as e:
        dsx_logging.error(f"Failed to validate scan request or verdict: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Invalid scan request or verdict data",
            description=str(e),
            id=verdict_task_id
        ).model_dump()

    # 2. Call item_action if verdict is MALICIOUS and severity meets threshold
    if verdict.verdict == DPAVerdictEnum.MALICIOUS:
            # and
            # verdict.verdict_details.severity and
            # verdict.severity >= SecurityConfig().action_severity_threshold):
        # dpx_logging.info(f"Verdict is MALICIOUS with severity {verdict.severity} >= threshold {SecurityConfig().action_severity_threshold}, calling item_action")
        dsx_logging.info(f"Verdict is MALICIOUS, calling item_action")
        try:
            client = get_connector_client(scan_request.connector_url)
            response = client.post(
                f'{scan_request.connector_url}{ConnectorEndpoints.ITEM_ACTION}',
                json=scan_request.model_dump()
            )
            response.raise_for_status()
            dsx_logging.info(f"Item action triggered successfully for {scan_request.location}")
        except httpx.HTTPError as e:
            dsx_logging.error(f"Item action failed for {scan_request.location}: {e}", exc_info=True)
            # Continue processing even if item_action fails
        except Exception as e:
            dsx_logging.error(f"Unexpected error during item_action for {scan_request.location}: {e}", exc_info=True)
            # Continue processing even if item_action fails

    # 3. Return success response
    dsx_logging.info(f"Verdict processed for {scan_request.location}")
    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Verdict processed for {scan_request.location}",
        description=f"{scan_request}; verdict {verdict}",
        id=verdict_task_id
    ).model_dump()


@celery_app.task(name=config.taskqueue.scan_result_task)
def scan_result_task(scan_request_dict: dict, verdict_dict: dict, original_task_id: str = None) -> dict:
    """
    Processes scan results for persistence, statistics and logging.

    This task consumes scan results from the scan_result_queue, constructs a ScanResultModel,
    and persists it in the configured database, computes statistics on scans and outputs syslog if configured.

    Args:
        scan_request_dict: A dictionary containing scan request details (ScanRequestModel).
        verdict_dict: A dictionary containing verdict details (DPAVerdictModel2).
        original_task_id: The task ID of the originating scan_request_task (optional).

    Returns:
        dict: A StatusResponse dictionary indicating success or failure.

    Raises:
        ValidationError: If scan request or verdict data is invalid.
        Other exceptions may propagate if database or syslog operations fail.
    """
    task_id = scan_result_task.request.id if hasattr(scan_result_task, 'request') else original_task_id

    try:
        scan_request: ScanRequestModel = ScanRequestModel(**scan_request_dict)
        dpa_verdict = DPAVerdictModel2(**verdict_dict)
        dsx_logging.debug(f"Processing scan result for {scan_request.location} (task_id: {task_id}) (original task id: {original_task_id} ")
    except ValidationError as e:
        dsx_logging.error(f"Failed to validate scan result: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Invalid scan result data",
            description=str(e),
            id=task_id
        ).model_dump()

    try:
        # Construct and store scan result in database
        scan_result = ScanResultModel(
            scan_request_task_id=original_task_id,
            metadata_tag=scan_request.metainfo,
            status=ScanResultStatusEnum.SCANNED,
            dpa_verdict=dpa_verdict.model_dump()
        )
        _scan_results_db.insert(scan_result)
        dsx_logging.info(f"Stored scan result for {scan_request.location} in database")

        _scan_stats_worker.insert(scan_result)
        dsx_logging.info(f"Stored scan stats for {scan_request.location} in database")

    except Exception as e:
        dsx_logging.error(f"Failed to store scan result: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Failed to store scan result",
            description=f"str(e) for task_id= {task_id}",
        ).model_dump()

    from dsx_connect.utils.log_chain import log_verdict_chain

    # Send to syslog
    log_verdict_chain(
        scan_request=scan_request,
        verdict=dpa_verdict,
        item_action_success=True,  # need to actually base this on success or failure
        original_task_id=original_task_id,
        current_task_id=task_id
    )

    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Scan result stored for {scan_request.location}",
        description=f"Scan result: {scan_result} for task_id= {task_id}"
    ).model_dump()
