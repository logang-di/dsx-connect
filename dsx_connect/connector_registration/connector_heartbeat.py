import asyncio
import httpx
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.connector_registration.connector_registration import register_or_refresh_connector_from_redis, unregister_connector_from_redis
from dsx_connect.models.constants import ConnectorEndpoints


async def heartbeat_all_connectors(app, interval=30):
    while True:
        # Copy the list to avoid concurrent modification
        for conn in list(app.state.connectors):
            try:
                config_url = f"{conn.url}{ConnectorEndpoints.CONFIG}"
                async with httpx.AsyncClient(verify=False) as client:
                    resp = await client.get(config_url)
                    resp.raise_for_status()
                    await register_or_refresh_connector_from_redis(conn)
                    dsx_logging.debug(f"Heartbeat OK for {conn.name} ({conn.uuid})")
            except Exception as e:
                dsx_logging.warn(f"Heartbeat failed for {conn.name} ({conn.uuid}): {e}")
                app.state.connectors = [c for c in app.state.connectors if c.uuid != conn.uuid]
                await unregister_connector_from_redis(str(conn.uuid))

        await asyncio.sleep(interval)
