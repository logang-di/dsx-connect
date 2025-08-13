from typing import Optional

from dsx_connect.superlog.core.formatter import LogFormatter


class SyslogFormatter(LogFormatter):
    """
    RFC 3164/5424 compliant syslog formatter.

    Creates properly formatted syslog messages with priority calculation,
    timestamp formatting, and structured data support.
    """

    def __init__(self,
                 facility: SyslogFacility = SyslogFacility.LOCAL0,
                 hostname: Optional[str] = None,
                 app_name: str = "dsx-connect",
                 use_rfc5424: bool = True,
                 include_structured_data: bool = True):
        """
        Initialize syslog formatter.

        Args:
            facility: Syslog facility to use
            hostname: Hostname to include in messages (auto-detected if None)
            app_name: Application name for syslog messages
            use_rfc5424: Use RFC 5424 format (recommended) vs RFC 3164
            include_structured_data: Include structured data elements (RFC 5424 only)
        """
        self.facility = facility
        self.hostname = hostname or socket.gethostname()
        self.app_name = app_name
        self.use_rfc5424 = use_rfc5424
        self.include_structured_data = include_structured_data

        # Map LogLevel to SyslogSeverity
        self.severity_map = {
            LogLevel.DEBUG: SyslogSeverity.DEBUG,
            LogLevel.INFO: SyslogSeverity.INFO,
            LogLevel.WARNING: SyslogSeverity.WARNING,
            LogLevel.ERROR: SyslogSeverity.ERROR,
            LogLevel.CRITICAL: SyslogSeverity.CRITICAL
        }

    def format(self, event: LogEvent) -> str:
        """Format LogEvent as syslog message"""
        if self.use_rfc5424:
            return self._format_rfc5424(event)
        else:
            return self._format_rfc3164(event)

    def _calculate_priority(self, event: LogEvent) -> int:
        """Calculate syslog priority value (facility * 8 + severity)"""
        severity = self.severity_map.get(event.severity, SyslogSeverity.INFO)
        return self.facility.value * 8 + severity.value

    def _format_rfc5424(self, event: LogEvent) -> str:
        """Format as RFC 5424 syslog message"""
        priority = self._calculate_priority(event)

        # Parse timestamp
        try:
            dt = datetime.fromisoformat(event.timestamp.replace('Z', '+00:00'))
            timestamp = dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        except:
            timestamp = event.timestamp

        # Build basic message
        parts = [
            f"<{priority}>1",  # Priority + Version
            timestamp,
            self.hostname,
            self.app_name,
            event.task_id or "-",  # Process ID
            event.event_type.value,  # Message ID
        ]

        # Add structured data if enabled
        if self.include_structured_data:
            structured_data = self._build_structured_data(event)
            parts.append(structured_data)
        else:
            parts.append("-")

        # Build message text
        msg_parts = []
        if event.threat_name:
            msg_parts.append(f"Threat: {event.threat_name}")
        if event.verdict:
            msg_parts.append(f"Verdict: {event.verdict}")
        if event.file_location:
            msg_parts.append(f"File: {event.file_location}")
        if event.action_taken:
            msg_parts.append(f"Action: {event.action_taken}")

        message = " | ".join(msg_parts) if msg_parts else f"{event.event_type.value} event"
        parts.append(message)

        return " ".join(parts)

    def _format_rfc3164(self, event: LogEvent) -> str:
        """Format as RFC 3164 (traditional) syslog message"""
        priority = self._calculate_priority(event)

        # Parse timestamp for RFC 3164 format
        try:
            dt = datetime.fromisoformat(event.timestamp.replace('Z', '+00:00'))
            timestamp = dt.strftime('%b %d %H:%M:%S')
        except:
            # Fallback to current time
            timestamp = datetime.now().strftime('%b %d %H:%M:%S')

        # Build message
        tag = f"{self.app_name}[{event.task_id or 'unknown'}]:"

        msg_parts = []
        if event.threat_name:
            msg_parts.append(f"THREAT={event.threat_name}")
        if event.verdict:
            msg_parts.append(f"VERDICT={event.verdict}")
        if event.file_location:
            msg_parts.append(f"FILE={event.file_location}")
        if event.connector_name:
            msg_parts.append(f"CONNECTOR={event.connector_name}")
        if event.action_taken:
            msg_parts.append(f"ACTION={event.action_taken}")

        message = " ".join(msg_parts) if msg_parts else f"{event.event_type.value}"

        return f"<{priority}>{timestamp} {self.hostname} {tag} {message}"

    def _build_structured_data(self, event: LogEvent) -> str:
        """Build RFC 5424 structured data elements"""
        elements = []

        # Core DSX-Connect structured data
        dsx_data = []
        if event.connector_name:
            dsx_data.append(f'connector="{event.connector_name}"')
        if event.verdict:
            dsx_data.append(f'verdict="{event.verdict}"')
        if event.threat_name:
            dsx_data.append(f'threat="{event.threat_name}"')
        if event.action_taken:
            dsx_data.append(f'action="{event.action_taken}"')
        if event.confidence_score is not None:
            dsx_data.append(f'confidence="{event.confidence_score}"')

        if dsx_data:
            elements.append(f'[dsx@32473 {" ".join(dsx_data)}]')

        # File information
        file_data = []
        if event.file_location:
            # Escape quotes in file paths
            escaped_path = event.file_location.replace('"', '\\"')
            file_data.append(f'path="{escaped_path}"')
        if event.file_hash:
            file_data.append(f'hash="{event.file_hash}"')
        if event.file_size:
            file_data.append(f'size="{event.file_size}"')

        if file_data:
            elements.append(f'[file@32473 {" ".join(file_data)}]')

        # Custom fields
        if event.custom_fields:
            custom_data = []
            for key, value in event.custom_fields.items():
                escaped_value = str(value).replace('"', '\\"')
                custom_data.append(f'{key}="{escaped_value}"')

            if custom_data:
                elements.append(f'[custom@32473 {" ".join(custom_data)}]')

        return "".join(elements) if elements else "-"

