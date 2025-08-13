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
    celery_app -A dsx_connect.celery_app.celery_app worker --loglevel=info -Q scan_request_queue,verdict_action_queue,scan_result_queue
    ```
    Or run standalone for debugging:
    ```bash
    python taskworkers/taskworkers.py
    ```

Dependencies:
    - httpx: For synchronous HTTP requests.
    - celery_app: For task queue management.
    - dsx_connect: Internal models, config, and client utilities.
"""
import io
import json
import time
import unicodedata
from io import BytesIO
from typing import Dict, Optional

import httpx
import pyzipper
import redis
from celery.signals import worker_process_init
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from dsx_connect.database.scan_stats_worker import ScanStatsWorker
from dsx_connect.dsxa_client.verdict_models import DPAVerdictEnum, DPAVerdictModel2
from dsx_connect.common.endpoint_names import ConnectorEndpoints
from dsx_connect.database.scan_results_base_db import ScanResultsBaseDB
from dsx_connect.database.scan_stats_base_db import ScanStatsBaseDB
from dsx_connect.dsxa_client.dsxa_client import DSXAClient, DSXAScanRequest, DSXAConnectionError, DSXATimeoutError, \
    DSXAServiceError, DSXAClientError
from dsx_connect.models.connector_models import ScanRequestModel, ItemActionEnum
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum, ItemActionStatusResponse
from dsx_connect.models.scan_models import ScanResultModel, ScanResultStatusEnum, ScanStatsModel
from dsx_connect.connector_utils.connector_client import get_connector_client
from dsx_connect.models.dead_letter import DeadLetterItem
from dsx_connect.celery_app.celery_app import celery_app
from dsx_connect.config import DatabaseConfig, ConfigDatabaseType
from dsx_connect.utils.app_logging import dsx_logging
from dsx_connect.config import ConfigManager
from dsx_connect.utils.redis_manager import redis_manager, RedisQueueNames

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


# HELPER FUNCTIONS - Define these OUTSIDE the task function
def _handle_retryable_error(task_instance, scan_request, error, task_id, retry_count, max_retries, failure_reason,
                            backoff_base=60):
    """Handle errors that should be retried with configurable backoff, then sent to DLQ"""
    if retry_count < max_retries:
        # Use configurable backoff_base instead of hardcoded values
        retry_delay = backoff_base * (3 ** retry_count)  # exponential backoff with configurable base

        next_retry = retry_count + 1
        total_retries = max_retries

        dsx_logging.info(f"Scheduling retry due to '{failure_reason}' for {scan_request.location} in {retry_delay}s "
                         f"(retry {next_retry}/{total_retries})")

        # Raise retry to let Celery handle it - use the task_instance
        raise task_instance.retry(countdown=retry_delay, exc=error)
    else:
        # Max retries exceeded - send to DLQ
        return _send_to_dlq_final_error(scan_request, error, task_id, retry_count, failure_reason)


def _send_to_dlq_final_error(scan_request, error, task_id, retry_count, failure_reason):
    """Send item to dead letter queue for final errors or after max retries"""
    dsx_logging.error(f"Max retries exceeded for '{failure_reason}' attempting to scan: {scan_request.location} after {retry_count + 1} attempts. "
                      f"Moving to dead letter queue.")

    dead_letter_item = DeadLetterItem(
        scan_request=scan_request,
        failure_reason=failure_reason,
        error_details=str(error),
        failed_at=time.time(),
        original_task_id=task_id,
        retry_count=retry_count + 1
    )

    try:
        success = redis_manager.add_to_dead_letter_queue(
            queue_name=RedisQueueNames.DLQ_SCAN_FILE,
            item_data=dead_letter_item.model_dump_json(),
            ttl_days=config.taskqueue.dlq_expire_after_days
        )
        dsx_logging.info(f"Added {scan_request.location} to dead letter queue: {failure_reason}")
    except Exception as redis_err:
        dsx_logging.error(f"Failed to add to dead letter queue: {redis_err}")

    return StatusResponseEnum.ERROR


@celery_app.task(name=config.taskqueue.scan_request_task, bind=True,
                 max_retries=config.taskqueue.scan_request_max_retries)
def scan_request_task(self, scan_request_dict: dict) -> str:
    """
    Process a scan request by fetching file content and scanning it for malware.
    Implements configurable retry/DLQ strategy for both connector and DSXA errors.
    """
    task_id = self.request.id if hasattr(self, 'request') else None
    retry_count = self.request.retries if hasattr(self, 'request') else 0
    max_retries = config.taskqueue.scan_request_max_retries

    if retry_count == 0:
        dsx_logging.warning(f"scan_request_task {task_id}: Initial attempt (max retries: {max_retries})")
    else:
        dsx_logging.warning(f"scan_request_task {task_id}: Retry {retry_count}/{max_retries}")


    # 1. Validate and parse scan request
    try:
        scan_request = ScanRequestModel(**scan_request_dict)
        dsx_logging.debug(f"Processing scan request for {scan_request.location} with {scan_request.connector_url}")
    except ValidationError as e:
        dsx_logging.error(f"Failed to validate scan request: {e}", exc_info=True)
        return StatusResponseEnum.ERROR

    # 2. Read file content from connector
    try:
        client = get_connector_client(scan_request.connector_url)
        response = client.post(
            f'{scan_request.connector_url}{ConnectorEndpoints.READ_FILE}',
            json=jsonable_encoder(scan_request))
        response.raise_for_status()
        bytes_content = BytesIO(response.content)
        bytes_content.seek(0)
        dsx_logging.debug(f"Received {bytes_content.getbuffer().nbytes} bytes")
    except httpx.ConnectError as e:
        # Handle connector connection errors
        if "Name does not resolve" in str(e) or "Connection refused" in str(e):
            dsx_logging.warning(f"Connector {scan_request.connector_url} unavailable: {e}")

            # Check config setting - use getattr with default for backwards compatibility
            if getattr(config.taskqueue, 'retry_connector_connection_errors', True):
                backoff_base = getattr(config.taskqueue, 'connector_retry_backoff_base', 60)
                return _handle_retryable_error(
                    self, scan_request, e, task_id, retry_count, max_retries,
                    failure_reason="connector unavailable",
                    backoff_base=backoff_base
                )
            else:
                return _send_to_dlq_final_error(
                    scan_request, e, task_id, retry_count,
                    failure_reason="connector unavailable (no retry configured)"
                )
        else:
            dsx_logging.error(f"Non-retryable connector error: {e}")
            return _send_to_dlq_final_error(
                scan_request, e, task_id, retry_count,
                failure_reason="connector error"
            )
    except httpx.HTTPStatusError as e:
        # Handle HTTP status errors
        if e.response.status_code in [502, 503, 504]:
            # Check config setting for server errors
            if getattr(config.taskqueue, 'retry_connector_server_errors', True) and retry_count < max_retries:
                backoff_base = getattr(config.taskqueue, 'server_error_retry_backoff_base', 30)
                retry_delay = backoff_base * (2 ** retry_count)
                dsx_logging.warning(f"Server error {e.response.status_code}, retrying in {retry_delay}s")
                raise self.retry(countdown=retry_delay, exc=e)
            else:
                return _send_to_dlq_final_error(
                    scan_request, e, task_id, retry_count,
                    failure_reason=f"connector HTTP {e.response.status_code} error"
                )
        elif 400 <= e.response.status_code < 500:
            # Client errors
            if getattr(config.taskqueue, 'retry_connector_client_errors', False):
                backoff_base = getattr(config.taskqueue, 'connector_retry_backoff_base', 60)
                return _handle_retryable_error(
                    self, scan_request, e, task_id, retry_count, max_retries,
                    failure_reason=f"connector HTTP {e.response.status_code} error",
                    backoff_base=backoff_base
                )
            else:
                return _send_to_dlq_final_error(
                    scan_request, e, task_id, retry_count,
                    failure_reason=f"connector HTTP {e.response.status_code} error (client error - no retry)"
                )

        dsx_logging.error(f"HTTP {e.response.status_code} error from connector: {e}")
        return _send_to_dlq_final_error(
            scan_request, e, task_id, retry_count,
            failure_reason=f"connector HTTP {e.response.status_code} error"
        )
    except Exception as e:
        dsx_logging.error(f"Unexpected error while fetching file: {e}", exc_info=True)
        return _send_to_dlq_final_error(
            scan_request, e, task_id, retry_count,
            failure_reason="unexpected connector error"
        )

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
        dsx_logging.debug(f"Verdict: {dpa_verdict}")
        reason = getattr(dpa_verdict.verdict_details, "reason", "") or ""

        if dpa_verdict.verdict == DPAVerdictEnum.NOT_SCANNED and "initializing" in reason:
            dsx_logging.warning(f"DSXA scanner initializing — will retry for {scan_request.location}")
            return _handle_retryable_error(
                self, scan_request,
                Exception("DSXA initializing"),  # or a custom exception
                task_id, retry_count, max_retries,
                failure_reason="dsxa initializing",
                backoff_base=getattr(config.taskqueue, "dsxa_retry_backoff_base", 60),
            )

    except DSXAConnectionError as e:
        # DSXA connection issues
        dsx_logging.warning(f"DSXA scanner unavailable for {scan_request.location}: {e}")

        if getattr(config.taskqueue, 'retry_dsxa_connection_errors', True):
            backoff_base = getattr(config.taskqueue, 'dsxa_retry_backoff_base', 60)
            return _handle_retryable_error(
                self, scan_request, e, task_id, retry_count, max_retries,
                failure_reason="dsxa scanner unavailable",
                backoff_base=backoff_base
            )
        else:
            return _send_to_dlq_final_error(
                scan_request, e, task_id, retry_count,
                failure_reason="dsxa scanner unavailable (no retry configured)"
            )

    except DSXATimeoutError as e:
        # Timeout errors
        dsx_logging.warning(f"DSXA timeout for {scan_request.location}: {e}")

        if getattr(config.taskqueue, 'retry_dsxa_timeout_errors', True):
            backoff_base = getattr(config.taskqueue, 'dsxa_retry_backoff_base', 60)
            return _handle_retryable_error(
                self, scan_request, e, task_id, retry_count, max_retries,
                failure_reason="dsxa scanner timeout",
                backoff_base=backoff_base
            )
        else:
            return _send_to_dlq_final_error(
                scan_request, e, task_id, retry_count,
                failure_reason="dsxa scanner timeout (no retry configured)"
            )

    except DSXAServiceError as e:
        # Service errors - classify and handle based on config
        error_str = str(e)

        # Server errors and rate limiting
        if any(code in error_str for code in ["HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"]):
            if getattr(config.taskqueue, 'retry_dsxa_server_errors', True):
                dsx_logging.warning(f"Retryable DSXA service error for {scan_request.location}: {e}")
                backoff_base = getattr(config.taskqueue, 'dsxa_retry_backoff_base', 60)
                return _handle_retryable_error(
                    self, scan_request, e, task_id, retry_count, max_retries,
                    failure_reason="dsxa service error (retryable)",
                    backoff_base=backoff_base
                )
            else:
                return _send_to_dlq_final_error(
                    scan_request, e, task_id, retry_count,
                    failure_reason="dsxa service error (retryable - no retry configured)"
                )

        # Client errors (4xx)
        else:
            if getattr(config.taskqueue, 'retry_dsxa_client_errors', False):
                dsx_logging.warning(f"DSXA client error (configured to retry) for {scan_request.location}: {e}")
                backoff_base = getattr(config.taskqueue, 'dsxa_retry_backoff_base', 60)
                return _handle_retryable_error(
                    self, scan_request, e, task_id, retry_count, max_retries,
                    failure_reason="dsxa service error (client)",
                    backoff_base=backoff_base
                )
            else:
                dsx_logging.error(f"Non-retryable DSXA service error for {scan_request.location}: {e}")
                return _send_to_dlq_final_error(
                    scan_request, e, task_id, retry_count,
                    failure_reason="dsxa service error (permanent)"
                )

    except DSXAClientError as e:
        # Client-side errors
        if getattr(config.taskqueue, 'retry_dsxa_client_errors', False):
            dsx_logging.warning(f"DSXA client error (configured to retry) for {scan_request.location}: {e}")
            backoff_base = getattr(config.taskqueue, 'dsxa_retry_backoff_base', 60)
            return _handle_retryable_error(
                self, scan_request, e, task_id, retry_count, max_retries,
                failure_reason="dsxa client error",
                backoff_base=backoff_base
            )
        else:
            dsx_logging.error(f"DSXA client error for {scan_request.location}: {e}")
            return _send_to_dlq_final_error(
                scan_request, e, task_id, retry_count,
                failure_reason="dsxa client error"
            )

    except Exception as e:
        # Fallback for any other unexpected DSXA errors
        dsx_logging.error(f"Unexpected DSXA scan error for {scan_request.location}: {e}", exc_info=True)
        return _send_to_dlq_final_error(
            scan_request, e, task_id, retry_count,
            failure_reason="unexpected dsxa error"
        )

    # 4. Send verdict to verdict queue
    try:
        task1 = celery_app.send_task(
            config.taskqueue.verdict_action_task,
            queue=config.taskqueue.verdict_action_queue,
            args=[scan_request_dict, dpa_verdict.model_dump(), task_id]
        )
        dsx_logging.debug(
            f"Sent verdict for {scan_request.location} to {config.taskqueue.verdict_action_queue} with task_id {task1.id}")

    except Exception as e:
        dsx_logging.error(f"Queue dispatch failed: {e}", exc_info=True)

        # Check config setting for queue dispatch errors
        if getattr(config.taskqueue, 'retry_queue_dispatch_errors', False):
            backoff_base = getattr(config.taskqueue, 'dsxa_retry_backoff_base', 60)
            return _handle_retryable_error(
                self, scan_request, e, task_id, retry_count, max_retries,
                failure_reason="queue dispatch error",
                backoff_base=backoff_base
            )
        else:
            return _send_to_dlq_final_error(
                scan_request, e, task_id, retry_count,
                failure_reason="queue dispatch error"
            )

    # Success response
    dsx_logging.info(f"Successful scan_request_task {
    StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Scan request task completed",
        description=f"{scan_request}",
        id=task_id
    ).model_dump()}")
    return StatusResponseEnum.SUCCESS


@celery_app.task(name=config.taskqueue.verdict_action_task)
def verdict_action_task(scan_request_dict: dict, verdict_dict: dict, original_task_id: str = None) -> str:
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
        return StatusResponseEnum.ERROR

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
    #         config.celery_app.encrypted_file_task,
    #         queue=config.celery_app.encrypted_file_queue,
    #         args=[scan_request_dict, original_task_id]
    #     )
    #     dsx_logging.debug(
    #         f"Sent scan request {scan_request.metainfo} to {config.celery_app.encrypted_file_queue} with task_id {task2b.id}")

    # 3. Send to scan_result_queue for post scan result processing
    task2 = celery_app.send_task(
        config.taskqueue.scan_result_task,
        queue=config.taskqueue.scan_result_queue,
        args=[scan_request_dict, verdict.model_dump(), item_action_response.model_dump(), original_task_id]
    )
    dsx_logging.debug(
        f"Sent scan result for {scan_request.location} to {config.taskqueue.scan_result_queue} with task_id {task2.id}")

    # 3. Return success response
    dsx_logging.info(f"Successful {config.taskqueue.verdict_action_task} processed for {StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Verdict processed for {scan_request.location}",
        description=f"{scan_request}; verdict {verdict}",
        id=verdict_task_id
    ).model_dump()}")
    return StatusResponseEnum.SUCCESS


@celery_app.task(name=config.taskqueue.scan_result_task)
def scan_result_task(scan_request_dict: dict, verdict_dict: dict, item_action_dict: dict,
                     original_task_id: str = None) -> str:
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
        dsx_logging.error(f"Failed to validate scan result in scan result task: {e}", exc_info=True)

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
    try:
        task = celery_app.send_task(
            name=config.taskqueue.scan_result_notification_task,
            queue=config.taskqueue.scan_result_notification_queue,
            args=[scan_result.model_dump()]
        )
        dsx_logging.debug("[SSE Notification] Scan result notification queued successfully.")
    except Exception as e:
        dsx_logging.error(f"Failed to queue scan result notification: {e}", exc_info=True)

    # try:
    #     celery_app.send_task(
    #         name=config.celery_app.scan_result_notification_task,
    #         queue=config.celery_app.scan_result_notification_queue,
    #         args=[scan_result.model_dump()]
    #     )
    #     dsx_logging.debug("[SSE Notification] Scan result notification queued successfully.")
    # except Exception as e:
    #     dsx_logging.error(f"[SSE Notification] Failed to queue scan result: {e}", exc_info=True)
    #
    # 4. All done, return success to... somewhere...
    dsx_logging.info(f"Successful {config.taskqueue.scan_result_task} processed for {StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=f"Scan result stored for {scan_request.location}",
        description=f"Scan result: {scan_result} for task_id= {task_id}"
    ).model_dump()}")
    return StatusResponseEnum.SUCCESS


# @celery_app.task(name=config.celery_app.scan_result_notification_task)
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

# @celery_app.task(name=config.celery_app.data_classification_task)
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
