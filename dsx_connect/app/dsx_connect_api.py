import asyncio
import json
from contextlib import asynccontextmanager

from typing import List
from uuid import UUID

from redis.asyncio import Redis
import httpx
from fastapi import FastAPI, Request, Path
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import uvicorn
import pathlib

from pydantic import HttpUrl, BaseModel
from starlette import status
from starlette.responses import FileResponse, StreamingResponse

from dsx_connect.config import ConfigManager
from dsx_connect.models.connector_models import ConnectorInstanceModel

from dsx_connect.models.constants import DSXConnectAPIEndpoints, ConnectorEndpoints
from dsx_connect.dsxa_client.dsxa_client import DSXAClient
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum
from dsx_connect.models.scan_models import ScanResultModel
from dsx_connect.taskqueue.celery_app import celery_app
from dsx_connect.utils.logging import dsx_logging

from dsx_connect.app.dependencies import static_path
from dsx_connect.app.routers import scan_request, scan_request_test, scan_results, connectors
from dsx_connect.connector_utils import connector_client
from dsx_connect.connector_utils.connector_heartbeat import heartbeat_all_connectors
from dsx_connect import version


@asynccontextmanager
async def lifespan(app: FastAPI):
    dsx_logging.info(f"dsx-connect version: {version.DSX_CONNECT_VERSION}")
    dsx_logging.info(f"dsx-connect configuration: {config}")
    dsx_logging.info("dsx-connect startup completed.")

    app.state.connectors: List[ConnectorInstanceModel] = []
    app.state.heartbeat_task = asyncio.create_task(heartbeat_all_connectors(app))

    # inside an async context (e.g., in lifespan)
    app.state.redis = Redis.from_url(config.redis_url)

    yield

    app.state.heartbeat_task.cancel()
    await app.state.redis.close()

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
app.include_router(connectors.router, tags=["connectors"])


@app.get("/")
def home(request: Request):
    home_path = pathlib.Path(static_path / 'html/dsx_connect.html')
    return FileResponse(home_path)


@app.get(DSXConnectAPIEndpoints.CONFIG, description='Get all configuration')
def get_get_config():
    return config


@app.get(DSXConnectAPIEndpoints.VERSION, description='Get version')
def get_get_version():
    return version.DSX_CONNECT_VERSION

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


async def notification_stream():
    pubsub = app.state.redis.pubsub()
    await pubsub.subscribe("scan_results")
    # This loops to see if messages are ready to notify clients...
    # if no messages coming in on successive loops, start sleeping a little longer, just
    # so we aren't looping unnecessarily 10 times a second
    sleep_duration = 0.1
    while True:
        msg = await pubsub.get_message(ignore_subscribe_messages=True)
        if msg and msg["type"] == "message":
            event = json.loads(msg["data"])
            yield f"data: {json.dumps(event)}\n\n"
            sleep_duration = 0.01  # reset after receiving
        else:
            sleep_duration = min(sleep_duration * 2, 1.0)  # back off up to 1s
        await asyncio.sleep(sleep_duration)

@app.get(DSXConnectAPIEndpoints.NOTIFICATIONS_SCAN_RESULT)
async def get_notification_scan_result():
    return StreamingResponse(notification_stream(), media_type="text/event-stream")


@app.get(DSXConnectAPIEndpoints.NOTIFICATIONS_CONNECTOR_REGISTERED)
async def connector_registered_stream():
    pubsub = app.state.redis.pubsub()
    await pubsub.subscribe("connector_registered")

    async def event_generator():
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True)
            if msg and msg["type"] == "message":
                event = json.loads(msg["data"])
                yield f"data: {json.dumps(event)}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Main entry point to start the FastAPI app
if __name__ == "__main__":
    # Uvicorn will serve the FastAPI app and keep it running
    uvicorn.run("dsx_connect_api:app", host="0.0.0.0", port=8586, reload=True, workers=1)
