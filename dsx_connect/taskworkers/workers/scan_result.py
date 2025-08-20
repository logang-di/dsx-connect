# dsx_connect/taskworkers/workers/scan_result.py

from __future__ import annotations
from typing import Dict, Any, Optional

from celery import states
from pydantic import ValidationError

from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.names import Tasks, Queues
from dsx_connect.taskworkers.errors import MalformedScanRequest, MalformedResponse
from dsx_connect.taskworkers.policy import load_policy, load_policy_variant, get_policy_info
from dsx_connect.taskworkers.dlq_store import DeadLetterItem, dlq_enqueue
from dsx_connect.models.connector_models import ScanRequestModel
from dsx_connect.dsxa_client.verdict_models import DPAVerdictModel2
from dsx_connect.models.scan_models import ScanResultModel, ScanResultStatusEnum
from shared.dsx_logging import dsx_logging
from shared.status_responses import ItemActionStatusResponse
from dsx_connect.config import get_config


# ============================================================================
# Core Business Logic - Syslog (Critical) vs Extras (Optional)
# ============================================================================

def send_syslog_entry(scan_result: ScanResultModel, original_task_id: str, current_task_id: str) -> bool:
    """
    Send syslog entry for scan result - THIS IS THE CRITICAL FUNCTION.

    Args:
        scan_result: Complete scan result data
        original_task_id: ID of original scan request
        current_task_id: ID of current scan result task

    Returns:
        True if syslog sent successfully, False otherwise

    Raises:
        Exception: Any syslog-related errors that should trigger retry
    """
    try:
        from dsx_connect.utils.log_chain import log_verdict_chain

        log_verdict_chain(
            scan_result=scan_result,
            original_task_id=original_task_id,
            current_task_id=current_task_id
        )

        dsx_logging.debug(f"Syslog entry sent for {scan_result.scan_request.location}")
        return True

    except Exception as e:
        dsx_logging.error(f"Failed to send syslog for {scan_result.scan_request.location}: {e}")
        raise  # Re-raise to trigger retry


def process_optional_features(scan_result: ScanResultModel, config) -> dict:
    """
    Process optional/demonstration features. Failures here should NOT cause task retry.

    Args:
        scan_result: Complete scan result data
        config: Application configuration

    Returns:
        Dict with results of each optional operation
    """
    results = {
        "scan_results_db": {"enabled": False, "success": False, "error": None},
        "scan_stats_db": {"enabled": False, "success": False, "error": None},
        "notification_queue": {"enabled": False, "success": False, "error": None}
    }

    # 1. Store in scan results database (optional)
    if getattr(config.workers, 'enable_scan_results_db', True):
        results["scan_results_db"]["enabled"] = True
        try:
            # Access the global database instance (initialized in worker_process_init)
            global _scan_results_db
            if _scan_results_db:
                _scan_results_db.insert(scan_result)
                results["scan_results_db"]["success"] = True
                dsx_logging.debug(f"Stored scan result in database for {scan_result.scan_request.location}")
            else:
                results["scan_results_db"]["error"] = "Database not initialized"
        except Exception as e:
            results["scan_results_db"]["error"] = str(e)
            dsx_logging.warning(f"Failed to store scan result in database: {e}")

    # 2. Update scan statistics (optional)
    if getattr(config.workers, 'enable_scan_stats', True):
        results["scan_stats_db"]["enabled"] = True
        try:
            global _scan_stats_worker
            if _scan_stats_worker:
                _scan_stats_worker.insert(scan_result)
                results["scan_stats_db"]["success"] = True
                dsx_logging.debug(f"Updated scan stats for {scan_result.scan_request.location}")
            else:
                results["scan_stats_db"]["error"] = "Stats worker not initialized"
        except Exception as e:
            results["scan_stats_db"]["error"] = str(e)
            dsx_logging.warning(f"Failed to update scan stats: {e}")

    # 3. Queue notification (optional)
    if getattr(config.workers, 'enable_scan_notifications', True):
        results["notification_queue"]["enabled"] = True
        try:
            async_result = celery_app.send_task(
                name=Tasks.NOTIFICATION,
                queue=Queues.NOTIFICATION,
                args=[scan_result.model_dump()]
            )
            results["notification_queue"]["success"] = True
            results["notification_queue"]["task_id"] = async_result.id
            dsx_logging.debug(f"Queued scan result notification with task {async_result.id}")
        except Exception as e:
            results["notification_queue"]["error"] = str(e)
            dsx_logging.warning(f"Failed to queue scan result notification: {e}")

    return results


