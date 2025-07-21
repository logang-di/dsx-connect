from typing import List
from uuid import UUID

import httpx
from fastapi import APIRouter, Request, Path, HTTPException
from starlette import status
from starlette.responses import FileResponse, Response
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.models.connector_models import ScanRequestModel, ConnectorModel

from dsx_connect.models.constants import DSXConnectAPIEndpoints, ConnectorEndpoints
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum

router = APIRouter()


# This is mostly for testing purposes, to avoid CORS restrictions on a webapp
# calling on a dsx-connector to perform a full scan
@router.post(
    DSXConnectAPIEndpoints.INVOKE_FULLSCAN_CONNECTOR,
    response_model=StatusResponse,
    status_code=status.HTTP_200_OK,
    tags=["connectors"]
)
async def invoke_fullscan_connector(
        request: Request,
        connector_uuid: UUID = Path(..., description="The UUID of the connector to invoke")
):
    registry: List[ConnectorModel] = request.app.state.connectors

    # Find the ConnectorModel in our registry
    conn = next((c for c in registry if c.uuid == connector_uuid), None)
    if not conn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No connector registered with UUID={connector_uuid}"
        )

    # Build the connector’s own full_scan URL
    full_scan_url = f"{conn.url}{ConnectorEndpoints.FULL_SCAN}"

    # Call the connector
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(full_scan_url)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=e.response.text
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e)
        )

    # Unpack and return the connector’s StatusResponse
    return StatusResponse(**payload)


@router.post(
    DSXConnectAPIEndpoints.REGISTER_CONNECTORS,
    response_model=StatusResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["connectors"]
)
async def register_connector(conn: ConnectorModel, request: Request):
    registry: List[ConnectorModel] = request.app.state.connectors
    # optional: dedupe by url
    if any(existing.uuid == conn.uuid for existing in registry):
        # don't do anything - already registered and there's no harm in re-registering, as the connector model is\
        # unique for the live and active connector
        return StatusResponse(status=StatusResponseEnum.NOTHING,
                              message=f"Registration of {conn.url} : {conn.uuid} already in place",
                              description="")
    registry.append(conn)
    return StatusResponse(status=StatusResponseEnum.SUCCESS,
                          message="Registration succeeded",
                          description=f"Registration of {conn.url} : {conn.uuid} succeeded")


@router.delete(
    DSXConnectAPIEndpoints.UNREGISTER_CONNECTORS,
    tags=["connectors"])
async def unregister_connector(
        request: Request,
        connector_uuid: UUID = Path(..., description="UUID of the connector to remove")
):
    registry: List[ConnectorModel] = request.app.state.connectors
    request.app.state.connectors = [c for c in registry if c.uuid != connector_uuid]
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    DSXConnectAPIEndpoints.LIST_CONNECTORS,
    response_model=list[ConnectorModel],
    status_code=status.HTTP_200_OK,
    tags=["connectors"]
)
async def list_connectors(request: Request):
    return request.app.state.connectors


@router.get(
    DSXConnectAPIEndpoints.INVOKE_CONFIG_CONNECTOR,
    response_model=dict,
    status_code=status.HTTP_200_OK,
    tags=["connectors"]
)
async def fetch_connector_config(
        request: Request,
        connector_uuid: UUID = Path(..., description="The UUID of the connector to fetch config from")
):
    registry: List[ConnectorModel] = request.app.state.connectors
    conn = next((c for c in registry if c.uuid == connector_uuid), None)
    if not conn:
        raise HTTPException(status_code=404, detail=f"No connector found with UUID={connector_uuid}")

    # Build the connector’s own full_scan URL
    config_url = f"{conn.url}{ConnectorEndpoints.CONFIG}"

    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(config_url)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
