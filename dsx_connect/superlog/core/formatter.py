"""
Abstract base classes for the DSX-Connect logging framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any

from .events import LogEvent
from typing import Optional, Set, Protocol
from abc import ABC, abstractmethod

from dsx_connect.superlog.core.events import LogEvent, LogLevel


class LogFormatter(ABC):
    """
    Abstract base class for log formatters.

    Formatters convert LogEvent objects into strings in various formats
    (JSON, CEF, syslog, etc.) suitable for different destinations.
    """

    @abstractmethod
    def format(self, event: LogEvent) -> str:
        """
        Format a LogEvent into the target format.

        Args:
            event: The LogEvent to format

        Returns:
            Formatted string ready to send to destination
        """
        pass

    def validate_event(self, event: LogEvent) -> bool:
        """
        Optional validation of event before formatting.

        Args:
            event: The LogEvent to validate

        Returns:
            True if event is valid for this formatter
        """
        return True
