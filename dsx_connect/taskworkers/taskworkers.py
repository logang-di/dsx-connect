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
import io
import json
import unicodedata
from io import BytesIO
from typing import Dict, Optional

import httpx
import pyzipper
from celery.signals import worker_process_init
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from dsx_connect.database.scan_stats_worker import ScanStatsWorker
from dsx_connect.dsxa_client.verdict_models import DPAVerdictEnum, DPAVerdictModel2
from dsx_connect.models.constants import ConnectorEndpoints
from dsx_connect.database.scan_results_base_db import ScanResultsBaseDB
from dsx_connect.database.scan_stats_base_db import ScanStatsBaseDB
from dsx_connect.dsxa_client.dsxa_client import DSXAClient, DSXAScanRequest
from dsx_connect.models.connector_models import ScanRequestModel, ItemActionEnum
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum, ItemActionStatusResponse
from dsx_connect.models.scan_models import ScanResultModel, ScanResultStatusEnum, ScanStatsModel
from dsx_connect.connector_utils.connector_client import get_connector_client
from dsx_connect.taskqueue.celery_app import celery_app
from dsx_connect.config import DatabaseConfig, ConfigDatabaseType
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.config import ConfigManager
from dsx_connect.utils.redis_manager import redis_manager

# Shared client pools and scan client per worker process
# _connector_clients: Dict[str, httpx.Client] = {}
# Lock for thread-safe access to the client pool
# _client_pool_lock = threading.Lock()
_redis_client = None
_scan_results_db: Optional[ScanResultsBaseDB] = None  # Assuming initialized via database_scan_results_factory
_scan_stats_db: Optional[ScanStatsBaseDB] = None  # Assuming initialized via database_scan_stats_factory
_scan_stats_worker: Optional[ScanStatsWorker] = None  # Assuming initialized via passing _scan_stats_db

config = ConfigManager.get_config()


