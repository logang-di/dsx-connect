import os
import pathlib
import urllib
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi.encoders import jsonable_encoder
from httpx import HTTPStatusError
from requests.exceptions import RequestException, HTTPError, Timeout, ConnectionError

from fastapi import FastAPI, APIRouter, Request, BackgroundTasks, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from typing import Callable, Awaitable

from starlette.responses import StreamingResponse

from connectors.framework.base_config import BaseConnectorConfig
from dsx_connect.models.connector_models import ScanRequestModel, ConnectorInstanceModel, ConnectorStatusEnum, ItemActionEnum
from dsx_connect.models.constants import DSXConnectAPIEndpoints, ConnectorEndpoints
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum, ItemActionStatusResponse
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.models.connector_api_key import APIKeySettings
from connectors.framework.connector_id import get_or_create_connector_uuid



# read API key if available from environment settings (via Pydantic's BaseSettings)
api_key_setting = APIKeySettings()
dsx_logging.info(f"Using API key for authorization: {'True' if api_key_setting.api_key else 'False'}")


# specify header name for api keys
API_KEY_NAME = "x-api-key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def validate_api_key(api_key: str = Security(api_key_header)):
    if api_key != api_key_setting.api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
# <end> API key config and validation

connector_api = None


class DSXConnector:
    def __init__(self, connector_config: BaseConnectorConfig) :

        # connector_name: str, base_connector_url: str, dsx_connect_url: str,
        #          item_action_move_metainfo: str = "", test_mode: bool = False):

        self.test_mode = connector_config.test_mode
        self.connector_id = connector_config.name # for now, the id is just he name
        # self.item_action_move_metainfo = item_action_move_metainfo

        uuid = get_or_create_connector_uuid()
        dsx_logging.debug(f"Logical connector {self.connector_id} using UUID: {uuid}")
        self.scan_request_count = 0

        # clean up URL if needed
        self.dsx_connect_url = str(connector_config.dsx_connect_url).rstrip('/')

        self.connector_running_model = ConnectorInstanceModel(
            name=connector_config.name,
            uuid = uuid,
            url=f'{str(connector_config.connector_url).rstrip("/")}/{self.connector_id}',
            status=ConnectorStatusEnum.STARTING,
            item_action_move_metainfo = connector_config.item_action_move_metainfo,
            asset=connector_config.asset,
            filter=connector_config.filter
        )

        self.startup_handler: Callable[[ConnectorInstanceModel], Awaitable[None]] = None
        self.shutdown_handler: Callable[[], Awaitable[None]] = None

        self.full_scan_handler: Callable[[ScanRequestModel], StatusResponse] = None
        self.item_action_handler: Callable[[ScanRequestModel], ItemActionStatusResponse] = None
        self.read_file_handler: Callable[[ScanRequestModel], StreamingResponse | StatusResponse] = None
        self.webhook_handler: Callable[[ScanRequestModel], StatusResponse] = None
        self.repo_check_connection_handler: Callable[[], StatusResponse] = None
        # self.config_handler: Callable[[ConnectorInstanceModel], dict] = None
        self.config_handler: Callable[[ConnectorInstanceModel], Awaitable[ConnectorInstanceModel]] = None


        # Startup / shutdown logic using FastAPI's lifecycle mechanism
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # ============ 1) “startup” logic ============

            # 1a) call the user’s @connector.startup decorated function, if any:
            if self.startup_handler:
                self.connector_running_model = await self.startup_handler(self.connector_running_model)

            # 1b) register with dsx-connect
            register_resp = await self.register_connector(self.connector_running_model)
            if register_resp.status == StatusResponseEnum.SUCCESS:
                dsx_logging.info(f"Registered connector OK: {register_resp.message}")
            else:
                dsx_logging.warn(f"Connector registration failed: {register_resp.message}")

            # Now yield control back to FastAPI so it can start serving requests:
            yield

            # ============ 2) “shutdown” logic ============

            # 2a) unregister from dsx-connect
            unregister_resp = await self.unregister_connector()
            if unregister_resp.status == StatusResponseEnum.SUCCESS:
                dsx_logging.info(f"Unregistered connector OK: {unregister_resp.message}")
            else:
                dsx_logging.warn(f"Connector unregistration failed: {unregister_resp.message}")

            # 2b) call the user’s @connector.shutdown decorated function, if any:
            if self.shutdown_handler:
                await self.shutdown_handler()

        # Create the connector FastAPI app and include its router
        global connector_api
        connector_api = FastAPI(
            title=f"{self.connector_running_model.name} [dsx-connector]",
            description=f"API for dsx-connector: {self.connector_running_model.name} (UUID: {self.connector_running_model.uuid})",
            lifespan=lifespan
        )
        connector_api.include_router(DSXAConnectorRouter(self))


    # Register handlers for startup and shutdown events
    def startup(self, func: Callable[[ConnectorInstanceModel], Awaitable[ConnectorInstanceModel]]):
        self.startup_handler = func
        return func

    def shutdown(self, func: Callable[[], bool]):
        self.shutdown_handler = func
        return func

    # Register handler for the /full_scan event
    def full_scan(self, func: Callable[[], dict]):
        self.full_scan_handler = func
        return func

    # Register handler for the /quarantine_action event
    def item_action(self, func: Callable[[ScanRequestModel], dict]):
        self.item_action_handler = func
        return func

    # Register handler for the /read_file event
    def read_file(self, func: Callable[[ScanRequestModel], StreamingResponse | StatusResponse]):
        self.read_file_handler = func
        return func

    def repo_check(self, func: Callable[[], bool]):
        """Register a function to check repository connectivity."""
        self.repo_check_connection_handler = func
        return func  # ✅ Return func so it works as a decorator

    # Register handler for the /webhook event
    def webhook_event(self, func: Callable[[ScanRequestModel], StreamingResponse | StatusResponse]):
        self.webhook_handler = func
        return func

    # Decorator registration method:
    # def config(self, func: Callable[[], dict]):
    #     self.config_handler = func
    #     return func

    def config(self, func: Callable[[ConnectorInstanceModel], Awaitable[ConnectorInstanceModel]]):
        self.config_handler = func
        return func

    async def scan_file_request(self, scan_request: ScanRequestModel) -> StatusResponse:
        # Skip if location includes the configured quarantine/metainfo path
        if self.connector_running_model.item_action_move_metainfo in scan_request.location:
            dsx_logging.info(f"Skipping scan for file in quarantine path: {scan_request.location}")
            return StatusResponse(
                status=StatusResponseEnum.NOTHING,
                description="File in quarantine path, skipping scan",
                message=f"Scan skipped for: {scan_request.location}"
            )

        scan_request.connector = self.connector_running_model
        scan_request.connector_url = self.connector_running_model.url
        try:
            async with httpx.AsyncClient(verify=False) as client:
                if not self.test_mode:
                    response = await client.post(
                        f'{self.dsx_connect_url}{DSXConnectAPIEndpoints.SCAN_REQUEST}',
                        json=jsonable_encoder(scan_request)
                    )
                    dsx_logging.debug(f'Scan request returned')

                else:
                    response = await client.post(
                        f'{self.dsx_connect_url}{DSXConnectAPIEndpoints.SCAN_REQUEST_TEST}',
                        json=scan_request.dict()
                    )
                    dsx_logging.debug(f'Scan request test returned')

            # Raise an exception for bad responses (4xx and 5xx status codes)
            response.raise_for_status()

            self.scan_request_count += 1  # for reporting purposes
            # Return the response JSON if the request was successful
            return StatusResponse(**response.json())

        except httpx.HTTPStatusError as http_error:
            dsx_logging.error(f"HTTP error during scan request: {http_error}", exc_info=True)
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                description="Failed to send scan request",
                message=str(http_error)
            )
        except Exception as e:
            dsx_logging.error(f"Unexpected error during scan request: {e}", exc_info=True)
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                description="Unexpected error in scan request",
                message=str(e)
            )

    async def get_status(self):
        dsxa_status = await self.test_dsx_connect()
        repo_status = await self.repo_check_connection_handler() if self.repo_check_connection_handler else False

        return {
            "connector_status": "Active",
            "dsx-connect connectivity": "success" if dsxa_status else "failed",
            "repo connectivity": "success" if repo_status else "failed",
            "scan_requests_since_active_count": self.scan_request_count,
        }

    async def register_connector(self, conn_model: ConnectorInstanceModel) -> StatusResponse:
        """
        Tell dsx-connect about this connector instance.
        """
        payload = jsonable_encoder(conn_model)

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(
                    f"{self.dsx_connect_url}{DSXConnectAPIEndpoints.REGISTER_CONNECTORS}",
                    json=payload
                )
                resp.raise_for_status()
                return StatusResponse(**resp.json())
        except HTTPStatusError as e:
            dsx_logging.error(f"Failed to register connector: {e}", exc_info=True)
            return StatusResponse(status=StatusResponseEnum.ERROR,
                                  message="Registration failed",
                                  description=str(e))
        except Exception as e:
            dsx_logging.error(f"Unexpected error registering connector: {e}", exc_info=True)
            return StatusResponse(status=StatusResponseEnum.ERROR,
                                  message="Registration error",
                                  description=str(e))

    async def unregister_connector(self) -> StatusResponse:
        """
        Tell dsx-connect this connector instance is going away.
        """
        # URL‑encode the connector URL so it fits safely in the path
        encoded = urllib.parse.quote(str(self.connector_running_model.uuid), safe="")
        path = DSXConnectAPIEndpoints.UNREGISTER_CONNECTORS.format(connector_uuid=encoded)
        url = f"{self.dsx_connect_url}{path}"

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.delete(url)
                # 204 No Content → success
                if resp.status_code == 204:
                    return StatusResponse(
                        status=StatusResponseEnum.SUCCESS,
                        message="Unregistered",
                        description=f"Connector {self.connector_running_model.url} : {self.connector_running_model.uuid} removed"
                    )
                # if they happen to return a body, parse it:
                resp.raise_for_status()
                return StatusResponse(**resp.json())
        except HTTPStatusError as e:
            dsx_logging.error(f"Failed to unregister connector: {e}", exc_info=True)
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                message="Unregistration failed",
                description=str(e)
            )
        except Exception as e:
            dsx_logging.error(f"Unexpected error unregistering connector: {e}", exc_info=True)
            return StatusResponse(
                status=StatusResponseEnum.ERROR,
                message="Unregistration error",
                description=str(e)
            )

    async def test_dsx_connect(self) -> StatusResponse:
        try:
            async with httpx.AsyncClient(verify=False) as client:
                response = await client.get(f'{self.dsx_connect_url}{DSXConnectAPIEndpoints.CONNECTION_TEST}')
            # Raise an exception for bad responses (4xx and 5xx status codes)
            response.raise_for_status()

            # Return the response JSON if the request was successful
            return response.json()
        except HTTPError as http_err:
            dsx_logging.warn(f"HTTP error occurred: {http_err}")  # Handle HTTP errors (4xx, 5xx)
        except HTTPStatusError as http_err:
            dsx_logging.warn(f"HTTP error occurred: {http_err}")  # Handle HTTP errors (4xx, 5xx)
        except ConnectionError as conn_err:
            dsx_logging.warn(f"Connection error occurred: {conn_err}")  # Handle network problems (e.g., DNS failure)
        except Timeout as timeout_err:
            dsx_logging.warn(f"Timeout error occurred: {timeout_err}")  # Handle request timeout
        except RequestException as req_err:
            dsx_logging.warn(f"An error occurred: {req_err}")  # Catch all other request exceptions
        except Exception as e:
            dsx_logging.warn(f"An unexpected error occurred: {e}")
        return None  # Return None if an exception occurred


