import pathlib
import random

import uvicorn
import os

from starlette.responses import StreamingResponse

from dsx_connect.utils import file_ops
from connectors.framework.dsx_connector import DSXConnector
from dsx_connect.models.connector_models import ScanRequestModel, ItemActionEnum, ConnectorInstanceModel, \
    ConnectorStatusEnum
from dsx_connect.utils.app_logging import dsx_logging
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum, ItemActionStatusResponse
from dsx_connect.utils.streaming import stream_blob
from filesystem_monitor import FilesystemMonitor, FilesystemMonitorCallback, ScanFolderModel
from dsx_connect.utils.async_ops import run_async
from connectors.filesystem.config import ConfigManager
from connectors.filesystem.version import CONNECTOR_VERSION

# Reload config to pick up environment variables
config = ConfigManager.reload_config()
connector = DSXConnector(config)


# given that this could potentially be a lengthy file iteration, make the iteration asynchronous...
# TODO possibly should allow startup of FAstAPI to complete, and schedule full scans in the background
async def start_monitor():
    """
    Create a filesystem monitor and capture the file information to send to the webhook/event.
    """
    if config.monitor:
        class MonitorCallback(FilesystemMonitorCallback):
            def __init__(self):
                super().__init__()

            def file_modified_callback(self, file_path: pathlib.Path):
                dsx_logging.debug(f'Sending scan request for {file_path}')
                run_async(
                    connector.webhook_handler(ScanRequestModel(location=str(file_path), metainfo=file_path.name)))

        monitor_callback = MonitorCallback()

        connector.filesystem_monitor = FilesystemMonitor(
            monitor_folder=ScanFolderModel(folder=pathlib.Path(config.asset), recursive=config.recursive,
                                           scan_existing=False),
            callback=monitor_callback)
        connector.filesystem_monitor.start()
        dsx_logging.info(f"Monitor set on {config.asset} for new or modified files")
    else:
        dsx_logging.info(f"Monitor set to false, {config.asset} will not be monitored for new or modified files")


@connector.startup
async def startup_event(base: ConnectorInstanceModel) -> ConnectorInstanceModel:
    """
    Startup handler for the DSX Connector.

    This function is invoked by dsx-connector during the startup phase of the connector.
    It should be used to initialize any required resources, such as setting up connections,
    starting background tasks, or performing initial configuration checks.

    Returns:
        ConnectorInstanceModel: the base dsx-connector will have populated this model, modify as needed and return
    """
    await start_monitor()
    dsx_logging.info(f"{base.name} version: {CONNECTOR_VERSION}.")
    dsx_logging.info(f"{base.name} configuration: {config}.")
    dsx_logging.info(f"{base.name} startup completed.")

    base.status = ConnectorStatusEnum.READY
    base.meta_info = f"Filesystem location: {config.asset}"
    return base


@connector.shutdown
async def shutdown_event():
    dsx_logging.info(f"{config.name} shutdown completed.")


@connector.full_scan
async def full_scan_handler() -> StatusResponse:
    dsx_logging.debug(
        f"Scanning files at: {config.asset} (recursive={config.recursive}, filter='{config.filter}')"
    )
    async for file_path in file_ops.get_filepaths_async(
            pathlib.Path(config.asset),
            config.filter):
        status_response = await connector.scan_file_request(
            ScanRequestModel(location=str(file_path), metainfo=file_path.name))
        dsx_logging.debug(f'Sent scan request for {file_path}, result: {status_response}')

    return StatusResponse(status=StatusResponseEnum.SUCCESS, message='Full scan invoked and scan requests sent.')


@connector.item_action
async def item_action_handler(scan_event_queue_info: ScanRequestModel) -> StatusResponse:
    file_path = scan_event_queue_info.location
    path_obj = pathlib.Path(file_path)

    if not path_obj.is_file():
        return ItemActionStatusResponse(status=StatusResponseEnum.ERROR, item_action=config.item_action,
                                        message="Item action failed.",
                                        description=f"File does not exist at {file_path}")

    if config.item_action == ItemActionEnum.DELETE:
        dsx_logging.debug(f'Item action {ItemActionEnum.DELETE} on {file_path} invoked.')
        path_obj.unlink()
        return ItemActionStatusResponse(status=StatusResponseEnum.SUCCESS,
                                        item_action=config.item_action,
                                        message=f'File deleted.',
                                        description=f"File deleted from {file_path}")
    elif config.item_action == ItemActionEnum.MOVE:
        dsx_logging.debug(f'Item action {ItemActionEnum.MOVE} on {file_path} invoked.')
        # Ensure the destination directory exists
        move_dir = pathlib.Path(config.item_action_move_metainfo)
        move_dir.mkdir(parents=True, exist_ok=True)
        # Construct the destination file path (same file name)
        new_path = move_dir / path_obj.name
        try:
            path_obj.rename(new_path)
            return ItemActionStatusResponse(
                status=StatusResponseEnum.SUCCESS,
                item_action=config.item_action,
                message="File moved",
                description=f'Item action {config.item_action} was invoked. File {file_path} successfully moved to {new_path}.'
            )
        except Exception as e:
            error_msg = f'Failed to move file {file_path}: {e}'
            dsx_logging.error(error_msg)
            return ItemActionStatusResponse(
                status=StatusResponseEnum.ERROR,
                message=error_msg,
                item_action=config.item_action
            )

    return ItemActionStatusResponse(status=StatusResponseEnum.NOTHING, item_action=config.item_action,
                                    message="Item action did nothing or not implemented")


def stream_file(file_like, chunk_size: int = 1024 * 1024):
    while True:
        chunk = file_like.read(chunk_size)
        if not chunk:
            break
        yield chunk


@connector.read_file
async def read_file_handler(scan_request_info: ScanRequestModel) -> StreamingResponse | StatusResponse:
    file_path = pathlib.Path(scan_request_info.location)

    # Check if the file exists
    if not os.path.isfile(file_path):
        return StatusResponse(status=StatusResponseEnum.ERROR,
                              message=f"File {file_path} not found")

    # Read the file content
    try:
        file_like = file_path.open("rb")  # Open file in binary mode
        return StreamingResponse(stream_file(file_like), media_type="application/octet-stream")  # Stream file
    except Exception as e:
        return StatusResponse(status=StatusResponseEnum.ERROR,
                              message=f"Failed to read file: {str(e)}")


@connector.repo_check
async def repo_check_handler() -> StatusResponse:
    """
    Repository connectivity check handler.

    This handler verifies that the configured repository location exists and this DSX Connector can connect to it.

    Returns:
        bool: True if the repository connectivity OK, False otherwise.
    """
    if os.path.exists(config.asset):
        return StatusResponse(
            status=StatusResponseEnum.SUCCESS,
            message=f"{config.asset} connectivity success.")
    else:
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message=f"Repo check failed for {config.asset}",
            description="")

#
# @connector.config
# async def config_handler():
#     # connector_running_config: ConnectorInstanceModel):
#     # override the connector_running_config with any specific configuration details you want to add
#     return {
#         "connector_name": connector.connector_running_model.name,
#         "uuid": connector.connector_running_model.uuid,
#         "dsx_connect_url": connector.connector_running_model.url,
#         "asset": config.asset,
#         "filter": config.filter,
#         "version": CONNECTOR_VERSION
#     }


# Main entry point to start the FastAPI app
if __name__ == "__main__":
    # Uvicorn will serve the FastAPI app and keep it running
    uvicorn.run("connectors.framework.dsx_connector:connector_api", host="0.0.0.0", port=8590, reload=False, workers=1)
