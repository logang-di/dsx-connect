from __future__ import annotations
import time
from typing import Dict, Any, Optional

import httpx
from celery import states
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.names import Tasks, Queues
from dsx_connect.taskworkers.errors import (
    ConnectorConnectionError, ConnectorServerError, ConnectorClientError,
    MalformedScanRequest, MalformedResponse
)
from dsx_connect.taskworkers.policy import load_policy, load_policy_variant, get_policy_info
from dsx_connect.taskworkers.dlq_store import DeadLetterItem, dlq_enqueue, dlq_enqueue_sync
from dsx_connect.models.connector_models import ScanRequestModel, ItemActionEnum
from dsx_connect.dsxa_client.verdict_models import DPAVerdictModel2, DPAVerdictEnum
from dsx_connect.connectors.client import get_connector_client
from shared.dsx_logging import dsx_logging
from shared.routes import ConnectorAPI
from shared.status_responses import ItemActionStatusResponse, StatusResponseEnum
from dsx_connect.config import get_config


# ============================================================================
# Business Logic - Action Execution
# ============================================================================

def should_take_action(verdict: DPAVerdictModel2, policy_config: Any = None) -> bool:
    """
    Determine if action should be taken based on verdict.

    Args:
        verdict: The scan verdict
        policy_config: Future: policy for action thresholds

    Returns:
        True if action should be taken
    """
    # Current logic: take action if MALICIOUS
    if verdict.verdict == DPAVerdictEnum.MALICIOUS:
        return True

    # Future: could check severity thresholds
    # if policy_config and verdict.severity:
    #     return verdict.severity in policy_config.action_severity_levels

    # Future: could check for encrypted files
    # if verdict.verdict_details and "Encrypted" in verdict.verdict_details.reason:
    #     return policy_config.action_on_encrypted_files

    return False


def execute_item_action(scan_request: ScanRequestModel) -> ItemActionStatusResponse:
    """
    Execute item action via connector.

    Args:
        scan_request: Original scan request for the file

    Returns:
        ItemActionStatusResponse indicating success/failure

    Raises:
        ConnectorConnectionError: Connection issues with connector
        ConnectorServerError: Server errors from connector
        ConnectorClientError: Client errors (4xx)
        MalformedResponse: Invalid response from connector
    """
    try:
        with get_connector_client(scan_request.connector_url) as client:
            response = client.post(
                ConnectorAPI.ITEM_ACTION,
                json_body=jsonable_encoder(scan_request),
            )

        response.raise_for_status()

        try:
            action_response = ItemActionStatusResponse.model_validate(response.json())
            return action_response

        except ValidationError as e:
            dsx_logging.error(f"Invalid item_action response format: {e}")
            raise MalformedResponse(f"Invalid response from item_action endpoint: {e}") from e

    except httpx.ConnectError as e:
        if "Name does not resolve" in str(e) or "Connection refused" in str(e):
            raise ConnectorConnectionError(f"Connector unavailable for action: {e}") from e
        raise ConnectorConnectionError(f"Connector connection failed for action: {e}") from e

    except httpx.HTTPStatusError as e:
        if 500 <= e.response.status_code < 600:
            raise ConnectorServerError(f"Connector server error during action {e.response.status_code}") from e
        elif 400 <= e.response.status_code < 500:
            raise ConnectorClientError(f"Connector client error during action {e.response.status_code}") from e
        raise ConnectorConnectionError(f"Connector HTTP error during action {e.response.status_code}") from e


# ============================================================================
# Error Handling for Verdict Action
# ============================================================================

class VerdictActionRetryDecision:
    def __init__(self, should_retry: bool, backoff_seconds: int = 0, reason: str = ""):
        self.should_retry = should_retry
        self.backoff_seconds = backoff_seconds
        self.reason = reason


def decide_verdict_action_retry(error: Exception, attempt: int, policy) -> VerdictActionRetryDecision:
    """
    Decide retry strategy for verdict action errors.
    Similar to scan_request but focused on action execution.
    """

    if isinstance(error, ConnectorConnectionError):
        if not policy.retry_connector_connection_errors:
            return VerdictActionRetryDecision(False, reason="connector_connection_no_retry")
        if attempt >= policy.max_retries:
            return VerdictActionRetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.connector_backoff_base)
        return VerdictActionRetryDecision(True, backoff, "connector_connection_retry")

    elif isinstance(error, ConnectorServerError):
        if not policy.retry_connector_server_errors:
            return VerdictActionRetryDecision(False, reason="connector_server_no_retry")
        if attempt >= policy.max_retries:
            return VerdictActionRetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.server_backoff_base)
        return VerdictActionRetryDecision(True, backoff, "connector_server_retry")

    elif isinstance(error, ConnectorClientError):
        if not policy.retry_connector_client_errors:
            return VerdictActionRetryDecision(False, reason="connector_client_no_retry")
        if attempt >= policy.max_retries:
            return VerdictActionRetryDecision(False, reason="max_retries_exceeded")
        backoff = policy.compute_backoff(attempt + 1, policy.connector_backoff_base)
        return VerdictActionRetryDecision(True, backoff, "connector_client_retry")

    elif isinstance(error, (MalformedScanRequest, MalformedResponse)):
        # Never retry validation/format errors
        return VerdictActionRetryDecision(False, reason="validation_error")

    else:
        # Unknown errors - conservative approach
        return VerdictActionRetryDecision(False, reason="unknown_error")