class DSXAConnectorRouter(APIRouter):
    def __init__(self, connector: DSXConnector):
        super().__init__(dependencies=[Depends(validate_api_key)])
        self._connector = connector
        # no longer used when we converted to using FastAPI's lifecycle context manager
        # self._startup_done = False
        # self._registered = False

        (self.get('/', description='Connector status and availability',
                  response_model=None)
         (self.home))
        self.put(f'/{self._connector.connector_running_model.name}{ConnectorEndpoints.ITEM_ACTION}',
                 description='Request that the connector perform and action on an item',
                 response_model=ItemActionStatusResponse,
                 response_description='')(self.put_item_action)
        self.post(f'/{self._connector.connector_running_model.name}{ConnectorEndpoints.FULL_SCAN}',
                  description='Request that the connector initiate a full scan.',
                  response_model=StatusResponse,
                  response_description='')(self.post_full_scan)
        self.post(f'/{self._connector.connector_running_model.name}{ConnectorEndpoints.READ_FILE}',
                  description='Request a file from the connector',
                  response_description='',
                  response_model=None)(self.post_read_file)

        self.get(f'/{self._connector.connector_running_model.name}{ConnectorEndpoints.REPO_CHECK}',
                 description='Check connectivity to repository',
                 response_description='',
                 response_model=None)(self.get_repo_check)

        self.post(f'/{self._connector.connector_running_model.name}{ConnectorEndpoints.WEBHOOK_EVENT}')(self.post_handle_webhook_event)

        self.get(f'/{self._connector.connector_running_model.name}{ConnectorEndpoints.CONFIG}',
                 description='Returns connector configuration')(self.get_config)


        # Register FastAPI events
        # self.on_event("startup")(self.on_startup_event)
        # self.on_event("shutdown")(self.on_shutdown_event)

    async def home(self):
        return await self._connector.get_status()

    async def put_item_action(self, scan_request_info: ScanRequestModel) -> ItemActionStatusResponse:
        if self._connector.item_action_handler:
            return await self._connector.item_action_handler(scan_request_info)
        return ItemActionStatusResponse(status=StatusResponseEnum.ERROR,
                                        item_action=ItemActionEnum.NOT_IMPLEMENTED,
                                        message="No handler registered for quarantine_action",
                                        description="Add a decorator (ex: @connector.item_action) to handle item_action requests")

    async def post_full_scan(self, background_tasks: BackgroundTasks) -> StatusResponse:
        if self._connector.full_scan_handler:
            background_tasks.add_task(self._connector.full_scan_handler)
            return StatusResponse(
                status=StatusResponseEnum.SUCCESS,
                message="Full scan initiated",
                description="The scan is running in the background."
            )
        return StatusResponse(status=StatusResponseEnum.ERROR,
                              message="No handler registered for full_scan",
                              description="Add a decorator (ex: @connector.full_scan) to handle full scan requests")

    async def post_read_file(self, scan_request_info: ScanRequestModel) -> StreamingResponse | StatusResponse:
        dsx_logging.debug(f'Receive read_file request for {scan_request_info}')
        if self._connector.read_file_handler:
            return await self._connector.read_file_handler(scan_request_info)
        return StatusResponse(status=StatusResponseEnum.ERROR,
                              message="No event handler registered for read_file",
                              description="Add a decorator (ex: @connector.read_file) to handle read file requests")

    async def get_repo_check(self) -> StatusResponse:
        if self._connector.repo_check_connection_handler:
            return await self._connector.repo_check_connection_handler()
        return StatusResponse(status=StatusResponseEnum.ERROR,
                              message="No event handler registered for repo_check",
                              description="Add a decorator (ex: @connector.repo_check) to handle repo check requests")

    async def post_handle_webhook_event(self, request: Request):
        if self._connector.webhook_handler:
            # Parse the JSON payload from the request body
            event = await request.json()
            dsx_logging.info(f"Received webhook for artifact path: {event}")
            return await self._connector.webhook_handler(event)
        # dpx_logging.info(f"Received webhook for file: {event.name} at {event.repo_key}/{event.path}")
        # Trigger a scan or other processing as required
        # await connector.scan_file_request(ScanEventQueueModel(location=event.path, metainfo=event.name))
        return StatusResponse(status=StatusResponseEnum.ERROR,
                              message="No handler registered for webhook_event",
                              description="Add a decorator (ex: @connector.webhook_event) to handle webhook events")

    async def get_config(self):
        if self._connector.config_handler:
            return await self._connector.config_handler(self._connector.connector_running_model)
        return self._connector.connector_running_model

