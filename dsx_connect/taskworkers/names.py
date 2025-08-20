# dsx_connect/messaging/celery_names.py
import os
from typing import Final
from dsx_connect.config import APP_ENV

SERVICE: Final = "dsx_connect"  # Python package style (no hyphen)


class Queues:
    DEFAULT: Final = f"{APP_ENV}.{SERVICE}.scans.default"
    REQUEST: Final = f"{APP_ENV}.{SERVICE}.scans.request"
    VERDICT: Final = f"{APP_ENV}.{SERVICE}.scans.verdict"
    RESULT: Final = f"{APP_ENV}.{SERVICE}.scans.result"
    NOTIFICATION: Final = f"{APP_ENV}.{SERVICE}.scans.result.notify"
    DLQ:          Final = f"{APP_ENV}.{SERVICE}.dlq"


class Tasks:
    # Keep task names environment-agnostic (dotted module paths)
    REQUEST: Final = "dsx_connect.tasks.scan.request"
    VERDICT: Final = "dsx_connect.tasks.scan.verdict"
    RESULT: Final = "dsx_connect.tasks.scan.result"
    NOTIFICATION: Final = "dsx_connect.tasks.scan.result.notify"
    DLQ:          Final = "dsx_connect.tasks.dlq.dead_letter"
