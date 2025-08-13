import io
import logging
import asyncio
from typing import List
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
from dsx_connect.dsxa_client.verdict_models import DPAVerdictModel2
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum
from dsx_connect.utils.app_logging import dsx_logging

CHUNK_SIZE = 1024 * 1024


class DSXAScanRequest:
    def __init__(self, binary_data: io.BytesIO, metadata_info: str = None, protected_entity: str = None):
        self.binary_data = binary_data
        self.metadata_info = metadata_info
        self.protected_entity = protected_entity


class DSXAClientError(Exception):
    """Base exception for DSXA client errors"""
    pass


class DSXAConnectionError(DSXAClientError):
    """Raised when unable to connect to DSXA service"""
    pass


class DSXAServiceError(DSXAClientError):
    """Raised when DSXA service returns an error"""
    pass


class DSXATimeoutError(DSXAClientError):
    """Raised when DSXA service times out"""
    pass


class DSXAClient:
    def __init__(self, scan_binary_url: str,
                 scan_concurrent_connections: int = 5,
                 timeout: int = 600):
        self._scan_binary_url = scan_binary_url
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

    def _handle_connection_error(self, error: Exception, is_async: bool = False) -> Exception:
        """Convert low-level connection errors to more descriptive exceptions"""
        error_str = str(error)
        method_type = "async" if is_async else "sync"

        # Connection refused (service not running)
        if "Connection refused" in error_str or "[Errno 61]" in error_str:
            return DSXAConnectionError(
                f"Unable to connect to DSXA scanner at {self._scan_binary_url}. "
                f"Please verify the scanner service is running and accessible. "
                f"Original error: {error_str}"
            )

        # Name resolution failed (DNS/hostname issues)
        if "Name or service not known" in error_str or "[Errno -2]" in error_str or "getaddrinfo failed" in error_str:
            return DSXAConnectionError(
                f"Cannot resolve hostname for DSXA scanner at {self._scan_binary_url}. "
                f"Please check the URL and network connectivity. "
                f"Original error: {error_str}"
            )

        # Network unreachable
        if "Network is unreachable" in error_str or "[Errno 101]" in error_str:
            return DSXAConnectionError(
                f"Network unreachable to DSXA scanner at {self._scan_binary_url}. "
                f"Please check network connectivity and firewall settings. "
                f"Original error: {error_str}"
            )

        # Connection timeout
        if "timeout" in error_str.lower() or "timed out" in error_str.lower():
            return DSXATimeoutError(
                f"Connection to DSXA scanner at {self._scan_binary_url} timed out. "
                f"The service may be overloaded or slow to respond. "
                f"Original error: {error_str}"
            )

        # SSL/TLS errors
        if "ssl" in error_str.lower() or "certificate" in error_str.lower():
            return DSXAConnectionError(
                f"SSL/TLS error connecting to DSXA scanner at {self._scan_binary_url}. "
                f"Please check certificate configuration. "
                f"Original error: {error_str}"
            )

        # Generic connection error
        return DSXAConnectionError(
            f"Failed to connect to DSXA scanner at {self._scan_binary_url} during {method_type} scan. "
            f"Original error: {error_str}"
        )

    def _handle_http_error(self, error: httpx.HTTPStatusError, is_async: bool = False) -> Exception:
        """Convert HTTP status errors to more descriptive exceptions"""
        status_code = error.response.status_code
        method_type = "async" if is_async else "sync"

        try:
            response_body = error.response.text
        except:
            response_body = "Unable to read response body"

        if status_code == 400:
            return DSXAServiceError(
                f"DSXA scanner rejected the request (HTTP 400 Bad Request). "
                f"The file format or metadata may be invalid. "
                f"URL: {self._scan_binary_url}. Response: {response_body[:200]}"
            )
        elif status_code == 401:
            return DSXAServiceError(
                f"Authentication failed with DSXA scanner (HTTP 401 Unauthorized). "
                f"Please check API credentials. "
                f"URL: {self._scan_binary_url}"
            )
        elif status_code == 403:
            return DSXAServiceError(
                f"Access forbidden to DSXA scanner (HTTP 403 Forbidden). "
                f"Please check API permissions. "
                f"URL: {self._scan_binary_url}"
            )
        elif status_code == 404:
            return DSXAServiceError(
                f"DSXA scanner endpoint not found (HTTP 404 Not Found). "
                f"Please verify the scanner URL is correct. "
                f"URL: {self._scan_binary_url}"
            )
        elif status_code == 413:
            return DSXAServiceError(
                f"File too large for DSXA scanner (HTTP 413 Payload Too Large). "
                f"The file exceeds the maximum size limit. "
                f"URL: {self._scan_binary_url}"
            )
        elif status_code == 429:
            return DSXAServiceError(
                f"Rate limited by DSXA scanner (HTTP 429 Too Many Requests). "
                f"Please reduce scan frequency. "
                f"URL: {self._scan_binary_url}"
            )
        elif 500 <= status_code <= 599:
            return DSXAServiceError(
                f"DSXA scanner internal error (HTTP {status_code}). "
                f"The scanner service may be experiencing issues. "
                f"URL: {self._scan_binary_url}. Response: {response_body[:200]}"
            )
        else:
            return DSXAServiceError(
                f"Unexpected HTTP {status_code} error from DSXA scanner during {method_type} scan. "
                f"URL: {self._scan_binary_url}. Response: {response_body[:200]}"
            )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
    async def reconnect(self):
        """Reinitialize the AsyncClient if the connection is lost."""
        if hasattr(self, "aclient"):
            await self.aclient.aclose()
        self.aclient = httpx.AsyncClient(**self._client_config)
        dsx_logging.info("DSXA AsyncClient connection pool reestablished.")

    async def scan_binaries_async(self, scan_requests: List[DSXAScanRequest]) -> List[DPAVerdictModel2]:
        tasks = [self._scan_binary_async(scan_request) for scan_request in scan_requests]
        return await asyncio.gather(*tasks)

    async def scan_binary_async(self, scan_request: DSXAScanRequest) -> DPAVerdictModel2:
        results = await self.scan_binaries_async([scan_request])
        return results[0]

    def scan_binary(self, scan_request: DSXAScanRequest) -> DPAVerdictModel2:
        """Synchronous version of scan_binary_async with improved error handling."""
        try:
            headers = {}
            if scan_request.protected_entity:
                headers["protected_entity"] = scan_request.protected_entity
            if scan_request.metadata_info:
                # Clean metadata to ensure it's ASCII-safe
                clean_metadata = scan_request.metadata_info.encode("ascii", "ignore").decode("ascii")
                headers["X-Custom-Metadata"] = clean_metadata

            scan_request.binary_data.seek(0)  # Reset stream position
            file_size = len(scan_request.binary_data.getvalue())

            dsx_logging.debug(f"Scanning {file_size} bytes with DSXA at {self._scan_binary_url}")

            response = self.client.post(
                self._scan_binary_url,
                headers=headers,
                content=scan_request.binary_data.read()
            )
            response.raise_for_status()
            verdict = response.json()
            return DPAVerdictModel2(**verdict)

        except httpx.ConnectError as e:
            # Handle connection-level errors (network, DNS, etc.)
            raise self._handle_connection_error(e, is_async=False) from e

        except httpx.TimeoutException as e:
            raise DSXATimeoutError(
                f"DSXA scanner request timed out at {self._scan_binary_url}. "
                f"Try reducing file size or increasing timeout. Original error: {str(e)}"
            ) from e

        except httpx.HTTPStatusError as e:
            # Handle HTTP status errors (400, 500, etc.)
            raise self._handle_http_error(e, is_async=False) from e

        except httpx.RequestError as e:
            # Handle other request errors
            raise DSXAConnectionError(
                f"Request error during sync scan to DSXA at {self._scan_binary_url}. "
                f"Original error: {str(e)}"
            ) from e

        except Exception as e:
            # Handle JSON parsing errors and other unexpected errors
            error_str = str(e)
            if "json" in error_str.lower() or "decode" in error_str.lower():
                raise DSXAServiceError(
                    f"Invalid JSON response from DSXA scanner at {self._scan_binary_url}. "
                    f"The service may be returning an unexpected format. "
                    f"Original error: {error_str}"
                ) from e
            else:
                raise DSXAClientError(
                    f"Unexpected error during sync binary scan to {self._scan_binary_url}. "
                    f"Original error: {error_str}"
                ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
        before_sleep=before_sleep_log(dsx_logging, log_level=logging.WARNING),
    )
    async def _scan_binary_async(self, scan_request: DSXAScanRequest) -> DPAVerdictModel2:
        try:
            headers = {}
            if scan_request.protected_entity:
                headers["protected_entity"] = scan_request.protected_entity
            if scan_request.metadata_info:
                # Clean metadata to ensure it's ASCII-safe
                clean_metadata = scan_request.metadata_info.encode("utf-8", errors="ignore").decode("ascii", errors="ignore")
                headers["X-Custom-Metadata"] = clean_metadata

            scan_request.binary_data.seek(0)  # Reset the stream position just in case
            file_size = len(scan_request.binary_data.getvalue())

            dsx_logging.debug(f"Async scanning {file_size} bytes with DSXA at {self._scan_binary_url}")

            response = await self.aclient.post(
                self._scan_binary_url,
                headers=headers,
                content=scan_request.binary_data.read()
            )

            response.raise_for_status()
            verdict = response.json()
            dpa_verdict = DPAVerdictModel2(**verdict)
            return dpa_verdict

        except httpx.ConnectError as e:
            # Handle connection-level errors (network, DNS, etc.)
            raise self._handle_connection_error(e, is_async=True) from e

        except httpx.TimeoutException as e:
            raise DSXATimeoutError(
                f"DSXA scanner async request timed out at {self._scan_binary_url}. "
                f"Try reducing file size or increasing timeout. Original error: {str(e)}"
            ) from e

        except httpx.HTTPStatusError as e:
            # Handle HTTP status errors (400, 500, etc.)
            raise self._handle_http_error(e, is_async=True) from e

        except httpx.RequestError as e:
            # Handle other request errors
            raise DSXAConnectionError(
                f"Request error during async scan to DSXA at {self._scan_binary_url}. "
                f"Original error: {str(e)}"
            ) from e

        except Exception as e:
            # Handle JSON parsing errors and other unexpected errors
            error_str = str(e)
            if "json" in error_str.lower() or "decode" in error_str.lower():
                raise DSXAServiceError(
                    f"Invalid JSON response from DSXA scanner at {self._scan_binary_url}. "
                    f"The service may be returning an unexpected format. "
                    f"Original error: {error_str}"
                ) from e
            else:
                raise DSXAClientError(
                    f"Unexpected error during async binary scan to {self._scan_binary_url}. "
                    f"Original error: {error_str}"
                ) from e

    async def test_connection_async(self) -> StatusResponse:
        try:
            test_data = DSXAScanRequest(
                binary_data=io.BytesIO(b'This is a test file for connection verification'),
                metadata_info="connection-test"
            )
            response = await self.scan_binary_async(scan_request=test_data)
            return StatusResponse(
                status=StatusResponseEnum.SUCCESS,
                message=f"Successfully connected to DSXA scanner at {self._scan_binary_url}"
            )
        except (DSXAConnectionError, DSXATimeoutError, DSXAServiceError, DSXAClientError) as e:
            dsx_logging.error(f"DSXA connection test failed: {str(e)}")
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                message=f"DSXA connection test failed: {str(e)}"
            )
        except Exception as e:
            dsx_logging.error(f"Unexpected error testing DSXA connection: {str(e)}")
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                message=f"Unexpected error testing DSXA connection: {str(e)}"
            )

    def test_connection_sync(self) -> StatusResponse:
        """Synchronous version of connection test"""
        try:
            test_data = DSXAScanRequest(
                binary_data=io.BytesIO(b'This is a test file for connection verification'),
                metadata_info="connection-test"
            )
            response = self.scan_binary(scan_request=test_data)
            return StatusResponse(
                status=StatusResponseEnum.SUCCESS,
                message=f"Successfully connected to DSXA scanner at {self._scan_binary_url}"
            )
        except (DSXAConnectionError, DSXATimeoutError, DSXAServiceError, DSXAClientError) as e:
            dsx_logging.error(f"DSXA sync connection test failed: {str(e)}")
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                message=f"DSXA connection test failed: {str(e)}"
            )
        except Exception as e:
            dsx_logging.error(f"Unexpected error testing DSXA sync connection: {str(e)}")
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                message=f"Unexpected error testing DSXA connection: {str(e)}"
            )


# Define the main function for testing the class
def main():
    logging.basicConfig(level=logging.DEBUG)
    scan_binary_url = "https://localhost/scan/binary/v2"

    # Create a test binary content
    test_binary = io.BytesIO(b"This is a test binary content.")

    async def test_scan():
        async with DSXAClient(scan_binary_url=scan_binary_url) as client:
            try:
                result = await client._scan_binary_async(
                    scan_request=DSXAScanRequest(
                        binary_data=test_binary,
                        metadata_info="something2"
                    )
                )
                logging.info(f"Scan result: {result}")
            except (DSXAConnectionError, DSXATimeoutError, DSXAServiceError, DSXAClientError) as e:
                logging.error(f"DSXA error during scan: {e}")
            except Exception as e:
                logging.error(f"Unexpected error during scan: {e}")

    asyncio.run(test_scan())


if __name__ == "__main__":
    main()