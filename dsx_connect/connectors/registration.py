from __future__ import annotations
import json
from typing import Optional, Tuple, Union
from uuid import UUID
from fastapi import Request
from redis.asyncio import Redis as AsyncRedis

from dsx_connect.messaging.notifiers import Notifiers
from dsx_connect.messaging.bus import Bus
from dsx_connect.messaging.topics import Topics, Keys
from dsx_connect.models.connector_models import ConnectorInstanceModel
from shared.dsx_logging import dsx_logging

DEFAULT_TTL = 120  # seconds

def _get_services(request: Request) -> tuple[Optional[AsyncRedis], Optional[Bus], Optional[Notifiers]]:
    app = request.app
    return (
        getattr(app.state, "redis", None),
        getattr(app.state, "bus", None),
        getattr(app.state, "notifiers", None),
    )

async def register_or_refresh_connector(
        request: Request,
        conn: ConnectorInstanceModel,
        ttl: int = DEFAULT_TTL,
) -> Tuple[bool, str]:
    """
    Upsert presence for a connector and broadcast:
      - full model on REGISTRY_CONNECTORS (internal)
      - compact envelope on NOTIFY_CONNECTORS (UI)
    Returns (ok, 'registered'|'refreshed'|'unavailable').
    """
    r, bus, notifiers = _get_services(request)
    if r is None:
        dsx_logging.warning("Registry unavailable; no async Redis client provided")
        return False, "unavailable"

    key = Keys.presence(str(conn.uuid))
    model_json = json.dumps(conn.model_dump(mode="json"), separators=(",", ":"))

    try:
        is_new = await r.set(name=key, value=model_json, ex=ttl, nx=True)
        if not is_new:
            await r.expire(key, ttl)

        # Internal bus: full payload for registry cache
        if bus is not None:
            await bus.publish(Topics.REGISTRY_CONNECTORS, model_json)

        # Notify SSE endpoints...
        if notifiers is not None:
            await notifiers.publish_connector_notify(
                event=("registered" if is_new else "refreshed"),
                uuid=str(conn.uuid),
                name=conn.name,
                url=conn.url,
            )
        return True, ("registered" if is_new else "refreshed")
    except Exception as e:
        dsx_logging.error(f"Redis operation failed for connector {conn.uuid}: {e}")
        return False, "unavailable"

async def unregister_connector(
        request: Request,
        uuid: Union[str, UUID],
        name: Optional[str] = None,
        url: Optional[str] = None,
) -> bool:
    """Delete presence and publish an 'unregistered' to both buses."""
    r, bus, notifiers = _get_services(request)
    if r is None:
        dsx_logging.warning("Registry unavailable; no async Redis client provided")
        return False

    uid = str(uuid)
    try:
        await r.delete(Keys.presence(uid))

        # Internal bus: lightweight envelope understood by ConnectorsRegistry
        envelope = json.dumps({"type": "unregistered", "uuid": uid, "name": name}, separators=(",", ":"))
        if bus is not None:
            await bus.publish(Topics.REGISTRY_CONNECTORS, envelope)

        # UI bus
        if notifiers is not None:
            await notifiers.publish_connector_notify(event="unregistered", uuid=uid, name=name or "", url=url or "")

        return True
    except Exception as e:
        dsx_logging.error(f"Redis operation failed during unregister {uid}: {e}")
        return False
