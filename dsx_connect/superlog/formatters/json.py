"""
JSON formatter for DSX-Connect logging framework.

This is the most flexible and widely supported format, suitable for
modern SIEM systems, Splunk, Elasticsearch, and debugging.
"""

import json
from typing import Any, Dict

from ..core.destination import LogFormatter
from ..core.events import LogEvent


class JSONFormatter(LogFormatter):
    """
    JSON format formatter - most flexible and widely supported.

    This replaces the ad-hoc JSON serialization from the old log_chain.py
    with a structured, configurable approach.
    """

    def __init__(self,
                 include_raw_data: bool = True,
                 include_null_fields: bool = False,
                 pretty_print: bool = False,
                 custom_fields: Dict[str, Any] = None):
        """
        Initialize JSON formatter with options.

        Args:
            include_raw_data: Whether to include the raw_data field (large but useful for debugging)
            include_null_fields: Whether to include fields with None values
            pretty_print: Whether to format JSON with indentation (useful for debugging)
            custom_fields: Additional fields to add to every event
        """
        self.include_raw_data = include_raw_data
        self.include_null_fields = include_null_fields
        self.pretty_print = pretty_print
        self.custom_fields = custom_fields or {}

    def format(self, event: LogEvent) -> str:
        """
        Format a LogEvent as JSON string.

        This produces a clean, structured JSON format that's easy to parse
        by downstream systems like Splunk, Elasticsearch, or custom analytics.
        """
        # Start with the event data
        data = event.to_dict()

        # Add custom fields
        if self.custom_fields:
            data.update(self.custom_fields)

        # Handle raw_data inclusion
        if not self.include_raw_data:
            data.pop('raw_data', None)

        # Handle null fields
        if not self.include_null_fields:
            data = {k: v for k, v in data.items() if v is not None}

        # Serialize to JSON
        return json.dumps(
            data,
            ensure_ascii=False,
            indent=2 if self.pretty_print else None,
            separators=(',', ': ') if self.pretty_print else (',', ':'),
            default=self._json_serializer
        )

    def _json_serializer(self, obj: Any) -> Any:
        """
        Custom JSON serializer for objects that aren't natively JSON-serializable.

        This handles common types that might appear in LogEvent data.
        """
        # Handle datetime objects
        if hasattr(obj, 'isoformat'):
            return obj.isoformat()

        # Handle Pydantic models
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()

        # Handle dataclasses
        if hasattr(obj, '__dataclass_fields__'):
            from dataclasses import asdict
            return asdict(obj)

        # Handle enums
        if hasattr(obj, 'value'):
            return obj.value

        # Fallback to string representation
        return str(obj)

    def validate_event(self, event: LogEvent) -> bool:
        """Validate that the event can be JSON serialized"""
        try:
            self.format(event)
            return True
        except (TypeError, ValueError):
            return False


class CompactJSONFormatter(JSONFormatter):
    """
    Compact JSON formatter for high-volume logging scenarios.

    Removes optional fields and raw data to minimize log size while
    retaining essential security information.
    """

    def __init__(self, **kwargs):
        # Override defaults for compact output
        kwargs.setdefault('include_raw_data', False)
        kwargs.setdefault('include_null_fields', False)
        kwargs.setdefault('pretty_print', False)
        super().__init__(**kwargs)

        # Fields to exclude for compactness
        self.excluded_fields = {
            'custom_fields', 'correlation_id', 'user_agent',
            'client_ip', 'api_endpoint'
        }

    def format(self, event: LogEvent) -> str:
        """Format event with minimal fields for high-volume scenarios"""
        data = event.to_dict()

        # Remove excluded fields
        for field in self.excluded_fields:
            data.pop(field, None)

        # Add custom fields if any
        if self.custom_fields:
            data.update(self.custom_fields)

        return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


class StructuredJSONFormatter(JSONFormatter):
    """
    Structured JSON formatter that organizes fields into logical groups.

    This creates a more organized JSON structure that's easier to query
    in systems like Elasticsearch or Splunk.
    """

    def format(self, event: LogEvent) -> str:
        """Format event with structured field organization"""
        data = event.to_dict()

        # Reorganize into logical groups
        structured_data = {
            # Core event metadata
            "event": {
                "timestamp": data.pop("timestamp"),
                "type": data.pop("event_type"),
                "severity": data.pop("severity"),
                "source": data.pop("source")
            },

            # Task/request context
            "context": {
                k: data.pop(k) for k in [
                    "task_id", "original_task_id", "correlation_id"
                ] if k in data and data[k] is not None
            },

            # File/scan information
            "file": {
                k: data.pop(k) for k in [
                    "file_location", "file_hash", "file_size", "connector_name"
                ] if k in data and data[k] is not None
            },

            # Security analysis
            "security": {
                k: data.pop(k) for k in [
                    "verdict", "threat_name", "threat_category", "confidence_score"
                ] if k in data and data[k] is not None
            },

            # Action taken
            "action": {
                k: data.pop(k) for k in [
                    "action_taken", "action_status", "action_details"
                ] if k in data and data[k] is not None
            },

            # User/API context
            "user": {
                k: data.pop(k) for k in [
                    "user_id", "api_endpoint", "client_ip", "user_agent"
                ] if k in data and data[k] is not None
            }
        }

        # Remove empty sections
        structured_data = {k: v for k, v in structured_data.items() if v}

        # Add any remaining fields
        if data:
            structured_data["additional"] = data

        # Add custom fields at the root level
        if self.custom_fields:
            structured_data.update(self.custom_fields)

        return json.dumps(
            structured_data,
            ensure_ascii=False,
            indent=2 if self.pretty_print else None,
            separators=(',', ': ') if self.pretty_print else (',', ':'),
            default=self._json_serializer
        )