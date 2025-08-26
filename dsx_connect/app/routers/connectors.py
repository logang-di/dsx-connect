
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Request, Path, HTTPException, Depends, status
from starlette.responses import JSONResponse, Response

from dsx_connect.connectors import registration
from dsx_connect.connectors.client import get_connector_client, \
    get_async_connector_client  # ← new auth-aware async client
from dsx_connect.connectors.registry import ConnectorsRegistry
from shared.models.connector_models import ConnectorInstanceModel
from shared.dsx_logging import dsx_logging
from shared.models.status_responses import StatusResponse, StatusResponseEnum

from shared.routes import (
    API_PREFIX_V1,
    DSXConnectAPI,
    ConnectorPath,
    ConnectorAPI,
    route_name,
    Action,
    route_path,
)

# Make sure prefix starts with "/"
router = APIRouter(prefix=route_path(API_PREFIX_V1))


def get_registry(request: Request) -> Optional[ConnectorsRegistry]:
    return getattr(request.app.state, "registry", None)

def get_redis(request: Request):
    # wherever you stash the async Redis client at startup
    return getattr(request.app.state, "redis", None)

async def _lookup(
        registry: Optional[ConnectorsRegistry],
        request: Request,
        connector_uuid: UUID,
) -> Optional[ConnectorInstanceModel]:
    if registry is not None:
        return await registry.get(connector_uuid)  # async
    lst: list[ConnectorInstanceModel] = getattr(request.app.state, "connectors", [])
    return next((c for c in lst if c.uuid == connector_uuid), None)



# Register connector (idempotent)
@router.post(
    route_path(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.REGISTER_CONNECTORS),
    name=route_name(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.REGISTER_CONNECTORS, Action.REGISTER),
    response_model=StatusResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_connector(
        conn: ConnectorInstanceModel,
        request: Request,
        response: Response,
        registry: ConnectorsRegistry | None = Depends(get_registry),
        r = Depends(get_redis),
):
    ok, reg_status = await registration.register_or_refresh_connector(request, conn)
    if not ok:
        # keep returning 503 so connectors back off when Redis truly down
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="registry_unavailable")

    # optional: warm local cache immediately (pub/sub will also upsert)
    try:
        if registry is not None:
            await registry.upsert(conn)
    except Exception:
        pass

        # Dynamically set 201 vs 200, while still returning a validated model
    response.status_code = status.HTTP_201_CREATED if reg_status == "registered" else status.HTTP_200_OK

    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        message=str(reg_status),
        description=f"{conn.name} ({conn.uuid}) @ {conn.url}",
    )


@router.delete(
    route_path(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.UNREGISTER_CONNECTORS),
    name=route_name(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.UNREGISTER_CONNECTORS, Action.UNREGISTER),
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unregister_connector(
        request: Request,
        connector_uuid: UUID = Path(..., description="UUID of the connector"),
        registry: ConnectorsRegistry | None = Depends(get_registry),
        r = Depends(get_redis),
):
    # If Redis is down, tell the caller to back off
    if r is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="registry_unavailable")

    # (Optional) grab name for nicer event payloads
    name = None
    if registry is not None:
        try:
            m = await registry.get(connector_uuid)
            name = getattr(m, "name", None)
        except Exception:
            pass

    # Unregister in Redis (idempotent)
    try:
        ok = await registration.unregister_connector(request, connector_uuid, name=name)
        if not ok:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="registry_unavailable")
    except HTTPException as e:
        dsx_logging.warn(f"Failed to unregister connector {connector_uuid}: {e}")
        raise
    except Exception as e:
        # Treat as not found vs server error depending on preference; here we surface 500
        dsx_logging.warn(f"Failed to unregister connector {connector_uuid}: {e}")
        raise HTTPException(status_code=500, detail=f"unregister_failed: {e}")

    # Best-effort local cache cleanup (pub/sub will also handle)
    if registry is not None:
        try:
            await registry.remove(connector_uuid)
        except Exception:
            pass

    # 204 No Content
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# List registered connectors
@router.get(
    route_path(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.LIST_CONNECTORS),
    name=route_name(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.LIST_CONNECTORS, Action.LIST),
    response_model=list[ConnectorInstanceModel],
    status_code=status.HTTP_200_OK,
)
async def list_connectors(request: Request, registry=Depends(get_registry)):
    # 1) Try cache first
    if registry is not None:
        items = await registry.list()          # cached view
        if items:                              # happy path
            return items
        dsx_logging.debug("ConnectorsRegistry cache empty; falling back to Redis scan")
    return []