@worker_process_init.connect
def init_worker(**kwargs):
    """Initialize shared httpx.Client for scan requests and empty connector client pool."""
    global config
    config = ConfigManager.reload_config()
    global _connector_clients
    global _scan_results_db
    global _scan_stats_db
    global _scan_stats_worker
    _connector_clients = {}
    dsx_logging.debug("Initialized shared httpx.Client for scan requests and empty connector pool")

    from dsx_connect.database.database_factory import database_scan_results_factory
    _scan_results_db = database_scan_results_factory(
        database_type=config.database.type,
        database_loc=config.database.loc,
        retain=config.database.retain,
        collection_name="scan_results"
    )
    dsx_logging.info(f"Initialized scan results database of type {config.database.type} at {config.database.loc}")

    from dsx_connect.database.database_factory import database_scan_stats_factory
    from dsx_connect.database.scan_stats_worker import ScanStatsWorker
    _scan_stats_db = database_scan_stats_factory(
        database_type=ConfigDatabaseType.TINYDB,
        database_loc=config.database.scan_stats_db,
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
    init_syslog_handler(syslog_host=config.scan_result_task_worker.syslog_server_url,
                        syslog_port=config.scan_result_task_worker.syslog_server_port)


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
            json=jsonable_encoder(scan_request))
        response.raise_for_status()
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
        # Send to verdict_action_queue for action-taking
        task1 = celery_app.send_task(
            config.taskqueue.verdict_action_task,
            queue=config.taskqueue.verdict_action_queue,
            args=[scan_request_dict, dpa_verdict.model_dump(), task_id]
        )
        dsx_logging.debug(
            f"Sent verdict for {scan_request.location} to {config.taskqueue.verdict_action_queue} with task_id {task1.id}")

    except Exception as e:
        dsx_logging.error(f"Scan or queue dispatch failed: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message=f"Failed to send scan result to queue {config.taskqueue.verdict_action_queue}",
            description=str(e),
            id=task_id
        ).model_dump()

    # 4a. Send scan_request to data_classification tasks for certain file types
    # TODO - I really only want to do this if data classification is enabled... otherwise it's just sending a task into the queue that never gets handled
    # try:
    #     task3 = celery_app.send_task(
    #         config.taskqueue.data_classification_task,
    #         queue=config.taskqueue.scan_result_queue,
    #         args=[scan_request_dict, dpa_verdict.file_info.file_type, task_id]
    #     )
    #     dsx_logging.debug(
    #         f"Sent scan request for {scan_request.location} to {config.taskqueue.data_classification_queue} with task_id {task3.id}")
    # except Exception as e:
    #     dsx_logging.error(f"Scan or queue dispatch failed: {e}", exc_info=True)
    #     return StatusResponse(
    #         status=StatusResponseEnum.ERROR,
    #         message=f"Failed to send scan result to queue {config.taskqueue.data_classification_queue}",
    #         description=str(e),
    #         id=task_id
    #     ).model_dump()

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

    This task processes the verdict, logs it, and calls item_action on the connector if the verdict is MALICIOUS and the severity meets the configured threshold.

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
        scan_request = ScanRequestModel.model_validate(scan_request_dict)
        verdict = DPAVerdictModel2.model_validate(verdict_dict)
        dsx_logging.debug(f"Processing {scan_request} for scan verdict: {verdict}")
    except ValidationError as e:
        dsx_logging.error(f"Failed to validate scan request or verdict: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Invalid scan request or verdict data",
            description=str(e),
            id=verdict_task_id
        ).model_dump()

    # 2. Determine what to do based on the verdict
    # Prepare a default “no‐action” response in case neither MALICIOUS nor Encrypted matches:
    item_action_response = ItemActionStatusResponse(
        status=StatusResponseEnum.NOTHING,
        item_action=ItemActionEnum.NOTHING,
        message="No action taken",
    )

    if verdict.verdict == DPAVerdictEnum.MALICIOUS:
        # 2a. Call item_action if verdict is MALICIOUS and perhaps in some future - where the severity meets a threshold
        dsx_logging.info(f"Verdict is MALICIOUS, calling item_action")
        try:
            client = get_connector_client(scan_request.connector_url)
            response = client.put(
                f'{scan_request.connector_url}{ConnectorEndpoints.ITEM_ACTION}',
                json=jsonable_encoder(scan_request)
            )
            response.raise_for_status()
            try:
                item_action_response = ItemActionStatusResponse.model_validate(response.json())
            except ValidationError as e:
                dsx_logging.error(f"ItemActionStatusResponse validation failed: {e}", exc_info=True)
                # Fallback to an “error” response
                item_action_response = ItemActionStatusResponse(
                    status=StatusResponseEnum.ERROR,
                    item_action=ItemActionEnum.NOT_IMPLEMENTED,
                    message="Invalid response from item_action endpoint",
                    description=str(e),
                )
            dsx_logging.info(f"Item action triggered successfully for {scan_request.location}")
        except httpx.HTTPError as e:
            dsx_logging.error(f"Item action HTTP error for {scan_request.location}: {e}", exc_info=True)
            item_action_response = ItemActionStatusResponse(
                status=StatusResponseEnum.ERROR,
                item_action=ItemActionEnum.NOTHING,
                message="HTTP error calling item_action",
                description=str(e),
            )
        except Exception as e:
            dsx_logging.error(f"Unexpected error during item_action for {scan_request.location}: {e}", exc_info=True)
            item_action_response = ItemActionStatusResponse(
                status=StatusResponseEnum.ERROR,
                item_action=ItemActionEnum.NOTHING,
                message="Unexpected error during item_action",
                description=str(e),
            )
    # elif "Encrypted" in verdict.verdict_details.reason:
    #     # 2b. Send a scan request to the
    #     task2b = celery_app.send_task(
    #         config.taskqueue.encrypted_file_task,
    #         queue=config.taskqueue.encrypted_file_queue,
    #         args=[scan_request_dict, original_task_id]
    #     )
    #     dsx_logging.debug(
    #         f"Sent scan request {scan_request.metainfo} to {config.taskqueue.encrypted_file_queue} with task_id {task2b.id}")

    # 3. Send to scan_result_queue for post scan result processing
    task2 = celery_app.send_task(
        config.taskqueue.scan_result_task,
        queue=config.taskqueue.scan_result_queue,
        args=[scan_request_dict, verdict.model_dump(), item_action_response.model_dump(), original_task_id]
    )
    dsx_logging.debug(
        f"Sent scan result for {scan_request.location} to {config.taskqueue.scan_result_queue} with task_id {task2.id}")

    # 3. Return success response
    dsx_logging.info(f"Verdict processed for {scan_request.location}")
    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Verdict processed for {scan_request.location}",
        description=f"{scan_request}; verdict {verdict}",
        id=verdict_task_id
    ).model_dump()


