import os, asyncio
from celery.signals import worker_process_init, worker_shutdown
from redis.asyncio import Redis
from dsx_connect.messaging.bus import Bus
from dsx_connect.messaging.notifiers import Notifiers
from dsx_connect.models.scan_models import ScanResultModel
from dsx_connect.taskworkers.celery_app import celery_app
from dsx_connect.taskworkers.names import Tasks
from shared.dsx_logging import dsx_logging
from dsx_connect.config import get_config

redis = bus = notifier = cfg = None

@worker_process_init.connect
def _init_messaging(**_):
    global redis, bus, notifier, cfg
    cfg = get_config()
    try:
        redis = Redis.from_url(str(cfg.redis_url), decode_responses=False,
                               socket_connect_timeout=0.5, socket_timeout=0.5)
        asyncio.run(redis.ping())                     # one-time fast-fail
        bus = Bus(redis)
        notifier = Notifiers(bus)
        dsx_logging.info("Scan result notifier initialized.")
    except Exception as e:
        dsx_logging.error(f"Scan result notifier init failed: {e}")

@worker_shutdown.connect
def _shutdown_messaging(**_):
    if redis:
        try:
            asyncio.run(redis.aclose())
        except Exception:
            pass


@celery_app.task(name=Tasks.NOTIFICATION)
def scan_result_notify_task(scan_result_dict: dict):
    if notifier is None:
        dsx_logging.error("Notifier not initialized.")
        return 0  # or log/fallback
    return asyncio.run(notifier.publish_scan_results(ScanResultModel.model_validate(scan_result_dict)))
