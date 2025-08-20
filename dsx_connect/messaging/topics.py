from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

try:
    from dsx_connect.config import get_config
    ENV = str(get_config().app_env.value)  # "dev" | "stg" | "prod"
except Exception:
    ENV = "dev"

try:
    from shared.routes import SERVICE_SLUG  # "dsx-connect"
except Exception:
    SERVICE_SLUG = "dsx-connect"

NS = f"{ENV}:{SERVICE_SLUG}"

def _topic(*parts: str) -> str:
    return ":".join((ENV, SERVICE_SLUG, *parts))

class Topics(StrEnum):
    # Internal registry bus (cache warm/evict)
    REGISTRY_CONNECTORS = _topic("registry", "connectors")

    # UI-facing notification buses (SSE)
    NOTIFY_CONNECTORS   = _topic("notify", "connectors")
    NOTIFY_SCAN_RESULT  = _topic("notify", "scan_results")
    NOTIFY_DLQ          = _topic("notify", "dlq")

@dataclass(frozen=True)
class Keys:
    CONNECTOR_PRESENCE_BASE: Final[str] = _topic("connectors", "presence")
    CONNECTOR_CONFIG_BASE:   Final[str] = _topic("connectors", "config")

    @staticmethod
    def presence(uuid: str) -> str:
        return f"{Keys.CONNECTOR_PRESENCE_BASE}:{uuid}"

    @staticmethod
    def config(uuid: str) -> str:
        return f"{Keys.CONNECTOR_CONFIG_BASE}:{uuid}"

@dataclass(frozen=True)
class DLQKeys:
    """Dead Letter Queue naming patterns."""
    DLQ_BASE: Final[str] = f"{NS}:dlq"

    # DLQ queue types (matches DeadLetterType enum in dead_letter.py)
    SCAN_REQUEST: Final[str] = f"{NS}:dlq:scan_request"
    VERDICT_ACTION: Final[str] = f"{NS}:dlq:verdict_action"
    SCAN_RESULT: Final[str] = f"{NS}:dlq:scan_result"

    @staticmethod
    def queue_name(queue_type: str) -> str:
        """Generate DLQ queue name for any type."""
        return f"{NS}:dlq:{queue_type}"

    @staticmethod
    def all_queues() -> list[str]:
        """Get all predefined DLQ queue names."""
        return [
            DLQKeys.SCAN_REQUEST,
            DLQKeys.VERDICT_ACTION,
            DLQKeys.SCAN_RESULT
        ]