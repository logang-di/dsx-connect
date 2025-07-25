import io
import logging
import asyncio
from typing import List
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
from dsx_connect.dsxa_client.verdict_models import DPAVerdictModel2
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum
from dsx_connect.utils.logging import dsx_logging

CHUNK_SIZE = 1024 * 1024


class DSXAScanRequest:
    def __init__(self, binary_data: io.BytesIO, metadata_info: str = None, protected_entity: str = None):
        self.binary_data = binary_data
        self.metadata_info = metadata_info
        self.protected_entity = protected_entity


class DSXAClient:
    def __init__(self, scan_binary_url: str,
                 scan_concurrent_connections: int = 5,
                 timeout: int = 600):
        self._scan_binary_url = scan_binary_url
        # self._protected_entity_id = protected_entity_id
        self._scan_concurrent_connections = scan_concurrent_connections
        self._client_config = {
            "timeout": httpx.Timeout(timeout, read=timeout, connect=timeout),
            "limits": httpx.Limits(max_connections=scan_concurrent_connections),
            "verify": False
        }
        self.aclient = httpx.AsyncClient(**self._client_config)
        # Sync client for synchronous methods
        self.client = httpx.Client(**self._client_config)

    async def __aenter__(self):
        if not self.aclient:
            self.aclient = httpx.AsyncClient(**self._client_config)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclient.aclose()

    def __str__(self):
        return f'Scan binary url: {self._scan_binary_url}'

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    async def reconnect(self):
        """Reinitialize the AsyncClient if the connection is lost."""
        if hasattr(self, "aclient"):
            await self.aclient.aclose()
        self.aclient = httpx.AsyncClient(**self._client_config)
        logging.info("DPAClientX connection pool reestablished.")

    async def scan_binaries_async(self, scan_requests: List[DSXAScanRequest]) -> List[DPAVerdictModel2]:
        tasks = [self._scan_binary_async(scan_request) for scan_request in scan_requests]
        return await asyncio.gather(*tasks)

    async def scan_binary_async(self, scan_request: DSXAScanRequest) -> DPAVerdictModel2:
        results = await self.scan_binaries_async([scan_request])
        return results[0]

    def scan_binary(self, scan_request: DSXAScanRequest) -> DPAVerdictModel2:
        """Synchronous version of scan_binary_async."""
        try:
            headers = {}
            if scan_request.protected_entity:
                headers["protected_entity"] = scan_request.protected_entity
            if scan_request.metadata_info:
                headers["X-Custom-Metadata"] = scan_request.metadata_info
            scan_request.binary_data.seek(0)  # Reset stream position

            response = self.client.post(
                self._scan_binary_url,
                headers=headers,
                content=scan_request.binary_data.read()
            )
            response.raise_for_status()
            verdict = response.json()
            return DPAVerdictModel2(**verdict)
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error during sync scan: {e.response.status_code}")
            raise
        except Exception as e:
            logging.error(f"Error during sync binary scan: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
        before_sleep=before_sleep_log(dsx_logging, log_level=logging.WARNING),
    )
    async def _scan_binary_async(self, scan_request: DSXAScanRequest) -> DPAVerdictModel2:
        try:
            headers = {} #"Content-Type": "application/octet-stream"}
            if scan_request.protected_entity:
                headers["protected_entity"] = scan_request.protected_entity
            if scan_request.metadata_info:
                headers["X-Custom-Metadata"] = scan_request.metadata_info.encode("utf-8", errors="ignore").decode("ascii", errors="ignore")
            scan_request.binary_data.seek(0)  # Reset the stream position just in case

            response = await self.aclient.post(
                self._scan_binary_url,
                headers=headers,  # No need for Content-Type, httpx will handle it
                content=scan_request.binary_data.read()  # Use content instead of files
            )

            response.raise_for_status()

            verdict = response.json()
            dpa_verdict = DPAVerdictModel2(**verdict)
            return dpa_verdict
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error during scan: {e.response.status_code}")
            raise
        except Exception as e:
            logging.error(f"Error during binary scan: {e}")
            raise

    async def test_connection_async(self) -> StatusResponse:
        try:
            response = await self.scan_binary_async(scan_request=DSXAScanRequest(binary_data=io.BytesIO(b'This is a test')))
            return StatusResponse(status=StatusResponseEnum.SUCCESS, message=r"Successful DSXA scan of test file.")
        except Exception as e:
            logging.error(f"Error testing connection: {e}")
            return StatusResponse(status=StatusResponseEnum.ERROR, message=str(e))


# Define the main function for testing the class
def main():
    logging.basicConfig(level=logging.DEBUG)
    # scan_binary_url = "http://a668960fee4324868b4154722ad9a909-856481437.us-east-1.elb.amazonaws.com/scan/binary/v2"
    scan_binary_url = "https://localhost/scan/binary/v2"

    # Create a test binary content
    test_binary = io.BytesIO(b"This is a test binary content.")

    async def test_scan():
        async with DSXAClient(scan_binary_url=scan_binary_url) as client:
            try:
                result = await client._scan_binary_async(scan_request=DSXAScanRequest(binary_data=test_binary, metadata_info="something2"))
                logging.info(f"Scan result: {result}")
            except Exception as e:
                logging.error(f"Error during scan: {e}")

    asyncio.run(test_scan())


if __name__ == "__main__":
    main()
