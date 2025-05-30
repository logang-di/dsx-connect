from contextlib import asynccontextmanager

from typing import List
from uuid import UUID

import httpx
from fastapi import FastAPI, Request, Path
from fastapi.staticfiles import StaticFiles
from fastapi import HTTPException
import uvicorn
import pathlib

from pydantic import HttpUrl
from starlette import status
from starlette.responses import FileResponse, Response
from dsx_connect.config import ConfigManager
from dsx_connect.models.connector_models import ConnectorModel

from dsx_connect.models.constants import DSXConnectAPIEndpoints, ConnectorEndpoints
from dsx_connect.dsxa_client.dsxa_client import DSXAClient
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum
from dsx_connect.utils.logging import dsx_logging

from dsx_connect.app.dependencies import static_path

from dsx_connect.app.routers import scan_request, scan_request_test, scan_results

from dsx_connect import version


@asynccontextmanager
async def lifespan(app: FastAPI):
    dsx_logging.info(f"dsx-connect version: {version.DSX_CONNECT_VERSION}")
    dsx_logging.info(f"dsx-connect configuration: {config}")
    dsx_logging.info("dsx-connect startup completed.")

    app.state.connectors: List[ConnectorModel] = []

    yield

    dsx_logging.info("dsx-connect shutdown completed.")


app = FastAPI(title='dsx-connect API',
              description='Deep Instinct Data Security X Connect for Applications API',
              version=version.DSX_CONNECT_VERSION,
              docs_url='/docs',
              lifespan=lifespan)

# Reload config to pick up environment variables
config = ConfigManager.reload_config()

app.mount("/static", StaticFiles(directory=static_path, html=True), name='static')

app.include_router(scan_request_test.router, tags=["test"])
app.include_router(scan_request.router, tags=["scan"])
app.include_router(scan_results.router, tags=["results"])


# @app.on_event("startup")
# async def startup_event():
#     dpx_logging.info(f"dsx-connect version: {version.DSX_CONNECT_VERSION}")
#     dpx_logging.info(f"dsx-connect configuration: {get_config()}")
#     dpx_logging.info("dsx-connect startup completed.")
#
#
# @app.on_event("shutdown")
# async def shutdown_event():
#     dpx_logging.info("dsx-connect shutdown completed.")


@app.get("/")
def home(request: Request):
    home_path = pathlib.Path(static_path / 'html/dsx_connect.html')
    return FileResponse(home_path)


@app.get(DSXConnectAPIEndpoints.CONFIG, description='Get all configuration')
def get_get_config():
    return config


@app.get(DSXConnectAPIEndpoints.CONNECTION_TEST, description="Test connection to dsx-connect.", tags=["test"])
async def get_test_connection():
    return StatusResponse(
        status=StatusResponseEnum.SUCCESS,
        description="",
        message="Successfully connected to dsx-connect"
    )


@app.get(DSXConnectAPIEndpoints.DSXA_CONNECTION_TEST, description="Test connection to dsxa.", tags=["test"])
async def get_dsxa_test_connection():
    dsxa_client = DSXAClient(config.scanner.scan_binary_url)
    response = await dsxa_client.test_connection_async()
    return response


# This is mostly for testing purposes, to avoid CORS restrictions on a webapp
# calling on a dsx-connector to perform a full scan
@app.post(
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

@app.post(
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

@app.delete(
    DSXConnectAPIEndpoints.UNREGISTER_CONNECTORS,
    tags=["connectors"])
async def unregister_connector(
        request: Request,
        connector_uuid: UUID = Path(..., description="UUID of the connector to remove")
):
    registry: List[ConnectorModel] = request.app.state.connectors
    app.state.connectors = [c for c in registry if c.uuid != connector_uuid]
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.get(
    DSXConnectAPIEndpoints.LIST_CONNECTORS,
    response_model=list[ConnectorModel],
    status_code=status.HTTP_200_OK,
    tags=["connectors"]
)
async def list_connectors(request: Request):
    return request.app.state.connectors


# Main entry point to start the FastAPI app
if __name__ == "__main__":
    # Uvicorn will serve the FastAPI app and keep it running
    uvicorn.run("dsx_connect_api:app", host="0.0.0.0", port=8586, reload=True, workers=1)