# ============================================================================
# Error Handling - Only Retry for Syslog Failures
# ============================================================================

class ScanResultRetryDecision:
    def __init__(self, should_retry: bool, backoff_seconds: int = 0, reason: str = ""):
        self.should_retry = should_retry
        self.backoff_seconds = backoff_seconds
        self.reason = reason


def decide_scan_result_retry(error: Exception, attempt: int, policy) -> ScanResultRetryDecision:
    """
    Decide retry strategy for scan result errors.

    Key insight: We only retry for syslog failures. Everything else is optional.
    """

    # Only retry validation errors if they're about core data
    if isinstance(error, (MalformedScanRequest, MalformedResponse)):
        return ScanResultRetryDecision(False, reason="validation_error")

    # For any other error (assumed to be syslog-related), apply retry policy
    # Use server backoff since syslog is infrastructure-like
    if attempt >= policy.max_retries:
        return ScanResultRetryDecision(False, reason="max_retries_exceeded")

    backoff = policy.compute_backoff(attempt + 1, policy.server_backoff_base)
    return ScanResultRetryDecision(True, backoff, "syslog_retry")


async def handle_scan_result_final_failure(scan_request_dict: dict, verdict_dict: dict,
                                           item_action_dict: dict, error: Exception,
                                           task_id: str, attempt: int, reason: str):
    """Handle final failure for scan result task."""
    location = scan_request_dict.get("location", "unknown")

    dsx_logging.error(
        f"Final scan result failure for {location} after {attempt} attempts: {reason}"
    )

    # Create DLQ item with full context
    dlq_item = DeadLetterItem(
        queue="scan_result_dlq",
        reason=f"scan_result_{reason}",
        scan_request=scan_request_dict,
        error_details=str(error),
        retry_count=attempt,
        original_task_id=task_id,
        idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request_dict),
        meta={
            "verdict": verdict_dict,
            "item_action": item_action_dict,
            "failure_stage": "scan_result_syslog"
        }
    )

    config = get_config()
    await dlq_enqueue(dlq_item, ttl_days=getattr(config.workers, 'dlq_expire_after_days', 30))


# ============================================================================
# Main Scan Result Task
# ============================================================================

# Global variables for database connections (initialized in worker_process_init)
_scan_results_db = None
_scan_stats_worker = None

