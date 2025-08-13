# dsx_connect/logging/core/chain.py
from __future__ import annotations
from typing import List, Any, Union, Mapping
import inspect
import asyncio

from dsx_connect.superlog.core.events import LogEvent, LogLevel
from dsx_connect.superlog.core.destination import LogDestination


class LogChain:
    def __init__(self, name: str):
        self.name = name
        self._destinations: List[LogDestination] = []
        self._pending: set[asyncio.Task] = set()

    def add_destination(self, dest: LogDestination) -> "LogChain":
        self._destinations.append(dest)
        return self

    # Accepts a fully built LogEvent
    def log(self, event: LogEvent) -> None:
        self._fanout(event)

    emit = log  # alias

    def _log_message(
            self,
            severity: LogLevel,
            message: Union[str, Mapping[str, Any]],
            *,
            capture_source: bool = True,
            stacklevel: int = 2,
            **fields: Any,
    ) -> None:
        """
        Build a LogEvent from a simple message (or dict) and emit it.
        - message: str -> event.message
                   mapping -> goes into event.custom_fields and optional 'message' key becomes text
        - capture_source: optionally fill source/lineno using the callsite
        - stacklevel: how many frames up to look for the callsite
        """
        if isinstance(message, Mapping):
            text = str(message.get("message", "")) if "message" in message else ""
            custom = dict(message)
            if "message" in custom:
                del custom["message"]
            fields.setdefault("custom_fields", {}).update(custom)
        else:
            text = str(message)

        if capture_source:
            try:
                frame = inspect.stack()[stacklevel]
                filename = frame.filename.rsplit("/", 1)[-1]
                lineno = frame.lineno
                fields.setdefault("source", filename)
                fields.setdefault("lineno", lineno)
            except Exception:
                pass

        event = LogEvent.from_message(text, severity=severity, **fields)
        self._fanout(event)

    # stdlib-like helpers
    def debug(self, message: Union[str, Mapping[str, Any]], **fields: Any) -> None:
        self._log_message(LogLevel.DEBUG, message, **fields)

    def info(self, message: Union[str, Mapping[str, Any]], **fields: Any) -> None:
        self._log_message(LogLevel.INFO, message, **fields)

    def warning(self, message: Union[str, Mapping[str, Any]], **fields: Any) -> None:
        self._log_message(LogLevel.WARNING, message, **fields)

    # alias for legacy code
    def warn(self, message, **fields):
        # optional: emit a one-time deprecation log to console if you want
        self.warning(message, **fields)

    def error(self, message: Union[str, Mapping[str, Any]], **fields: Any) -> None:
        self._log_message(LogLevel.ERROR, message, **fields)

    def critical(self, message: Union[str, Mapping[str, Any]], **fields: Any) -> None:
        self._log_message(LogLevel.CRITICAL, message, **fields)

    def event(self, event_or_message: Union[LogEvent, str, Mapping[str, Any]], **fields: Any) -> None:
        if isinstance(event_or_message, LogEvent):
            evt = event_or_message
            if evt.severity != LogLevel.EVENT:
                evt.severity = LogLevel.EVENT
        else:
            # Treat str/dict the same as above, but force EVENT level
            self._log_message(LogLevel.EVENT, event_or_message, **fields)
            return
        self._fanout(evt)

    def _fanout(self, event: LogEvent) -> None:
        if not self._destinations:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._fanout_async(event))
            return
        task = loop.create_task(self._fanout_async(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _fanout_async(self, event: LogEvent) -> None:
        await asyncio.gather(
            *[dest.send(event) for dest in self._destinations],
            return_exceptions=True,
        )
        # sync destinations already executed inside send(); nothing to await for them

    async def close(self) -> None:
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
            self._pending.clear()
        await asyncio.gather(
            *[dest.close() for dest in self._destinations],
            return_exceptions=True,
        )

