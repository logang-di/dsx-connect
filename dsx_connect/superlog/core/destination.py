"""
Base class for the DSX-Connect logging framework destinations.
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any

from typing import Optional, Set, Protocol
from abc import ABC, abstractmethod
from dsx_connect.superlog.core.events import LogEvent, LogLevel
from dsx_connect.superlog.core.formatter import LogFormatter

def _stdlib_levels():
    # Import inside the function so we only touch logging when it’s fully loaded
    import logging as _logging
    return {
        LogLevel.DEBUG:    _logging.DEBUG,
        LogLevel.INFO:     _logging.INFO,
        LogLevel.WARNING:  _logging.WARNING,
        LogLevel.ERROR:    _logging.ERROR,
        LogLevel.CRITICAL: _logging.CRITICAL,
    }

class LogDestination(ABC):
    def __init__(
            self,
            formatter: LogFormatter,
            name: str,
            *,
            min_level: LogLevel = LogLevel.INFO,
            enabled: bool = True,
            include_levels: Optional[Set[LogLevel]] = None,
            exclude_levels: Optional[Set[LogLevel]] = None,
            allowed_event_types: Optional[Set[str]] = None,
            is_sync: bool = False
    ) -> None:
        self.formatter = formatter
        self.name = name
        self.min_level = min_level
        self.enabled = enabled
        self.include_levels = include_levels
        self.exclude_levels = exclude_levels
        self.allowed_event_types = allowed_event_types
        self.is_sync = is_sync

    def accepts(self, event: LogEvent) -> bool:
        if not self.enabled:
            return False
        lvl = event.severity
        if self.exclude_levels and lvl in self.exclude_levels:
            return False
        if self.include_levels is not None and lvl not in self.include_levels:
            return False
        if lvl.value < self.min_level.value:
            return False
        if self.allowed_event_types is not None:
            if getattr(event, "event_type", None) not in self.allowed_event_types:
                return False
        return True

    def format(self, event: LogEvent) -> str:
        return self.formatter.format(event)

    async def send(self, event: LogEvent) -> bool:
        if not self.accepts(event): return True
        payload = self.format(event); level = LEVEL_MAP.get(event.severity, logging.INFO)
        if self.is_sync:  # fast path
            self.write_sync(level, payload, event)
            return True
        await self._write(level, payload, event)
        return True

    def write_sync(self, level: int, payload: str, event: LogEvent) -> None:
        # default: fall back to async path synchronously
        import asyncio; asyncio.get_event_loop().run_until_complete(self._write(level, payload, event))

    @abstractmethod
    async def _write(self, payload: str, event: LogEvent) -> None:
        """Transport-specific send. """
        ...

    async def close(self) -> None:
        return None
