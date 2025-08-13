
import asyncio
import json
import gzip
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin
import aiohttp
import ssl

from ..core.destination import LogDestination, LogFormatter
from ..core.events import LogEvent
from dsx_connect.utils.app_logging import dsx_logging


class HTTPDestination(LogDestination):
    """
    Generic HTTP endpoint destination.

    Supports sending events to any HTTP endpoint with configurable
    authentication, headers, and payload formatting.
    """

    def __init__(self,
                 formatter: LogFormatter,
                 name: str,
                 url: str,
                 method: str = "POST",
                 headers: Optional[Dict[str, str]] = None,
                 auth_header: Optional[str] = None,
                 verify_ssl: bool = True,
                 timeout: float = 30.0,
                 batch_size: int = 1,
                 batch_timeout: float = 10.0,
                 compress: bool = False):
        """
        Initialize HTTP destination.

        Args:
            formatter: LogFormatter instance
            name: Destination name
            url: HTTP endpoint URL
            method: HTTP method (POST, PUT, etc.)
            headers: Additional HTTP headers
            auth_header: Authorization header value
            verify_ssl: Verify SSL certificates
            timeout: Request timeout
            batch_size: Events per batch (1 = no batching)
            batch_timeout: Batch timeout
            compress: Compress payload with gzip
        """
        super().__init__(formatter, name)

        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.auth_header = auth_header
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.compress = compress

        # Set default headers
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json"

        if self.auth_header:
            self.headers["Authorization"] = self.auth_header

        if self.compress:
            self.headers["Content-Encoding"] = "gzip"

        # Batching
        self._event_batch: List[str] = []
        self._batch_lock = asyncio.Lock()
        self._batch_timer: Optional[asyncio.Task] = None

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

    async def send(self, event: LogEvent) -> bool:
        """Send event via HTTP"""
        if not self.formatter.validate_event(event):
            return False

        formatted_event = self.formatter.format(event)

        if self.batch_size == 1:
            # Send immediately
            return await self._send_single(formatted_event)
        else:
            # Add to batch
            async with self._batch_lock:
                self._event_batch.append(formatted_event)

                if len(self._event_batch) >= self.batch_size:
                    return await self._send_batch()

                if not self._batch_timer:
                    self._batch_timer = asyncio.create_task(self._batch_timeout_handler())

                return True

    async def _send_single(self, event_data: str) -> bool:
        """Send single event"""
        try:
            if not self._session:
                connector = aiohttp.TCPConnector(
                    ssl=ssl.create_default_context() if self.verify_ssl else False
                )
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                self._session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout
                )

            # Prepare payload
            if self.compress:
                payload = gzip.compress(event_data.encode('utf-8'))
            else:
                payload = event_data

            async with self._session.request(
                    self.method,
                    self.url,
                    headers=self.headers,
                    data=payload
            ) as response:

                if 200 <= response.status < 300:
                    self._stats["events_sent"] += 1
                    return True
                else:
                    error_text = await response.text()
                    dsx_logging.error(f"HTTP destination error {response.status}: {error_text}")
                    self._stats["events_failed"] += 1
                    self._stats["last_error"] = f"HTTP {response.status}"
                    return False

        except Exception as e:
            dsx_logging.error(f"HTTP destination send failed: {e}")
            self._stats["events_failed"] += 1
            self._stats["last_error"] = str(e)
            return False

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
        """Send batched events"""
        if not self._event_batch:
            return True

        batch_data = self._event_batch.copy()
        self._event_batch.clear()

        if self._batch_timer:
            self._batch_timer.cancel()
            self._batch_timer = None

        # Format batch as JSON array
        batch_payload = "[" + ",".join(batch_data) + "]"

        success = await self._send_single(batch_payload)
        if success:
            self._stats["events_sent"] += len(batch_data) - 1  # -1 because _send_single already counted 1
        else:
            self._stats["events_failed"] += len(batch_data) - 1

        return success

    async def close(self):
        """Close and cleanup"""
        async with self._batch_lock:
            if self._event_batch:
                await self._send_batch()

        if self._batch_timer:
            self._batch_timer.cancel()

        if self._session:
            await self._session.close()