# Trigger a full scan on a connector (async command)
@router.post(
    route_path(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_FULLSCAN_CONNECTOR),
    name=route_name(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_FULLSCAN_CONNECTOR, Action.FULLSCAN),
    response_model=StatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_fullscan(
        request: Request,
        connector_uuid: UUID = Path(..., description="UUID of the connector"),
        registry=Depends(get_registry),
):
    conn = await _lookup(registry, request, connector_uuid)
    if not conn:
        raise HTTPException(status_code=404, detail=f"No connector registered with UUID={connector_uuid}")

    try:
        async with get_async_connector_client(conn) as client:
            response = await client.post(ConnectorAPI.FULL_SCAN)
            response.raise_for_status()
            data = response.json()
        return StatusResponse(**data) if isinstance(data, dict) else StatusResponse(
            status=StatusResponseEnum.SUCCESS, message="full scan triggered"
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        # Log connection issues without stack trace - these are expected when services are down
        dsx_logging.warning(f"Connector FULLSCAN call failed - service unavailable at {conn.url}: {type(e).__name__}")
        raise HTTPException(status_code=502, detail=f"Connector service unavailable: {type(e).__name__}")
    except Exception as e:
        # Log unexpected errors with stack trace for debugging
        dsx_logging.error(f"Unexpected error in connector FULLSCAN call: {str(e)}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


# Fetch connector config (proxy GET)
@router.get(
    route_path(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_CONFIG_CONNECTOR),
    name=route_name(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_CONFIG_CONNECTOR, Action.CONFIG),
    response_model=dict,
    status_code=status.HTTP_200_OK,
)
async def get_connector_config(
        request: Request,
        connector_uuid: UUID = Path(..., description="UUID of the connector"),
        registry=Depends(get_registry),
):
    conn = await _lookup(registry, request, connector_uuid)
    if not conn:
        raise HTTPException(status_code=404, detail=f"No connector found with UUID={connector_uuid}")

    try:
        async with get_async_connector_client(conn) as client:
            response = await client.get(ConnectorAPI.CONFIG)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        # Log connection issues without stack trace - these are expected when services are down
        dsx_logging.warning(f"Connector CONFIG call failed - service unavailable at {conn.url}: {type(e).__name__}")
        raise HTTPException(status_code=502, detail=f"Connector service unavailable: {type(e).__name__}")
    except Exception as e:
        # Log unexpected errors with stack trace for debugging
        dsx_logging.error(f"Unexpected error in connector CONFIG call: {str(e)}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


@router.get(
    route_path(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_HEALTHZ_CONNECTOR),
    name=route_name(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_HEALTHZ_CONNECTOR, Action.HEALTH),
    status_code=status.HTTP_200_OK,
)
async def get_connector_healthz(
        request: Request,
        connector_uuid: UUID = Path(..., description="UUID of the connector"),
        registry=Depends(get_registry),
):
    conn = await _lookup(registry, request, connector_uuid)
    if not conn:
        raise HTTPException(status_code=404, detail=f"No connector found with UUID={connector_uuid}")

    try:
        async with get_async_connector_client(conn) as client:
            response = await client.get(ConnectorAPI.HEALTHZ)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        # Log connection issues without stack trace - these are expected when services are down
        dsx_logging.debug(f"Connector HEALTHZ call failed - service unavailable at {conn.url}: {type(e).__name__}")
        raise HTTPException(status_code=502, detail=f"Connector service unavailable: {type(e).__name__}")
    except Exception as e:
        # Log unexpected errors with stack trace for debugging
        dsx_logging.error(f"Unexpected error in connector HEALTHZ call: {str(e)}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))


# Readyz (proxy GET)
@router.get(
    route_path(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_READYZ_CONNECTOR),
    name=route_name(DSXConnectAPI.CONNECTORS_PREFIX, ConnectorPath.TRIGGER_READYZ_CONNECTOR, Action.READY),
    status_code=status.HTTP_200_OK,
)
async def get_connector_readyz(
        request: Request,
        connector_uuid: UUID = Path(..., description="UUID of the connector"),
        registry=Depends(get_registry),
):
    conn = await _lookup(registry, request, connector_uuid)
    if not conn:
        raise HTTPException(status_code=404, detail=f"No connector found with UUID={connector_uuid}")

    try:
        async with get_async_connector_client(conn) as client:
            response = await client.get(ConnectorAPI.READYZ)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        # Log connection issues without stack trace - these are expected when services are down
        dsx_logging.warning(f"Connector READYZ call failed - service unavailable at {conn.url}: {type(e).__name__}")
        raise HTTPException(status_code=502, detail=f"Connector service unavailable: {type(e).__name__}")
    except Exception as e:
        # Log unexpected errors with stack trace for debugging
        dsx_logging.error(f"Unexpected error in connector READYZ call: {str(e)}", exc_info=True)
        raise HTTPException(status_code=502, detail=str(e))

