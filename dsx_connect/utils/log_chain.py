import logging
import logging.handlers
import json
import socket
from datetime import datetime

from typing import Optional

from fastapi.encoders import jsonable_encoder

from dsx_connect.config import SecurityConfig
from dsx_connect.dsxa_client.verdict_models import DPAVerdictEnum
from dsx_connect.models.responses import ItemActionStatusResponse
from dsx_connect.models.scan_models import DPAVerdictModel2, ScanResultModel
from dsx_connect.models.connector_models import ScanRequestModel

# -------------------------------------------------------------------
# 1) APPLICATION LOGGER (console, file, etc.)
# -------------------------------------------------------------------
dsx_logging = logging.getLogger(__name__)
# (Elsewhere you’d configure dsx_logging’s handlers/formatters as you like,
# e.g. StreamHandler to stdout or a FileHandler.)

# -------------------------------------------------------------------
# 2) SYSLOG LOGGER (sends ONLY to SysLog server)
# -------------------------------------------------------------------
syslog_logger = logging.getLogger("syslog_logger")
syslog_logger.setLevel(logging.INFO)  # Only INFO or above go to syslog
# We delay attaching the SysLogHandler until init_syslog_handler() is called.

_syslog_handler: Optional[logging.Handler] = None


def init_syslog_handler(syslog_host: str = "localhost", syslog_port: int = 514):
    """Initialize the syslog handler for the worker process."""
    global _syslog_handler
    if _syslog_handler:
        return   # already initialized

    try:
        _syslog_handler = logging.handlers.SysLogHandler(
            address=(syslog_host, syslog_port),
            facility=logging.handlers.SysLogHandler.LOG_LOCAL0,
            socktype=socket.SOCK_DGRAM  # UDP for syslog
        )
        _syslog_handler.setFormatter(logging.Formatter('%(message)s'))
        syslog_logger.addHandler(_syslog_handler)

        # Emit the initial “workers initialized” message to remote syslog
        syslog_logger.info("dsx-connect-workers1 initialized to use syslog")

        dsx_logging.info(f"Initialized syslog handler for {syslog_host}:{syslog_port}")
    except Exception as e:
        dsx_logging.error(f"Failed to initialize syslog handler: {e}")


def log_verdict_chain(scan_result: ScanResultModel, original_task_id: str, current_task_id: Optional[str] = None) -> None:
    """
    Log the complete chain (scan request, verdict, and item action) to syslog.

    Args:
        scan_request: The original scan request details.
        verdict: The scan verdict result.
        item_action_status: Whether the item_action (if triggered) was successful and the action performed.
        original_task_id: The task ID of the initiating scan_request_task.
        current_task_id: The task ID of the verdict_task (optional).

    """
    global _syslog_handler
    if not _syslog_handler:
        dsx_logging.warning("Syslog handler not initialized, skipping log")
        return

    try:
        log_data = {
            "original_task_id": original_task_id,
            "current_task_id": current_task_id,
            "timestamp": datetime.utcnow().isoformat(),
            "scan_request": scan_result.scan_request.model_dump(),
            "verdict": scan_result.verdict.model_dump(),
            "item_action": scan_result.item_action.model_dump()
        }
        syslog_message = json.dumps(jsonable_encoder(log_data))
        syslog_logger.info(syslog_message)

        dsx_logging.debug(f"Sent verdict chain to syslog: {syslog_message}")
    except Exception as e:
        dsx_logging.error(f"Failed to log verdict chain to syslog: {e}", exc_info=True)
