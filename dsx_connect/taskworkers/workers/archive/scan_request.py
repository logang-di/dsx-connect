# dsx_connect/taskworkers/workers/scan_request.py

from __future__ import annotations
import io
import time
import unicodedata
from typing import Dict, Any

import httpx
from celery import states
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.names import Tasks, Queues
from dsx_connect.taskworkers.errors import (
    ConnectorConnectionError, ConnectorServerError, ConnectorClientError,
    DsxaTimeoutError, DsxaServerError, DsxaClientError,
    MalformedScanRequest
)
from dsx_connect.taskworkers.policy import load_policy, load_policy_variant, get_policy_info
from dsx_connect.taskworkers.dlq_store import DeadLetterItem, dlq_enqueue_sync
from dsx_connect.models.connector_models import ScanRequestModel
from dsx_connect.connectors.client import get_connector_client
from dsx_connect.dsxa_client.dsxa_client import (
    DSXAClient, DSXAScanRequest,
    DSXAConnectionError, DSXATimeoutError, DSXAServiceError, DSXAClientError
)
from shared.dsx_logging import dsx_logging
from shared.routes import ConnectorAPI
from dsx_connect.config import get_config


# ============================================================================
# Core Business Logic (stays in worker, minimal extraction)
# ============================================================================

def read_file_from_connector(scan_request: ScanRequestModel) -> bytes:
    """Read file bytes from connector. Maps exceptions to task-appropriate errors."""
    try:
        with get_connector_client(scan_request.connector_url) as client:
            response = client.post(
                ConnectorAPI.READ_FILE,
                json_body=jsonable_encoder(scan_request),
            )
        response.raise_for_status()
        return response.content

    except httpx.ConnectError as e:
        if "Name does not resolve" in str(e) or "Connection refused" in str(e):
            raise ConnectorConnectionError(f"Connector unavailable: {e}") from e
        raise ConnectorConnectionError(f"Connector connection failed: {e}") from e

    except httpx.HTTPStatusError as e:
        if 500 <= e.response.status_code < 600:
            raise ConnectorServerError(f"Connector server error {e.response.status_code}") from e
        elif 400 <= e.response.status_code < 500:
            raise ConnectorClientError(f"Connector client error {e.response.status_code}") from e
        raise ConnectorConnectionError(f"Connector HTTP error {e.response.status_code}") from e


def scan_with_dsxa(file_bytes: bytes, scan_request: ScanRequestModel, task_id: str = None):
    """Scan file with DSXA. Maps exceptions to task-appropriate errors."""
    config = get_config()
    dsxa_client = DSXAClient(scan_binary_url=config.scanner.scan_binary_url)

    # Prepare metadata
    safe_meta = unicodedata.normalize("NFKD", scan_request.metainfo).encode("ascii", "ignore").decode("ascii")
    metadata_info = f"file-tag:{safe_meta}"
    if task_id:
        metadata_info += f",task-id:{task_id}"

    try:
        dpa_verdict = dsxa_client.scan_binary(
            scan_request=DSXAScanRequest(
                binary_data=io.BytesIO(file_bytes),
                metadata_info=metadata_info
            )
        )

        # Handle special "initializing" case
        reason = getattr(dpa_verdict.verdict_details, "reason", "") or ""
        if dpa_verdict.verdict.value == "Not Scanned" and "initializing" in reason:
            raise DsxaServerError("DSXA scanner is initializing")

        return dpa_verdict

    except DSXAConnectionError as e:
        raise DsxaTimeoutError(f"DSXA connection failed: {e}") from e
    except DSXATimeoutError as e:
        raise DsxaTimeoutError(f"DSXA timeout: {e}") from e
    except DSXAServiceError as e:
        if any(code in str(e) for code in ["HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"]):
            raise DsxaServerError(f"DSXA server error: {e}") from e
        raise DsxaClientError(f"DSXA client error: {e}") from e
    except DSXAClientError as e:
        raise DsxaClientError(f"DSXA client error: {e}") from e


# ============================================================================
# Error Handling Strategy (RabbitMQ-ready)
# ============================================================================

class RetryDecision:
    def __init__(self, should_retry: bool, backoff_seconds: int = 0, reason: str = ""):
        self.should_retry = should_retry
        self.backoff_seconds = backoff_seconds
        self.reason = reason

