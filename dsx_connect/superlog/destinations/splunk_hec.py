"""
Library-based destinations using proven third-party libraries.

Uses splunk-handler for Splunk HEC and azure-monitor-ingestion for Azure Sentinel.
This approach leverages battle-tested libraries while maintaining framework integration.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

try:
    import splunk_handler
    SPLUNK_HANDLER_AVAILABLE = True
except ImportError:
    SPLUNK_HANDLER_AVAILABLE = False

from ..core.destination import LogDestination, LogFormatter
from ..core.events import LogEvent, LogLevel
from dsx_connect.utils.app_logging import dsx_logging


class SplunkHECDestination(LogDestination):
    """
    Splunk HEC destination using the splunk-handler library.

    This wraps splunk_handler.SplunkHandler to provide async compatibility
    and integration with the DSX-Connect logging framework.
    """

    def __init__(self,
                 formatter: LogFormatter,
                 name: str,
                 host: str,
                 token: str,
                 port: int = 8088,
                 index: str = "main",
                 source: str = "dsx-connect",
                 sourcetype: str = "dsx:scan:result",
                 verify: bool = True,
                 timeout: float = 60.0,
                 flush_interval: float = 15.0,
                 queue_size: int = 5000,
                 retry_count: int = 5,
                 retry_backoff: float = 2.0):
        """
        Initialize Splunk HEC destination using splunk-handler.

        Args:
            formatter: LogFormatter instance
            name: Destination name
            host: Splunk server hostname
            token: HEC token
            port: HEC port (usually 8088)
            index: Target Splunk index
            source: Event source field
            sourcetype: Event sourcetype field
            verify: Verify SSL certificates
            timeout: HTTP timeout in seconds
            flush_interval: Batch flush interval (0 for immediate)
            queue_size: Maximum queue size (0 for unlimited)
            retry_count: Number of retry attempts
            retry_backoff: Retry backoff factor
        """
        if not SPLUNK_HANDLER_AVAILABLE:
            raise ImportError(
                "splunk-handler is required for SplunkHECDestination. "
                "Install with: pip install splunk-handler"
            )

        super().__init__(formatter, name)

        self.host = host
        self.token = token
        self.port = port
        self.index = index
        self.source = source
        self.sourcetype = sourcetype

        # Initialize splunk-handler logger
        self._splunk_logger = None
        self._handler = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="splunk")

        # Splunk handler configuration
        self.splunk_config = {
            "host": host,
            "port": port,
            "token": token,
            "index": index,
            "source": source,
            "sourcetype": sourcetype,
            "verify": verify,
            "timeout": timeout,
            "flush_interval": flush_interval,
            "queue_size": queue_size,
            "retry_count": retry_count,
            "retry_backoff": retry_backoff,
            "record_format": True  # Send as structured JSON
        }

        # Map LogLevel to Python logging levels
        self.level_map = {
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.CRITICAL: logging.CRITICAL
        }

    def _initialize_splunk_handler(self):
        """Initialize splunk-handler logger lazily"""
        if self._splunk_logger is None:
            # Create dedicated logger for this destination
            logger_name = f"splunk.{self.name}"
            self._splunk_logger = logging.getLogger(logger_name)
            self._splunk_logger.setLevel(logging.DEBUG)

            # Clear any existing handlers
            self._splunk_logger.handlers.clear()
            self._splunk_logger.propagate = False

            # Create splunk handler
            try:
                self._handler = splunk_handler.SplunkHandler(**self.splunk_config)

                # Set formatter to pass through formatted events
                self._handler.setFormatter(logging.Formatter('%(message)s'))

                self._splunk_logger.addHandler(self._handler)

                dsx_logging.info(f"Initialized Splunk HEC destination: {self.name} -> {self.host}:{self.port}")

            except Exception as e:
                dsx_logging.error(f"Failed to initialize Splunk handler: {e}")
                raise

    async def send(self, event: LogEvent) -> bool:
        """Send event to Splunk via splunk-handler"""
        if not self.formatter.validate_event(event):
            return False

        try:
            # Initialize splunk handler if needed
            if self._splunk_logger is None:
                self._initialize_splunk_handler()

            # Format the event
            formatted_message = self.formatter.format(event)

            # Convert LogLevel to Python logging level
            log_level = self.level_map.get(event.severity, logging.INFO)

            # Create log record with extra Splunk fields
            extra = self._build_splunk_extra(event)

            # Send to splunk-handler in thread pool to avoid blocking
            await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._splunk_logger.log(
                    log_level,
                    formatted_message,
                    extra=extra
                )
            )

            self._stats["events_sent"] += 1
            return True

        except Exception as e:
            dsx_logging.error(f"Splunk HEC send failed: {e}")
            self._stats["events_failed"] += 1
            self._stats["last_error"] = str(e)
            return False

    def _build_splunk_extra(self, event: LogEvent) -> Dict[str, Any]:
        """Build extra fields for Splunk log record"""
        extra = {}

        # Override default fields if needed
        if event.source and event.source != self.source:
            extra["_source"] = event.source

        # Add event-specific sourcetype if available
        if event.event_type:
            extra["_sourcetype"] = f"dsx:{event.event_type.value}"

        # Add custom fields that Splunk can index
        if event.task_id:
            extra["task_id"] = event.task_id
        if event.connector_name:
            extra["connector"] = event.connector_name
        if event.verdict:
            extra["verdict"] = event.verdict
        if event.threat_name:
            extra["threat"] = event.threat_name

        return extra

    async def close(self):
        """Close splunk handler and cleanup"""
        try:
            if self._handler:
                # Force flush any pending logs
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    lambda: splunk_handler.force_flush()
                )

                # Close the handler
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    self._handler.close
                )

        except Exception as e:
            dsx_logging.error(f"Error closing Splunk handler: {e}")

        finally:
            # Shutdown thread pool
            self._executor.shutdown(wait=True)
            dsx_logging.debug(f"Closed Splunk HEC destination: {self.name}")

