# dsx_connect/taskworkers/workers/scan_result.py
from __future__ import annotations
from typing import Any, Dict
from celery import signals
from functools import cached_property

from pydantic import ValidationError

from dsx_connect.config import ConfigDatabaseType
from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.names import Tasks, Queues
from dsx_connect.taskworkers.workers.base_worker import BaseWorker, RetryGroup, RetryGroups
from dsx_connect.taskworkers.dlq_store import enqueue_scan_result_dlq_sync, \
    make_scan_result_dlq_item
from dsx_connect.taskworkers.errors import TaskError, MalformedScanRequest, MalformedResponse

from dsx_connect.dsxa_client.verdict_models import DPAVerdictModel2
from shared.models.connector_models import ScanRequestModel, ItemActionModel
from dsx_connect.models.scan_result import ScanResultModel, ScanResultStatusEnum
from shared.dsx_logging import dsx_logging
from shared.models.status_responses import ItemActionStatusResponse


# Optional extras (DB, stats, notifications) are best-effort and should NOT trigger retries.
# If you have helpers, import them here; otherwise keep the try/except blocks inline.

def _send_syslog(scan_result: ScanResultModel, original_task_id: str, current_task_id: str) -> None:
    """Critical path: raise retriable TaskError on failure to trigger BaseWorker retry."""
    try:
        from shared.log_chain import log_verdict_chain
        log_verdict_chain(
            scan_result=scan_result,
            scan_request_task_id=original_task_id,
            current_task_id=current_task_id,
        )
        dsx_logging.debug(f"[scan_result:{current_task_id}] syslog sent for {scan_result.scan_request.location}")
    except Exception as e:
        # Mark as retriable so BaseWorker will backoff/retry
        raise TaskError(retriable=True, reason="syslog_failure") from e


class ScanResultWorker(BaseWorker):
    name = Tasks.RESULT
    RETRY_GROUPS = RetryGroups.none()  # no connector/dsxa mapping; retries driven by TaskError.retriable
    _scan_results_db = None
    _stats_db = None

    def __init__(self):
        super().__init__()
        if self.__class__._scan_results_db is None:
            from dsx_connect.config import get_config
            from dsx_connect.database.database_factory import (
                database_scan_results_factory, database_scan_stats_factory
            )
            cfg = get_config()
            # one-time, per-process
            self.__class__._scan_results_db = database_scan_results_factory(
                database_type=cfg.database.type,
                database_loc=cfg.database.loc,
                retain=cfg.database.retain,
                collection_name="scan_results",
            )
            self.__class__._stats_db = database_scan_stats_factory(
                database_type=ConfigDatabaseType.TINYDB,
                database_loc=cfg.database.scan_stats_db,
                collection_name="scan_stats",
            )

    def execute(
            self,
            scan_request_dict: Dict[str, Any],
            verdict_dict: Dict[str, Any],
            item_action_dict: Dict[str, Any],
            *,
            scan_request_task_id: str) -> str:
        # 1) validate inputs
        try:
            scan_request = ScanRequestModel.model_validate(scan_request_dict)
            verdict = DPAVerdictModel2.model_validate(verdict_dict)
            item_action_status = ItemActionStatusResponse.model_validate(item_action_dict)
        except ValidationError as e:
            raise MalformedResponse(f"Invalid scan_result inputs: {e}") from e

        # 2) build ScanResultModel
        scan_result = ScanResultModel(
            scan_request=scan_request,
            verdict=verdict,
            item_action=item_action_status,
            scan_request_task_id=scan_request_task_id,   # ← add this
            # status=ScanResultStatusEnum.SUCCESS,  # only if this member exists; else omit
        )

        # 3) critical: send syslog (retryable on failure)
        _send_syslog(scan_result, original_task_id=scan_request_task_id, current_task_id=self.context.task_id)

        # 4) optional extras (best-effort; never raise to retry)
        self._best_effort_extras(scan_result)

        dsx_logging.info(f"[scan_result:{self.context.task_id}] completed for {scan_request.location}")
        return "SUCCESS"

    def _best_effort_extras(self, scan_result: ScanResultModel) -> None:
        """Store to DB / update stats / notify UI if enabled; never cause retries."""
        try:
            from dsx_connect.config import get_config
            cfg = get_config()

            if getattr(cfg.workers, "enable_scan_results_db", True):
                try:
                    self.__class__._scan_results_db.insert(scan_result)
                    dsx_logging.debug("[scan_result] stored in scan_results DB")
                except Exception as e:
                    dsx_logging.warning(f"[scan_result] store DB failed: {e}")

            if getattr(cfg.workers, "enable_scan_stats", True):
                try:
                    from dsx_connect.database.scan_stats_worker import ScanStatsWorker
                    ScanStatsWorker(self.__class__._stats_db).insert(scan_result)
                    dsx_logging.debug("[scan_result] stats updated")
                except Exception as e:
                    dsx_logging.warning(f"[scan_result] stats update failed: {e}")

            # Example: UI notifications
            if getattr(cfg.workers, "enable_notifications", True):
                try:
                    task = celery_app.send_task(
                        name=Tasks.NOTIFICATION,
                        queue=Queues.NOTIFICATION,
                        args=[scan_result.model_dump()]
                    )
                    dsx_logging.debug("[scan_result] notification published")
                except Exception as e:
                    dsx_logging.warning(f"[scan_result] notify failed: {e}")

        except Exception as e:
            # Don't bubble from extras
            dsx_logging.warning(f"[scan_result] extras wrapper failed: {e}")


    def _enqueue_dlq(
            self,
            *,
            error: Exception,
            reason: str,
            scan_request_task_id: str,
            current_task_id: str,
            retry_count: int,
            upstream_task_id: str | None = None,
            args: tuple,
            kwargs: dict,
    ) -> None:
        # args: [scan_request_dict, verdict_dict, item_action_dict, original_task_id?]
        scan_request_dict = args[0] if len(args) > 0 else {}
        verdict_dict      = args[1] if len(args) > 1 else {}
        item_action_dict  = args[2] if len(args) > 2 else {}

        item = make_scan_result_dlq_item(
            scan_request=scan_request_dict,
            verdict=verdict_dict,
            item_action=item_action_dict,
            error=error,
            reason=reason,
            scan_request_task_id=scan_request_task_id,  # forwarded root id
            current_task_id=current_task_id,            # this failing task
            retry_count=retry_count,
            upstream_task_id=upstream_task_id,
        )
        enqueue_scan_result_dlq_sync(item)

# Register with Celery
celery_app.register_task(ScanResultWorker())
