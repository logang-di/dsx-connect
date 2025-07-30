import time
import httpx
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.connector_utils.connector_registration import register_or_refresh_connector_from_redis, unregister_connector_from_redis
from dsx_connect.connector_utils.connector_client import get_connector_client  # Use sync client
from dsx_connect.models.constants import ConnectorEndpoints


def heartbeat_all_connectors(app, interval=30):
    while True:
        # Copy the list to avoid concurrent modification
        connectors_to_check = list(app.state.connectors)
        connectors_to_remove = []

        for conn in connectors_to_check:
            try:
                config_url = f"{conn.url}{ConnectorEndpoints.CONFIG}"
                client = get_connector_client(config_url)
                resp = client.get(config_url)
                resp.raise_for_status()

                # Call sync function directly - this will update Redis and notify frontend
                result = register_or_refresh_connector_from_redis(conn)
                dsx_logging.debug(f"Heartbeat OK for {conn.name} ({conn.uuid}) - {result}")

            except httpx.HTTPStatusError as http_err:
                status_code = http_err.response.status_code
                if status_code in (401, 403):
                    dsx_logging.warn(f"Heartbeat auth failed for {conn.name} ({conn.uuid}): API key rejected or missing")
                    connectors_to_remove.append(conn)
                else:
                    dsx_logging.warn(f"Heartbeat HTTP error for {conn.name} ({conn.uuid}): {http_err}")
                    # For other HTTP errors, try a few times before removing
                    if not hasattr(conn, '_error_count'):
                        conn._error_count = 0
                    conn._error_count += 1

                    if conn._error_count >= 3:  # Remove after 3 consecutive failures
                        dsx_logging.error(f"Removing connector {conn.name} after {conn._error_count} consecutive failures")
                        connectors_to_remove.append(conn)

            except Exception as e:
                dsx_logging.warn(f"Heartbeat failed for {conn.name} ({conn.uuid}): {e}")
                connectors_to_remove.append(conn)

        # Remove failed connectors and send unregister events
        for conn in connectors_to_remove:
            try:
                app.state.connectors = [c for c in app.state.connectors if c.uuid != conn.uuid]
                unregister_connector_from_redis(str(conn.uuid))
                dsx_logging.info(f"Removed failed connector: {conn.name} ({conn.uuid})")
            except Exception as e:
                dsx_logging.error(f"Error removing connector {conn.uuid}: {e}")

        time.sleep(interval)
