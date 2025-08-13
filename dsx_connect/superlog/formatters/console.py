from ..core.destination import LogDestination, LogFormatter
from ..core.events import LogEvent, LogLevel


class StructuredConsoleFormatter(LogFormatter):
    """
    Console-friendly formatter that creates readable structured output.

    This formatter is designed for console output with good readability
    while still maintaining structure for debugging.
    """

    def __init__(self,
                 show_timestamp: bool = True,
                 show_task_id: bool = True,
                 show_connector: bool = True,
                 compact: bool = False):
        """
        Initialize structured console formatter.

        Args:
            show_timestamp: Include timestamp in output
            show_task_id: Include task ID if available
            show_connector: Include connector name if available
            compact: Use compact single-line format
        """
        self.show_timestamp = show_timestamp
        self.show_task_id = show_task_id
        self.show_connector = show_connector
        self.compact = compact

    def format(self, event: LogEvent) -> str:
        """Format event for console output"""
        if self.compact:
            return self._format_compact(event)
        else:
            return self._format_structured(event)

    def _format_compact(self, event: LogEvent) -> str:
        """Create compact single-line format"""
        parts = []

        # Core event info
        if event.event_type:
            parts.append(f"[{event.event_type.value.upper()}]")

        # Context
        if event.connector_name and self.show_connector:
            parts.append(f"connector={event.connector_name}")

        if event.task_id and self.show_task_id:
            parts.append(f"task={event.task_id}")

        # Main message
        if event.event_type.value == "malware_detection" and event.threat_name:
            parts.append(f"THREAT: {event.threat_name}")
        elif event.event_type.value == "scan_result":
            if event.verdict:
                parts.append(f"verdict={event.verdict}")
            if event.file_location:
                parts.append(f"file={event.file_location}")

        # Action taken
        if event.action_taken:
            parts.append(f"action={event.action_taken}")

        return " | ".join(parts)

    def _format_structured(self, event: LogEvent) -> str:
        """Create multi-line structured format"""
        lines = []

        # Header line
        header_parts = [event.event_type.value.upper()]
        if event.connector_name and self.show_connector:
            header_parts.append(f"[{event.connector_name}]")
        if event.task_id and self.show_task_id:
            header_parts.append(f"(task: {event.task_id})")

        lines.append(" ".join(header_parts))

        # Details
        if event.file_location:
            lines.append(f"  File: {event.file_location}")

        if event.verdict:
            lines.append(f"  Verdict: {event.verdict}")

        if event.threat_name:
            lines.append(f"  Threat: {event.threat_name}")

        if event.action_taken:
            lines.append(f"  Action: {event.action_taken} ({event.action_status or 'unknown status'})")

        # Custom fields
        if event.custom_fields:
            for key, value in event.custom_fields.items():
                lines.append(f"  {key}: {value}")

        return "\n".join(lines)

