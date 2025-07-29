import asyncio
import httpx
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.connector_utils.connector_registration import register_or_refresh_connector_from_redis, unregister_connector_from_redis
from dsx_connect.connector_utils.connector_client import get_async_connector_client
from dsx_connect.models.constants import ConnectorEndpoints


async def heartbeat_all_connectors(app, interval=30):
    while True:
        # Copy the list to avoid concurrent modification
        for conn in list(app.state.connectors):
            try:
                config_url = f"{conn.url}{ConnectorEndpoints.CONFIG}"
                client = await get_async_connector_client(config_url)
                resp = await client.get(config_url)
                resp.raise_for_status()
                await register_or_refresh_connector_from_redis(conn)
                dsx_logging.debug(f"Heartbeat OK for {conn.name} ({conn.uuid})")
            except httpx.HTTPStatusError as http_err:
                status_code = http_err.response.status_code
                if status_code in (401, 403):
                    dsx_logging.warn(f"Heartbeat auth failed for {conn.name} ({conn.uuid}): API key rejected or missing")
                else:
                    dsx_logging.warn(f"Heartbeat HTTP error for {conn.name} ({conn.uuid}): {http_err}")
            except Exception as e:
                dsx_logging.warn(f"Heartbeat failed for {conn.name} ({conn.uuid}): {e}")
                app.state.connectors = [c for c in app.state.connectors if c.uuid != conn.uuid]
                await unregister_connector_from_redis(str(conn.uuid))

        await asyncio.sleep(interval)