@celery_app.task(name=config.taskqueue.scan_result_task)
def scan_result_task(scan_request_dict: dict, verdict_dict: dict, item_action_dict: dict,
                     original_task_id: str = None) -> dict:
    """
    Processes scan results for persistence, statistics, reporting and logging.

    This task consumes scan results from the scan_result_queue, constructs a ScanResultModel,
    and persists it in the configured database, computes statistics on scans and stores in a database,
    outputs syslog, and reports to web frontend, if configured.

    This task can be left turned off and all scanning will work as needed, there will just not be any additional
    reporting.  In the future, may have flags to toggle reporting components on/off, or have separate task workers
    for each type of reporting.

    Args:
        scan_request_dict: A dictionary containing scan request details (ScanRequestModel).
        verdict_dict: A dictionary containing verdict details (DPAVerdictModel2).
        item_action_dict: A dictionary containing item action details
        original_task_id: The task ID of the originating scan_request_task (optional).

    Returns:
        dict: A StatusResponse dictionary indicating success or failure.

    Raises:
        ValidationError: If scan request or verdict data is invalid.
        Other exceptions may propagate if database or syslog operations fail.
    """
    task_id = scan_result_task.request.id if hasattr(scan_result_task, 'request') else original_task_id

    # 0. Unparse all the models passed here as dicts and construct a scan result
    try:
        scan_request: ScanRequestModel = ScanRequestModel.model_validate(scan_request_dict)
        item_action_status = ItemActionStatusResponse.model_validate(item_action_dict)
        dpa_verdict = DPAVerdictModel2.model_validate(verdict_dict)

        scan_result = ScanResultModel(
            scan_request_task_id=original_task_id,
            metadata_tag=scan_request.metainfo,
            scan_request=scan_request,
            status=ScanResultStatusEnum.SCANNED,
            item_action=item_action_status,
            verdict=dpa_verdict
        )

        dsx_logging.debug(
            f"Processing scan result for {scan_request.location} (task_id: {task_id}) (original task id: {original_task_id} ")
    except ValidationError as e:
        dsx_logging.error(f"Failed to validate scan result: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Invalid scan result data",
            description=str(e),
            id=task_id
        ).model_dump()

    # 1a. Store scan result in database
    try:
        _scan_results_db.insert(scan_result)
        dsx_logging.info(f"Stored scan result for {scan_request.location} in database")

    except Exception as e:
        # Failure to save scan results should log an error but should not stop processing scan results
        dsx_logging.error(f"Failed to store scan result: {e}", exc_info=True)

    # 1b. Store scan result in database
    try:
        _scan_stats_worker.insert(scan_result)
        dsx_logging.info(f"Stored scan stats for {scan_request.location} in database")
    except Exception as e:
        # Failure to save scan results should log an error but should not stop processing scan results
        dsx_logging.error(f"Failed to store scan result: {e}", exc_info=True)

    # 2. Send to syslog
    from dsx_connect.utils.log_chain import log_verdict_chain

    log_verdict_chain(
        scan_result=scan_result,
        original_task_id=original_task_id,
        current_task_id=task_id
    )

    # 3. Queue scan result to notification queue
    # RIGHT BEFORE queuing notification task, add this:
    dsx_logging.info(f"[DEBUG] About to queue notification task")
    dsx_logging.info(f"[DEBUG] Notification task name: {config.taskqueue.scan_result_notification_task}")
    dsx_logging.info(f"[DEBUG] Notification queue name: {config.taskqueue.scan_result_notification_queue}")

    # 3. Queue scan result to notification queue
    try:
        task = celery_app.send_task(
            name=config.taskqueue.scan_result_notification_task,
            queue=config.taskqueue.scan_result_notification_queue,
            args=[scan_result.model_dump()]
        )
        dsx_logging.debug("[SSE Notification] Scan result notification queued successfully.")
    except Exception as e:
        dsx_logging.error(f"[DEBUG] Failed to queue scan result notification: {e}", exc_info=True)

    # try:
    #     celery_app.send_task(
    #         name=config.taskqueue.scan_result_notification_task,
    #         queue=config.taskqueue.scan_result_notification_queue,
    #         args=[scan_result.model_dump()]
    #     )
    #     dsx_logging.debug("[SSE Notification] Scan result notification queued successfully.")
    # except Exception as e:
    #     dsx_logging.error(f"[SSE Notification] Failed to queue scan result: {e}", exc_info=True)
    #
    # 4. All done, return success to... somewhere...
    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Scan result stored for {scan_request.location}",
        description=f"Scan result: {scan_result} for task_id= {task_id}"
    ).model_dump()


