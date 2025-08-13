# dsx_connect/superlog/core/events.py
from dataclasses import dataclass, field, asdict, fields as dataclass_fields
from enum import IntEnum
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import traceback


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    EVENT = 25
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


def _utcnow(): return datetime.now(timezone.utc)

@dataclass
class LogEvent:
    severity: LogLevel = LogLevel.INFO
    message: Optional[str] = None
    event_type: Optional[str] = None
    ts: datetime = field(default_factory=_utcnow)

    # context
    source: Optional[str] = None
    lineno: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    custom_fields: Dict[str, Any] = field(default_factory=dict)

    # rich (optional)
    task_id: Optional[str] = None
    connector_name: Optional[str] = None
    file_location: Optional[str] = None
    verdict: Optional[str] = None
    threat_name: Optional[str] = None

    # capture formatted exception text if provided
    exception_text: Optional[str] = None

    @classmethod
    def from_message(cls, message: str, severity: LogLevel = LogLevel.INFO, **kwargs) -> "LogEvent":
        known = {f.name for f in dataclass_fields(cls)}
        base_kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}

        for k, v in kwargs.items():
            if k in ("exc_info", "stack_info"):
                # normalize into exception_text; stdlib accepts bool/tuple/Exception for exc_info
                if k == "exc_info" and v:
                    if v is True:
                        txt = traceback.format_exc()
                    elif isinstance(v, tuple) and len(v) == 3:
                        txt = "".join(traceback.format_exception(*v))
                    elif isinstance(v, BaseException):
                        txt = "".join(traceback.format_exception(type(v), v, v.__traceback__))
                    else:
                        txt = None
                    if txt:
                        base_kwargs["exception_text"] = txt
                elif k == "stack_info" and v:
                    base_kwargs["exception_text"] = (base_kwargs.get("exception_text","") + "\n" +
                                                     "".join(traceback.format_stack())).strip()
                continue

            if k in known and k != "custom_fields":
                base_kwargs[k] = v
            else:
                extra[k] = v

        # merge unknowns into custom_fields
        cf = dict(kwargs.get("custom_fields", {}))
        cf.update(extra)
        base_kwargs["custom_fields"] = cf

        return cls(severity=severity, message=message, **base_kwargs)

    def as_text(self) -> str:
        return self.message or ""