@celery_app.task(name=Tasks.RESULT, bind=True)
def scan_result_task(self, scan_request_dict: dict, verdict_dict: dict, item_action_dict: dict,
                     original_task_id: str = None, policy_override: str = None) -> str:
    """
    Process scan results for persistence, statistics, reporting and logging.

    CRITICAL: Syslog entry must succeed - will retry on failure.
    OPTIONAL: Database storage, stats, notifications - failures are logged but ignored.

    Args:
        scan_request_dict: Original scan request data
        verdict_dict: Scan verdict data
        item_action_dict: Item action results
        original_task_id: ID of the original scan request task
        policy_override: Optional policy variant

    Returns:
        Status string indicating success/failure
    """
    task_id = self.request.id if hasattr(self, 'request') else "unknown"
    retry_count = self.request.retries if hasattr(self, 'request') else 0

    # Load appropriate policy
    if policy_override:
        policy = load_policy_variant(policy_override)
        dsx_logging.info(f"[scan_result:{task_id}] Using policy variant: {policy_override}")
    else:
        policy = load_policy()

    # Log policy info on first attempt
    if retry_count == 0:
        policy_info = get_policy_info(policy)
        dsx_logging.debug(f"[scan_result:{task_id}] Policy: {policy_info}")

    dsx_logging.info(
        f"[scan_result:{task_id}] Processing result from scan_request:{original_task_id} "
        f"(attempt {retry_count + 1}, env={policy.environment})"
    )

    config = get_config()

    try:
        # 1. Validate and construct scan result
        try:
            scan_request = ScanRequestModel.model_validate(scan_request_dict)
            verdict = DPAVerdictModel2.model_validate(verdict_dict)
            item_action = ItemActionStatusResponse.model_validate(item_action_dict)

            scan_result = ScanResultModel(
                scan_request_task_id=original_task_id,
                metadata_tag=scan_request.metainfo,
                scan_request=scan_request,
                status=ScanResultStatusEnum.SCANNED,
                item_action=item_action,
                verdict=verdict
            )

        except ValidationError as e:
            raise MalformedScanRequest(f"Invalid scan result data: {e}") from e

        location = scan_request.location
        verdict_result = verdict.verdict
        action_taken = item_action.item_action

        dsx_logging.debug(
            f"[scan_result:{task_id}] Processing {location} "
            f"(verdict: {verdict_result}, action: {action_taken})"
        )

        # 2. CRITICAL: Send syslog entry (will throw exception on failure)
        try:
            send_syslog_entry(scan_result, original_task_id, task_id)
            dsx_logging.debug(f"[scan_result:{task_id}] Syslog sent successfully for {location}")
        except Exception as syslog_error:
            # This is the only error that should cause task retry
            dsx_logging.error(f"[scan_result:{task_id}] Syslog failed for {location}: {syslog_error}")
            raise syslog_error

        # 3. OPTIONAL: Process demonstration/PoV features (failures are ignored)
        optional_results = process_optional_features(scan_result, config)

        # Log summary of optional features
        enabled_features = [name for name, result in optional_results.items() if result["enabled"]]
        successful_features = [name for name, result in optional_results.items()
                               if result["enabled"] and result["success"]]
        failed_features = [name for name, result in optional_results.items()
                           if result["enabled"] and not result["success"]]

        if enabled_features:
            dsx_logging.info(
                f"[scan_result:{task_id}] Optional features - "
                f"Enabled: {len(enabled_features)}, "
                f"Successful: {len(successful_features)}, "
                f"Failed: {len(failed_features)}"
            )

            if failed_features:
                dsx_logging.warning(
                    f"[scan_result:{task_id}] Failed optional features: {failed_features}"
                )

        # Success!
        dsx_logging.info(
            f"[scan_result:{task_id}] Success with {policy.environment} policy - "
            f"syslog sent, {len(successful_features)}/{len(enabled_features)} optional features completed"
        )

        return "SUCCESS"

    except Exception as error:
        # Handle retries with policy - only for critical syslog failures
        decision = decide_scan_result_retry(error, retry_count, policy)

        if decision.should_retry:
            dsx_logging.warning(
                f"[scan_result:{task_id}] {decision.reason} (env={policy.environment}): "
                f"retry in {decision.backoff_seconds}s (attempt {retry_count + 1}/{policy.max_retries})"
            )
            raise self.retry(exc=error, countdown=decision.backoff_seconds)

        else:
            # Final failure - to DLQ
            import asyncio
            asyncio.run(handle_scan_result_final_failure(
                scan_request_dict, verdict_dict, item_action_dict, error, task_id, retry_count, decision.reason
            ))

            self.update_state(state=states.FAILURE, meta={"reason": decision.reason})
            dsx_logging.error(
                f"[scan_result:{task_id}] Final failure with {policy.environment} policy: {decision.reason}"
            )

            return "ERROR"


# ============================================================================
# Specialized Scan Result Tasks
# ============================================================================

@celery_app.task(name=f"{Tasks.RESULT}.syslog_only", bind=True)
def scan_result_syslog_only_task(self, scan_request_dict: dict, verdict_dict: dict,
                                 item_action_dict: dict, original_task_id: str = None) -> str:
    """Syslog-only variant - disables all optional features."""
    # Temporarily disable optional features for this task
    config = get_config()
    original_settings = {}

    # Save original settings
    for feature in ['enable_scan_results_db', 'enable_scan_stats', 'enable_scan_notifications']:
        original_settings[feature] = getattr(config.workers, feature, True)
        setattr(config.workers, feature, False)

    try:
        result = scan_result_task(self, scan_request_dict, verdict_dict, item_action_dict, original_task_id)
    finally:
        # Restore original settings
        for feature, value in original_settings.items():
            setattr(config.workers, feature, value)

    return result


