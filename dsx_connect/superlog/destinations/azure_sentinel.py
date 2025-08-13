import asyncio
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

try:
    from azure.identity import DefaultAzureCredential
    from azure.monitor.ingestion import LogsIngestionClient
    from azure.core.exceptions import HttpResponseError
    AZURE_MONITOR_AVAILABLE = True
except ImportError:
    AZURE_MONITOR_AVAILABLE = False

from ..core.destination import LogDestination, LogFormatter
from ..core.events import LogEvent, LogLevel
from dsx_connect.utils.app_logging import dsx_logging


class AzureSentinelDestination(LogDestination):
    """
    Azure Sentinel destination using the azure-monitor-ingestion library.
    
    Sends events to Azure Sentinel via the Data Collection Rules API.
    This is the official Microsoft-recommended approach for custom logs.
    """

    def __init__(self,
                 formatter: LogFormatter,
                 name: str,
                 data_collection_endpoint: str,
                 data_collection_rule_id: str,
                 stream_name: str,
                 credential = None,
                 batch_size: int = 25,
                 batch_timeout: float = 60.0):
        """
        Initialize Azure Sentinel destination.
        
        Args:
            formatter: LogFormatter instance
            name: Destination name
            data_collection_endpoint: Azure DCE endpoint URL
            data_collection_rule_id: Data Collection Rule ID
            stream_name: Stream name in the DCR
            credential: Azure credential (DefaultAzureCredential if None)
            batch_size: Events per batch
            batch_timeout: Batch timeout in seconds
        """
        if not AZURE_MONITOR_AVAILABLE:
            raise ImportError(
                "azure-monitor-ingestion and azure-identity are required for AzureSentinelDestination. "
                "Install with: pip install azure-monitor-ingestion azure-identity"
            )

        super().__init__(formatter, name)

        self.data_collection_endpoint = data_collection_endpoint
        self.data_collection_rule_id = data_collection_rule_id
        self.stream_name = stream_name
        self.credential = credential or DefaultAzureCredential()
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout

        # Azure Monitor client
        self._client = None
        self._client_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="azure")

        # Batching
        self._event_batch = []
        self._batch_lock = asyncio.Lock()
        self._batch_timer = None

    async def _get_client(self):
        """Get or create Azure Monitor client"""
        async with self._client_lock:
            if self._client is None:
                self._client = LogsIngestionClient(
                    endpoint=self.data_collection_endpoint,
                    credential=self.credential,
                    logging_enable=True
                )
                dsx_logging.info(f"Initialized Azure Monitor client for {self.name}")

            return self._client

    async def send(self, event: LogEvent) -> bool:
        """Add event to batch for Azure Sentinel"""
        if not self.formatter.validate_event(event):
            return False

        # Convert formatted JSON to dict for Azure API
        try:
            import json
            event_data = json.loads(self.formatter.format(event))

            # Ensure TimeGenerated field exists (required by Azure)
            if "timestamp" in event_data:
                event_data["TimeGenerated"] = event_data["timestamp"]
            else:
                event_data["TimeGenerated"] = datetime.utcnow().isoformat() + "Z"

        except (json.JSONDecodeError, ValueError) as e:
            dsx_logging.error(f"Failed to parse event JSON for Azure: {e}")
            return False

        async with self._batch_lock:
            self._event_batch.append(event_data)

            if len(self._event_batch) >= self.batch_size:
                return await self._send_batch()

            if not self._batch_timer:
                self._batch_timer = asyncio.create_task(self._batch_timeout_handler())

            return True

    async def _batch_timeout_handler(self):
        """Handle batch timeout"""
        try:
            await asyncio.sleep(self.batch_timeout)
            async with self._batch_lock:
                if self._event_batch:
                    await self._send_batch()
        except asyncio.CancelledError:
            pass
        finally:
            self._batch_timer = None

    async def _send_batch(self) -> bool:
        """Send batch to Azure Sentinel"""
        if not self._event_batch:
            return True

        batch_data = self._event_batch.copy()
        self._event_batch.clear()

        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None

        try:
            client = await self._get_client()

            # Send to Azure Monitor in thread pool
            await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: client.upload(
                    rule_id=self.data_collection_rule_id,
                    stream_name=self.stream_name,
                    logs=batch_data
                )
            )

            self._stats["events_sent"] += len(batch_data)
            dsx_logging.debug(f"Sent {len(batch_data)} events to Azure Sentinel")
            return True

        except HttpResponseError as e:
            dsx_logging.error(f"Azure Sentinel HTTP error: {e}")
            self._stats["events_failed"] += len(batch_data)
            self._stats["last_error"] = f"HTTP error: {e}"
            return False

        except Exception as e:
            dsx_logging.error(f"Failed to send to Azure Sentinel: {e}")
            self._stats["events_failed"] += len(batch_data)
            self._stats["last_error"] = str(e)
            return False

    async def close(self):
        """Close and cleanup"""
        async with self._batch_lock:
            if self._event_batch:
                await self._send_batch()

        if self._batch_timer:
            self._batch_timer.cancel()

        # Azure client cleanup
        async with self._client_lock:
            if self._client:
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    self._client.close
                )

        self._executor.shutdown(wait=True)
        dsx_logging.debug(f"Closed Azure Sentinel destination: {self.name}")


def create_azure_sentinel_destination(data_collection_endpoint: str,
                                      data_collection_rule_id: str,
                                      stream_name: str,
                                      formatter: LogFormatter,
                                      name: str = "azure_sentinel",
                                      **kwargs) -> AzureSentinelDestination:
    """Create Azure Sentinel destination with sensible defaults"""
    return AzureSentinelDestination(
        formatter=formatter,
        name=name,
        data_collection_endpoint=data_collection_endpoint,
        data_collection_rule_id=data_collection_rule_id,
        stream_name=stream_name,
        **kwargs
    )