# @celery_app.task(name=config.taskqueue.scan_result_notification_task)
# def scan_result_notify_task(scan_result_dict: dict):
#     """
#     Receives scan result dict and forwards it to the FastAPI app via redis pubsub.
#     """
#     try:
#         scan_result = ScanResultModel.model_validate(scan_result_dict)
#         r = redis.Redis.from_url(config.redis_url)
#         r.publish("scan_results", json.dumps(jsonable_encoder(scan_result)))
#         dsx_logging.debug(f"[SSE Notify] Published scan result {scan_result} to Redis channel.")
#     except Exception as e:
#         dsx_logging.error(f"[SSE Notify] Failed to publish to Redis: {e}", exc_info=True)


@celery_app.task(name=config.taskqueue.scan_result_notification_task)
def scan_result_notify_task(scan_result_dict: dict):
    try:
        scan_result = ScanResultModel.model_validate(scan_result_dict)
        subscriber_count = redis_manager.publish_scan_result(jsonable_encoder(scan_result))
        dsx_logging.info(f"Published to scan result {scan_result} to {subscriber_count} subscribers")
    except Exception as e:
        dsx_logging.error(f"Failed to publish: {e}")


@celery_app.task(name=config.taskqueue.encrypted_file_task)
def encrypted_file_task(scan_request_dict: dict, original_task_id: str = None) -> dict:
    scan_request = ScanRequestModel.model_validate(scan_request_dict)
    task_id = encrypted_file_task.request.id if hasattr(encrypted_file_task, 'request') else original_task_id
    dsx_logging.debug(f"Encrypted file task called for {scan_request.metainfo}")

    # 1. Get the file and attempt decryption
    try:
        client = get_connector_client(scan_request.connector_url)
        response = client.post(
            f'{scan_request.connector_url}{ConnectorEndpoints.READ_FILE}',
            json=jsonable_encoder(scan_request)
        )
        response.raise_for_status()  # Raises HTTPError for 4xx/5xx responses
        bytes_content = BytesIO(response.content)
        bytes_content.seek(0)

        # Tell pyzipper to read from our BytesIO buffer, in “read” mode,
        # specifying AES encryption
        with pyzipper.AESZipFile(
                bytes_content,
                mode='r',
                compression=pyzipper.ZIP_LZMA,  # or ZIP_DEFLATED, etc.
                encryption=pyzipper.WZ_AES  # ensure AES is used
        ) as zipped:
            for zipinfo in zipped.infolist():
                # Each zipped.open() also needs the password
                with zipped.open(zipinfo, pwd=b'infected') as f:
                    decrypted_content = f.read()
                    # Now `decrypted_content` is the raw bytes of that file inside the zip
                    break  # (or process each file as needed)

        dsx_logging.debug(f"Successful decryption of {scan_request.metainfo}")
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

    # 2. Scan the file with DSXAClient
    dsxa_client = DSXAClient(scan_binary_url=config.scanner.scan_binary_url)
    try:
        safe_meta = unicodedata.normalize("NFKD", scan_request.metainfo).encode("ascii", "ignore").decode("ascii")
        metadata_info = f"file-tag:{safe_meta}"
        if task_id:
            metadata_info += f",task-id:{task_id}"
        dpa_verdict = dsxa_client.scan_binary(
            scan_request=DSXAScanRequest(
                binary_data=io.BytesIO(decrypted_content),
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

    # 3. Send verdict to verdict queue with original task_id
    try:
        # Send to verdict_action_queue for action-taking
        task1 = celery_app.send_task(
            config.taskqueue.verdict_action_task,
            queue=config.taskqueue.verdict_action_queue,
            args=[scan_request_dict, dpa_verdict.model_dump(), task_id]
        )
        dsx_logging.debug(
            f"Sent verdict for {scan_request.location} to {config.taskqueue.verdict_action_queue} with task_id {task1.id}")

    except Exception as e:
        dsx_logging.error(f"Scan or queue dispatch failed: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message=f"Failed to send scan result to queue {config.taskqueue.verdict_action_queue}",
            description=str(e),
            id=task_id
        ).model_dump()

# @celery_app.task(name=config.taskqueue.data_classification_task)
# def data_classification_task(scan_request_dict: dict, file_type: str, original_task_id: str = None) -> dict:
#     scan_request = ScanRequestModel.model_validate(scan_request_dict)
#     task_id = data_classification_task.request.id if hasattr(data_classification_task, 'request') else original_task_id
#
#     if "PDF" in file_type:
#         dsx_logging.debug(f"Data classification started for {scan_request.location}")
#         try:
#             client = get_connector_client(scan_request.connector_url)
#             response = client.post(
#                 f'{scan_request.connector_url}{ConnectorEndpoints.READ_FILE}',
#                 json=jsonable_encoder(scan_request)
#             )
#             response.raise_for_status()  # Raises HTTPError for 4xx/5xx responses
#             bytes_content = BytesIO(response.content)
#             bytes_content.seek(0)
#
#             import re
#             from PyPDF2 import PdfReader
#
#             # Read the PDF bytes into PyPDF2
#             reader = PdfReader(bytes_content)
#
#             # Extract text from every page
#             full_text = ""
#             for page in reader.pages:
#                 page_text = page.extract_text()
#                 if page_text:
#                     full_text += page_text
#             # Example SSN pattern: 3 digits-2 digits-4 digits
#             ssn_pattern = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
#
#             matches = ssn_pattern.findall(full_text)
#             if len(matches) > 0:
#                 dsx_logging.info(f"Found SSNs: {matches} in file {scan_request.location}")  # -> ['123-45-6789']
#
#             dsx_logging.debug(f"Received {bytes_content.getbuffer().nbytes} bytes")
#         except httpx.HTTPError as e:
#             dsx_logging.error(f"Failed to fetch file from connector: {e}", exc_info=True)
#             return StatusResponse(
#                 status=StatusResponseEnum.ERROR,
#                 message="Failed to fetch file from connector",
#                 description=f"HTTP error: {str(e)}",
#                 id=task_id
#             ).model_dump()
#         except Exception as e:
#             dsx_logging.error(f"Unexpected error while fetching file: {e}", exc_info=True)
#             return StatusResponse(
#                 status=StatusResponseEnum.ERROR,
#                 message="Unexpected error while fetching file",
#                 description=str(e),
#                 id=task_id
#             ).model_dump()