@celery_app.task(name=f"{Tasks.RESULT}.critical", bind=True)
def scan_result_critical_task(self, scan_request_dict: dict, verdict_dict: dict,
                              item_action_dict: dict, original_task_id: str = None) -> str:
    """Critical scan result - maximum retries for syslog."""
    return scan_result_task(
        self, scan_request_dict, verdict_dict, item_action_dict, original_task_id,
        policy_override="critical_files"
    )


# ============================================================================
# Configuration and Integration
# ============================================================================

def enqueue_scan_result(scan_request: dict, verdict: dict, item_action: dict,
                        original_task_id: str, priority: str = "normal", policy_override: str = None):
    """
    Enqueue scan result processing with appropriate task based on priority.

    Args:
        scan_request: Original scan request
        verdict: Scan verdict
        item_action: Action results
        original_task_id: ID of scan request task
        priority: Priority level affecting optional features
        policy_override: Explicit policy override
    """

    if priority == "syslog_only":
        # Minimal processing - syslog only
        return celery_app.send_task(
            f"{Tasks.RESULT}.syslog_only",
            args=[scan_request, verdict, item_action, original_task_id],
            queue=Queues.RESULT
        )
    elif priority == "critical":
        # Maximum retries for critical results
        return celery_app.send_task(
            f"{Tasks.RESULT}.critical",
            args=[scan_request, verdict, item_action, original_task_id],
            queue=Queues.RESULT
        )
    else:
        # Standard processing with optional features
        args = [scan_request, verdict, item_action, original_task_id]
        if policy_override:
            args.append(policy_override)

        return celery_app.send_task(
            Tasks.RESULT,
            args=args,
            queue=Queues.RESULT
        )


# ============================================================================
# Worker Initialization (matches your existing pattern)
# ============================================================================

from celery.signals import worker_process_init

@worker_process_init.connect
def init_scan_result_worker(**kwargs):
    """Initialize database connections for scan result worker."""
    global _scan_results_db, _scan_stats_worker

    config = get_config()

    # Initialize scan results database if enabled
    if getattr(config.workers, 'enable_scan_results_db', True):
        try:
            from dsx_connect.database.database_factory import database_scan_results_factory
            _scan_results_db = database_scan_results_factory(
                database_type=config.database.type,
                database_loc=config.database.loc,
                retain=config.database.retain,
                collection_name="scan_results"
            )
            dsx_logging.info(f"Initialized scan results database: {config.database.type}")
        except Exception as e:
            dsx_logging.error(f"Failed to initialize scan results database: {e}")
            _scan_results_db = None

    # Initialize scan stats worker if enabled
    if getattr(config.workers, 'enable_scan_stats', True):
        try:
            from dsx_connect.database.database_factory import database_scan_stats_factory
            from dsx_connect.database.scan_stats_worker import ScanStatsWorker
            from dsx_connect.config import ConfigDatabaseType

            scan_stats_db = database_scan_stats_factory(
                database_type=ConfigDatabaseType.TINYDB,
                database_loc=config.database.scan_stats_db,
                collection_name="scan_stats"
            )
            _scan_stats_worker = ScanStatsWorker(scan_stats_db)
            dsx_logging.info("Initialized scan stats worker")
        except Exception as e:
            dsx_logging.error(f"Failed to initialize scan stats worker: {e}")
            _scan_stats_worker = None


# For testing/debugging
if __name__ == "__main__":
    print("Scan Result Worker - Feature Priorities:")
    print("  CRITICAL: Syslog entry (will retry on failure)")
    print("  OPTIONAL: Database storage, statistics, notifications (log errors but continue)")
    print()
    print("Configuration flags:")
    print("  DSXCONNECT_WORKERS__ENABLE_SCAN_RESULTS_DB=true/false")
    print("  DSXCONNECT_WORKERS__ENABLE_SCAN_STATS=true/false")
    print("  DSXCONNECT_WORKERS__ENABLE_SCAN_NOTIFICATIONS=true/false")