async def handle_verdict_action_final_failure(scan_request_dict: dict, verdict_dict: dict,
                                              error: Exception, task_id: str, attempt: int, reason: str):
    """Handle final failure for verdict action task."""
    dsx_logging.error(
        f"Final verdict action failure for {scan_request_dict.get('location')} after {attempt} attempts: {reason}"
    )

    # Create DLQ item with verdict context
    dlq_item = DeadLetterItem(
        queue="verdict_action_dlq",  # Different queue for verdict actions
        reason=f"verdict_action_{reason}",
        scan_request=scan_request_dict,
        error_details=str(error),
        retry_count=attempt,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request_dict),
        meta={
            "verdict": verdict_dict,
            "failure_stage": "verdict_action"
        }
    )

    config = get_config()
    dlq_enqueue_sync(dlq_item, ttl_days=getattr(config.workers, 'dlq_expire_after_days', 30))


# ============================================================================
# Main Verdict Action Task
# ============================================================================

@celery_app.task(name=Tasks.VERDICT, bind=True)
def verdict_action_task(self, scan_request_dict: dict, verdict_dict: dict,
                        scan_task_id: str = None, policy_override: str = None) -> str:
    """
    Process a scan verdict and execute actions if needed.

    Args:
        scan_request_dict: Original scan request data
        verdict_dict: Verdict from DSXA scanner
        scan_task_id: ID of the original scan_request task
        policy_override: Optional policy variant

    Returns:
        Status string indicating success/failure
    """
    task_id = self.request.id if hasattr(self, 'request') else "unknown"
    retry_count = self.request.retries if hasattr(self, 'request') else 0

    # Load appropriate policy
    if policy_override:
        policy = load_policy_variant(policy_override)
        dsx_logging.info(f"[verdict_action:{task_id}] Using policy variant: {policy_override}")
    else:
        policy = load_policy()

    # Log policy info on first attempt
    if retry_count == 0:
        policy_info = get_policy_info(policy)
        dsx_logging.debug(f"[verdict_action:{task_id}] Policy: {policy_info}")

    dsx_logging.info(
        f"[verdict_action:{task_id}] Processing verdict from scan_request:{scan_task_id} "
        f"(attempt {retry_count + 1}, env={policy.environment})"
    )

    try:
        # 1. Validate inputs
        try:
            scan_request = ScanRequestModel.model_validate(scan_request_dict)
            verdict = DPAVerdictModel2.model_validate(verdict_dict)
        except ValidationError as e:
            raise MalformedScanRequest(f"Invalid verdict action input: {e}") from e

        location = scan_request.location
        verdict_result = verdict.verdict

        dsx_logging.debug(
            f"[verdict_action:{task_id}] Processing {location} with verdict: {verdict_result}"
        )

        # 2. Determine if action should be taken
        action_needed = should_take_action(verdict)

        if action_needed:
            dsx_logging.info(f"[verdict_action:{task_id}] Action required for {location} (verdict: {verdict_result})")

            # 3. Execute action
            action_response = execute_item_action(scan_request)

            dsx_logging.info(
                f"[verdict_action:{task_id}] Action executed for {location}: "
                f"{action_response.item_action} ({action_response.status})"
            )
        else:
            # No action needed
            dsx_logging.debug(f"[verdict_action:{task_id}] No action required for {location} (verdict: {verdict_result})")

            action_response = ItemActionStatusResponse(
                status=StatusResponseEnum.NOTHING,
                item_action=ItemActionEnum.NOTHING,
                message="No action required",
                description=f"Verdict: {verdict_result}"
            )

        # 4. Send to scan result queue for persistence/reporting
        result_payload = {
            "scan_request": scan_request_dict,
            "verdict": verdict_dict,
            "item_action": action_response.model_dump(),
            "original_task_id": scan_task_id
        }

        async_result = celery_app.send_task(
            Tasks.RESULT,
            args=[scan_request_dict, verdict_dict, action_response.model_dump(), scan_task_id],
            queue=Queues.RESULT
        )

        dsx_logging.info(
            f"[verdict_action:{task_id}] Success -> scan_result task {async_result.id} "
            f"(action: {action_response.item_action})"
        )

        return "SUCCESS"

    except Exception as error:
        # Handle retries with policy
        decision = decide_verdict_action_retry(error, retry_count, policy)

        if decision.should_retry:
            dsx_logging.warning(
                f"[verdict_action:{task_id}] {decision.reason} (env={policy.environment}): "
                f"retry in {decision.backoff_seconds}s (attempt {retry_count + 1}/{policy.max_retries})"
            )
            raise self.retry(exc=error, countdown=decision.backoff_seconds)

        else:
            # Final failure - to DLQ
            import asyncio
            asyncio.run(handle_verdict_action_final_failure(
                scan_request_dict, verdict_dict, error, task_id, retry_count, decision.reason
            ))

            # Still need to send to scan_result for tracking, even if action failed
            try:
                error_action_response = ItemActionStatusResponse(
                    status=StatusResponseEnum.ERROR,
                    item_action=ItemActionEnum.NOTHING,
                    message="Action execution failed",
                    description=f"Final error: {decision.reason}"
                )

                celery_app.send_task(
                    Tasks.RESULT,
                    args=[scan_request_dict, verdict_dict, error_action_response.model_dump(), scan_task_id],
                    queue=Queues.RESULT
                )

                dsx_logging.info(f"[verdict_action:{task_id}] Sent error result to scan_result queue")

            except Exception as result_error:
                dsx_logging.error(f"[verdict_action:{task_id}] Failed to send error result: {result_error}")

            self.update_state(state=states.FAILURE, meta={"reason": decision.reason})
            dsx_logging.error(f"[verdict_action:{task_id}] Final failure: {decision.reason}")

            return "ERROR"


