import os, asyncio
from celery.signals import worker_process_init, worker_shutdown
from dsx_connect.messaging.bus import SyncBus
from dsx_connect.messaging.notifiers import Notifiers
from dsx_connect.models.scan_result import ScanResultModel
from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.names import Tasks
from dsx_connect.taskworkers.workers.base_worker import BaseWorker, RetryGroups
from shared.dsx_logging import dsx_logging
from dsx_connect.config import get_config

class ScanResultNotificationWorker(BaseWorker):
    name = Tasks.NOTIFICATION
    RETRY_GROUPS = RetryGroups.none()

    def __init__(self):
        super().__init__()
        self.notifier = Notifiers(SyncBus(str(get_config().redis_url)))

    def execute(self, scan_result_dict: dict):
        dsx_logging.debug(f"[scan_result_notify:{self.context.task_id}] Publishing scan result")
        try:
            count = self.notifier.publish_scan_results_sync(ScanResultModel.model_validate(scan_result_dict))
            dsx_logging.debug(f"[scan_result_notify:{self.context.task_id}] Published to {count} subscriber(s)")
        except Exception as e:
            dsx_logging.warning(f"[scan_result_notify:{self.context.task_id}] publish failed: {e}")
        return "OK"


# Register with Celery
celery_app.register_task(ScanResultNotificationWorker())
