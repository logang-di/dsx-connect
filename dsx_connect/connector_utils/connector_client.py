import asyncio
import threading

import httpx
from typing import Dict
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.models.connector_api_key import APIKeySettings

api_key_settings = APIKeySettings()
_connector_clients: Dict[str, httpx.Client] = {}
_client_pool_lock = threading.Lock()
_async_connector_clients: Dict[str, httpx.AsyncClient] = {}
_async_client_pool_lock = asyncio.Lock()


def get_connector_client(connector_url: str, api_key: str = None) -> httpx.Client:
    """
    Thread-safe retrieval or creation of a cached httpx.Client with optional API key.  If api_key not supplied,
    environment settings/secrets for DSXCONNECTOR_API_KEY will be used
    """
    key = api_key or api_key_settings.api_key
    if not key:
        dsx_logging.warn(f"No API key configured for connector at {connector_url}")

    with _client_pool_lock:
        if connector_url not in _connector_clients:
            headers = {"X-API-Key": key} if key else {}
            client = httpx.Client(headers=headers, verify=False, timeout=30)
            _connector_clients[connector_url] = client
            dsx_logging.debug(f"Created HTTP client for {connector_url} with API key: {bool(key)}")
        return _connector_clients[connector_url]


async def get_async_connector_client(connector_url: str, api_key: str = None) -> httpx.AsyncClient:
    """
    Async-safe retrieval or creation of a cached httpx.AsyncClient with optional API key.
    If api_key not supplied, uses DSXCONNECTOR_API_KEY from environment/secrets.
    """
    key = api_key or api_key_settings.api_key
    if not key:
        dsx_logging.warn(f"No API key configured for connector at {connector_url}")

    async with _async_client_pool_lock:
        if connector_url not in _async_connector_clients:
            headers = {"X-API-Key": key} if key else {}
            client = httpx.AsyncClient(headers=headers, verify=False, timeout=30)
            _async_connector_clients[connector_url] = client
            dsx_logging.debug(f"Created Async HTTP client for {connector_url} with API key: {bool(key)}")
        return _async_connector_clients[connector_url]