# ============================================================================
# Specialized Verdict Action Tasks
# ============================================================================

@celery_app.task(name=f"{Tasks.VERDICT}.conservative", bind=True)
def verdict_action_conservative_task(self, scan_request_dict: dict, verdict_dict: dict,
                                     original_task_id: str = None) -> str:
    """Conservative verdict action - minimal retries, fail fast if connector unavailable."""
    return verdict_action_task(
        self, scan_request_dict, verdict_dict, original_task_id,
        policy_override="high_throughput"
    )


@celery_app.task(name=f"{Tasks.VERDICT}.aggressive", bind=True)
def verdict_action_aggressive_task(self, scan_request_dict: dict, verdict_dict: dict,
                                   original_task_id: str = None) -> str:
    """Aggressive verdict action - maximum retries for critical actions."""
    return verdict_action_task(
        self, scan_request_dict, verdict_dict, original_task_id,
        policy_override="critical_files"
    )


# ============================================================================
# Usage Examples and Integration
# ============================================================================

def enqueue_verdict_action(scan_request: dict, verdict: dict, original_task_id: str,
                           action_priority: str = "normal"):
    """
    Enqueue verdict action with appropriate task based on priority.

    Args:
        scan_request: Original scan request
        verdict: Scan verdict
        original_task_id: ID of scan request task
        action_priority: Priority level for action execution
    """

    if action_priority == "conservative":
        # Use conservative task for batch processing
        return celery_app.send_task(
            f"{Tasks.VERDICT}.conservative",
            args=[scan_request, verdict, original_task_id],
            queue=Queues.VERDICT
        )
    elif action_priority == "critical":
        # Use aggressive task for critical files
        return celery_app.send_task(
            f"{Tasks.VERDICT}.aggressive",
            args=[scan_request, verdict, original_task_id],
            queue=Queues.VERDICT
        )
    else:
        # Use standard task with environment-based policy
        return celery_app.send_task(
            Tasks.VERDICT,
            args=[scan_request, verdict, original_task_id],
            queue=Queues.VERDICT
        )


# For testing/debugging
if __name__ == "__main__":
    # Example usage
    sample_scan_request = {
        "location": "/test/malicious.exe",
        "connector_url": "http://connector:8080",
        "metainfo": "test-file",
        "connector_uuid": "test-connector"
    }

    sample_verdict = {
        "scan_guid": "test-scan-123",
        "verdict": "Malicious",
        "verdict_details": {"event_description": "Test malware detected"},
        "file_info": {"file_type": "PE", "file_size_in_bytes": 1024},
        "scan_duration_in_microseconds": 5000
    }

    print("Would execute verdict action for:")
    print(f"  File: {sample_scan_request['location']}")
    print(f"  Verdict: {sample_verdict['verdict']}")
    print(f"  Action needed: {should_take_action(DPAVerdictModel2.model_validate(sample_verdict))}")