def decide_retry_strategy(error: Exception, attempt: int, policy) -> RetryDecision:
    """
    Centralized retry decision logic.
    When moving to RabbitMQ, this becomes your retry/DLQ routing logic.
    """

    # Determine error category and policy
    if isinstance(error, ConnectorConnectionError):
        if not policy.retry_connector_connection_errors:
            return RetryDecision(False, reason="connector_connection_no_retry")
        if attempt >= policy.max_retries:
            return RetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.connector_backoff_base)
        return RetryDecision(True, backoff, "connector_connection_retry")

    elif isinstance(error, ConnectorServerError):
        if not policy.retry_connector_server_errors:
            return RetryDecision(False, reason="connector_server_no_retry")
        if attempt >= policy.max_retries:
            return RetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.server_backoff_base)
        return RetryDecision(True, backoff, "connector_server_retry")

    elif isinstance(error, ConnectorClientError):
        if not policy.retry_connector_client_errors:
            return RetryDecision(False, reason="connector_client_no_retry")
        if attempt >= policy.max_retries:
            return RetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.connector_backoff_base)
        return RetryDecision(True, backoff, "connector_client_retry")

    elif isinstance(error, DsxaTimeoutError):
        if not policy.retry_dsxa_timeout_errors:
            return RetryDecision(False, reason="dsxa_timeout_no_retry")
        if attempt >= policy.max_retries:
            return RetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.dsxa_backoff_base)
        return RetryDecision(True, backoff, "dsxa_timeout_retry")

    elif isinstance(error, DsxaServerError):
        if not policy.retry_dsxa_server_errors:
            return RetryDecision(False, reason="dsxa_server_no_retry")
        if attempt >= policy.max_retries:
            return RetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.dsxa_backoff_base)
        return RetryDecision(True, backoff, "dsxa_server_retry")

    elif isinstance(error, DsxaClientError):
        if not policy.retry_dsxa_client_errors:
            return RetryDecision(False, reason="dsxa_client_no_retry")
        if attempt >= policy.max_retries:
            return RetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.dsxa_backoff_base)
        return RetryDecision(True, backoff, "dsxa_client_retry")

    elif isinstance(error, MalformedScanRequest):
        # Never retry validation errors
        return RetryDecision(False, reason="validation_error")

    else:
        # Unknown errors - conservative approach
        return RetryDecision(False, reason="unknown_error")


async def handle_final_failure(scan_request_dict: dict, error: Exception,
                               task_id: str, attempt: int, reason: str):
    """
    Handle final failure after retries exhausted.
    With RabbitMQ, this becomes a simple message to DLQ exchange.
    """
    dsx_logging.error(
        f"Final failure for {scan_request_dict.get('location')} after {attempt} attempts: {reason}"
    )

    # For now, use your existing DLQ store
    dlq_item = DeadLetterItem(
        reason=reason,
        scan_request=scan_request_dict,
        error_details=str(error),
        retry_count=attempt,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request_dict)
    )

    config = get_config()
    dlq_enqueue_sync(dlq_item, ttl_days=getattr(config.workers, 'dlq_expire_after_days', 30))


# ============================================================================
# Main Task (Clean, Focused)
# ============================================================================

@celery_app.task(name=Tasks.REQUEST, bind=True)
def scan_request_task(self, scan_request_dict: dict, policy_override: str = None) -> str:
    """
    Main scan request task. Clean orchestration with separated concerns.

    Args:
        scan_request_dict: Scan request data
        policy_override: Optional policy variant ("high_throughput", "critical_files", etc.)

    RabbitMQ Migration Notes:
    - retry logic -> RabbitMQ TTL + retry exchange
    - DLQ logic -> RabbitMQ DLX (dead letter exchange)
    - backoff -> RabbitMQ TTL per message
    """
    task_id = self.request.id if hasattr(self, 'request') else "unknown"
    retry_count = self.request.retries if hasattr(self, 'request') else 0

    # Load appropriate policy
    if policy_override:
        policy = load_policy_variant(policy_override)
        dsx_logging.info(f"[scan_request:{task_id}] Using policy variant: {policy_override}")
    else:
        policy = load_policy()  # Uses current environment

    # Log policy info on first attempt for debugging
    if retry_count == 0:
        policy_info = get_policy_info(policy)
        dsx_logging.info(f"[scan_request:{task_id}] Policy: {policy_info}")

    location = scan_request_dict.get("location", "")

    dsx_logging.info(
        f"[scan_request:{task_id}] Processing {location} "
        f"(attempt {retry_count + 1}, env={policy.environment})"
    )

    try:
        # 1. Validate input
        try:
            scan_request = ScanRequestModel.model_validate(scan_request_dict)
        except ValidationError as e:
            raise MalformedScanRequest(f"Invalid scan request: {e}") from e

        # 2. Read file from connector
        file_bytes = read_file_from_connector(scan_request)
        dsx_logging.debug(f"[scan_request:{task_id}] Read {len(file_bytes)} bytes")

        # 3. Scan with DSXA
        dpa_verdict = scan_with_dsxa(file_bytes, scan_request, task_id)
        dsx_logging.debug(f"[scan_request:{task_id}] Verdict: {dpa_verdict.verdict}")

        # 4. Enqueue verdict task
        verdict_payload = dpa_verdict.model_dump() if hasattr(dpa_verdict, "model_dump") else dpa_verdict
        async_result = celery_app.send_task(
            Tasks.VERDICT,
            args=[scan_request_dict, verdict_payload, task_id],
            queue=Queues.VERDICT
        )

        dsx_logging.info(f"[scan_request:{task_id}] Success -> verdict task {async_result.id}")
        return "SUCCESS"

    except Exception as error:
        # Centralized error handling
        decision = decide_retry_strategy(error, retry_count, policy)

        if decision.should_retry:
            dsx_logging.warning(
                f"[scan_request:{task_id}] {decision.reason} (env={policy.environment}): "
                f"retry in {decision.backoff_seconds}s (attempt {retry_count + 1}/{policy.max_retries})"
            )
            raise self.retry(exc=error, countdown=decision.backoff_seconds)

        else:
            # Final failure - to DLQ
            # Note: In RabbitMQ, this would be automatic via DLX configuration
            handle_final_failure(scan_request_dict, error, task_id, retry_count, decision.reason)

            self.update_state(state=states.FAILURE, meta={"reason": decision.reason})
            dsx_logging.error(f"[scan_request:{task_id}] Final failure with {policy.environment} policy: {decision.reason}")
            return "ERROR"