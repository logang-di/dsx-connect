"""
CloudWatch destination using the watchtower library.

This provides a thin wrapper around watchtower to integrate with the
DSX-Connect logging framework while leveraging a mature, battle-tested
CloudWatch implementation.
"""

import asyncio
import logging
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

try:
    import watchtower
    import boto3
    WATCHTOWER_AVAILABLE = True
except ImportError:
    WATCHTOWER_AVAILABLE = False

from ..core.destination import LogDestination, LogFormatter
from ..core.events import LogEvent, LogLevel
from dsx_connect.utils.app_logging import dsx_logging


class CloudWatchDestination(LogDestination):
    """
    CloudWatch destination using the watchtower library.

    This wraps watchtower's CloudWatchLogsHandler to provide async compatibility
    and integration with the DSX-Connect logging framework.
    """

    def __init__(self,
                 formatter: LogFormatter,
                 name: str,
                 log_group: str,
                 log_stream: Optional[str] = None,
                 region: str = "us-east-1",
                 aws_access_key_id: Optional[str] = None,
                 aws_secret_access_key: Optional[str] = None,
                 create_log_group: bool = True,
                 create_log_stream: bool = True,
                 retention_days: Optional[int] = None,
                 max_batch_size: int = 10000,
                 max_batch_count: int = 50,
                 send_interval: int = 60,
                 use_queues: bool = True):
        """
        Initialize CloudWatch destination using watchtower.

        Args:
            formatter: LogFormatter instance
            name: Destination name
            log_group: CloudWatch log group name
            log_stream: CloudWatch log stream name (auto-generated if None)
            region: AWS region
            aws_access_key_id: AWS access key (optional)
            aws_secret_access_key: AWS secret key (optional)
            create_log_group: Create log group if it doesn't exist
            create_log_stream: Create log stream if it doesn't exist
            retention_days: Log group retention in days
            max_batch_size: Maximum batch size in bytes
            max_batch_count: Maximum number of events per batch
            send_interval: Send interval in seconds
            use_queues: Use background thread for sending
        """
        if not WATCHTOWER_AVAILABLE:
            raise ImportError(
                "watchtower and boto3 are required for WatchtowerDestination. "
                "Install with: pip install watchtower boto3"
            )

        super().__init__(formatter, name)

        self.log_group = log_group
        self.log_stream = log_stream
        self.region = region

        # Create boto3 session if credentials provided
        self.session = None
        if aws_access_key_id and aws_secret_access_key:
            self.session = boto3.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                region_name=region
            )

        # Watchtower configuration
        self.watchtower_config = {
            "log_group_name": log_group,
            "log_stream_name": log_stream,
            "create_log_group": create_log_group,
            "create_log_stream": create_log_stream,
            "max_batch_size": max_batch_size,
            "max_batch_count": max_batch_count,
            "send_interval": send_interval,
            "use_queues": use_queues,
            "boto3_session": self.session
        }

        if retention_days:
            self.watchtower_config["log_group_retention_days"] = retention_days

        # Initialize watchtower logger
        self._watchtower_logger = None
        self._handler = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="watchtower")

        # Map LogLevel to Python logging levels
        self.level_map = {
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.CRITICAL: logging.CRITICAL
        }

    def _initialize_watchtower(self):
        """Initialize watchtower logger lazily"""
        if self._watchtower_logger is None:
            # Create dedicated logger for this destination
            logger_name = f"watchtower.{self.name}"
            self._watchtower_logger = logging.getLogger(logger_name)
            self._watchtower_logger.setLevel(logging.DEBUG)

            # Clear any existing handlers
            self._watchtower_logger.handlers.clear()
            self._watchtower_logger.propagate = False

            # Create watchtower handler
            try:
                self._handler = watchtower.CloudWatchLogsHandler(**self.watchtower_config)

                # Set formatter to just pass through the message
                self._handler.setFormatter(logging.Formatter('%(message)s'))

                self._watchtower_logger.addHandler(self._handler)

                dsx_logging.info(f"Initialized watchtower destination: {self.name} -> {self.log_group}")

            except Exception as e:
                dsx_logging.error(f"Failed to initialize watchtower handler: {e}")
                raise

    async def send(self, event: LogEvent) -> bool:
        """Send event to CloudWatch via watchtower"""
        if not self.formatter.validate_event(event):
            return False

        try:
            # Initialize watchtower if needed
            if self._watchtower_logger is None:
                self._initialize_watchtower()

            # Format the event
            formatted_message = self.formatter.format(event)

            # Convert LogLevel to Python logging level
            log_level = self.level_map.get(event.severity, logging.INFO)

            # Create log record with extra context
            extra = self._build_extra_fields(event)

            # Send to watchtower in thread pool to avoid blocking
            await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._watchtower_logger.log(
                    log_level,
                    formatted_message,
                    extra=extra
                )
            )

            self._stats["events_sent"] += 1
            return True

        except Exception as e:
            dsx_logging.error(f"Watchtower send failed: {e}")
            self._stats["events_failed"] += 1
            self._stats["last_error"] = str(e)
            return False

    def _build_extra_fields(self, event: LogEvent) -> Dict[str, Any]:
        """Build extra fields for log record"""
        extra = {}

        # Add event metadata as extra fields (available to CloudWatch Insights)
        if event.task_id:
            extra["task_id"] = event.task_id
        if event.connector_name:
            extra["connector"] = event.connector_name
        if event.file_location:
            extra["file_path"] = event.file_location
        if event.verdict:
            extra["verdict"] = event.verdict
        if event.threat_name:
            extra["threat"] = event.threat_name
        if event.event_type:
            extra["event_type"] = event.event_type.value

        return extra

    async def close(self):
        """Close watchtower handler and cleanup"""
        try:
            if self._handler:
                # Flush any pending logs
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    self._handler.flush
                )

                # Close the handler
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    self._handler.close
                )

        except Exception as e:
            dsx_logging.error(f"Error closing watchtower handler: {e}")

        finally:
            # Shutdown thread pool
            self._executor.shutdown(wait=True)
            dsx_logging.debug(f"Closed watchtower destination: {self.name}")

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics including watchtower-specific info"""
        stats = super().get_stats()

        # Add watchtower-specific stats if available
        if self._handler:
            stats["watchtower"] = {
                "log_group": self.log_group,
                "log_stream": self.log_stream or "auto-generated",
                "region": self.region,
                "handler_class": self._handler.__class__.__name__
            }

        return stats

    #
    # @classmethod
    # def from_env(cls, formatter, name="cloudwatch"):
    #     if not WATCHTOWER_AVAILABLE:
    #         return None  # or raise
    #     import os
    #     return cls(
    #         formatter=formatter,
    #         name=name,
    #         log_group=os.getenv("CLOUDWATCH_LOG_GROUP", "dsx-connect"),
    #         log_stream=os.getenv("CLOUDWATCH_LOG_STREAM"),
    #         region=os.getenv("AWS_REGION", "us-east-1"),
    #         retention_days=int(os.getenv("CLOUDWATCH_RETENTION_DAYS", "30")),
    #         create_log_group=True,
    #         create_log_stream=True,
    #     )