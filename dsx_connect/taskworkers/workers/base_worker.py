# dsx_connect/taskworkers/base_task.py

from __future__ import annotations
import abc

from celery import Task, states
from pydantic import ValidationError

from dsx_connect.taskworkers.policy import load_policy, load_policy_variant, get_policy_info, RetryPolicy
from dsx_connect.taskworkers.dlq_store import DeadLetterItem, dlq_enqueue_sync
from dsx_connect.taskworkers.errors import TaskError, MalformedScanRequest
from dsx_connect.config import get_config
from shared.dsx_logging import dsx_logging

class RetryDecision:
    """A simple data class to hold the outcome of a retry decision."""
    def __init__(self, should_retry: bool, backoff_seconds: int = 0, reason: str = ""):
        self.should_retry = should_retry
        self.backoff_seconds = backoff_seconds
        self.reason = reason


class TaskContext:
    """Unified task context for all workers."""
    def __init__(self, task_self):
        self.task_id = task_self.request.id if hasattr(task_self, 'request') else "unknown"
        self.retry_count = task_self.request.retries if hasattr(task_self, 'request') else 0
        self.task_self = task_self

        self.policy = load_policy()

        # Log policy info on first attempt
        if self.retry_count == 0:
            policy_info = get_policy_info(self.policy)
            dsx_logging.debug(f"[{self.task_id}] Policy: {policy_info}")


class BaseWorker(Task):
    """
    A base class for Celery tasks that provides standardized
    logging, policy loading, retry, and Dead Letter Queue (DLQ) logic.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = self.__class__.__name__
        self.context = TaskContext(kwargs.get('policy_override'))

    def run(self, *args, **kwargs):
        """
        This is the entry point for the task execution, called by Celery.
        It orchestrates policy loading, execution, and exception handling.
        """
        dsx_logging.info(f"[{self.name}:{self.context.task_id}] Processing (attempt {retry_count + 1}/{policy.max_retries + 1})")

        try:
            # Execute the task's specific business logic
            result = self.execute(*args, **kwargs)
            dsx_logging.info(f"[{self.name}:{task_id}] Task completed successfully.")
            return result
        except Exception as error:
            # Centralized exception handling
            self._handle_exception(error, policy, *args, **kwargs)

    def _handle_exception(self, error: Exception, policy: RetryPolicy, *args, **kwargs):
        """
        Determines whether to retry the task or send it to the DLQ.
        """
        task_id = self.request.id or "unknown"
        retry_count = self.request.retries or 0

        # Decide on the retry strategy based on the error type and policy
        decision = self._decide_retry_strategy(error, retry_count, policy)

        if decision.should_retry:
            dsx_logging.warning(
                f"[{self.name}:{task_id}] Retriable error ({decision.reason}): "
                f"retrying in {decision.backoff_seconds}s (attempt {retry_count + 1}/{policy.max_retries})."
            )
            # Celery's retry mechanism
            raise self.retry(exc=error, countdown=decision.backoff_seconds)
        else:
            # If no more retries, move to the DLQ
            dsx_logging.error(f"[{self.name}:{task_id}] Final failure ({decision.reason}). Sending to DLQ.")
            self._handle_final_failure(error, decision.reason, *args, **kwargs)
            # Update Celery's state for monitoring
            self.update_state(state=states.FAILURE, meta={"reason": decision.reason})

    def _decide_retry_strategy(self, error: Exception, attempt: int, policy: RetryPolicy) -> RetryDecision:
        """
        Centralized retry decision logic based on error type.
        This replaces the duplicated `decide_..._retry` functions in each worker.
        """
        # Immediately fail on validation errors
        if isinstance(error, (MalformedScanRequest, ValidationError)):
            return RetryDecision(False, reason="validation_error")

        # Use the custom error's `retriable` flag if it's a known TaskError
        if isinstance(error, TaskError):
            if not error.retriable:
                return RetryDecision(False, reason=error.reason)
            if attempt >= policy.max_retries:
                return RetryDecision(False, reason="max_retries_exceeded")

            # Simple backoff for now, can be customized based on error.reason
            backoff = policy.compute_backoff(attempt + 1, policy.server_backoff_base)
            return RetryDecision(True, backoff, error.reason)

        # For unknown errors, take a conservative approach
        dsx_logging.error(f"[{self.name}:{self.request.id}] Encountered unknown error: {error}", exc_info=True)
        return RetryDecision(False, reason="unknown_error")

    def _handle_final_failure(self, error: Exception, reason: str, *args, **kwargs):
        """
        Constructs and enqueues a message to the Dead Letter Queue.
        """
        task_id = self.request.id or "unknown"
        retry_count = self.request.retries or 0

        # The first argument is typically the main data payload (e.g., scan_request_dict)
        scan_request_dict = args[0] if args and isinstance(args[0], dict) else {}

        # Additional context from other arguments
        meta_context = {f"arg_{i}": v for i, v in enumerate(args[1:])}

        dlq_item = DeadLetterItem(
            queue=f"{self.name.split('.')[-1]}_dlq", # e.g., "scan_request_dlq"
            reason=reason,
            scan_request=scan_request_dict,
            error_details=str(error),
            retry_count=retry_count,
            original_task_id=task_id,
            idempotency_key=DeadLetterItem.compute_idempotency_key(scan_request_dict),
            meta=meta_context
        )

        config = get_config()
        ttl_days = getattr(config.workers, 'dlq_expire_after_days', 30)
        dlq_enqueue_sync(dlq_item, ttl_days=ttl_days)

