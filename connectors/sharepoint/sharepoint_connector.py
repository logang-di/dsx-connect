
from starlette.responses import StreamingResponse

from connectors.framework.dsx_connector import DSXConnector
from shared.models.connector_models import ScanRequestModel, ItemActionEnum, ConnectorInstanceModel
from shared.dsx_logging import dsx_logging
from shared.models.status_responses import StatusResponse, StatusResponseEnum, ItemActionStatusResponse
from connectors.sharepoint.config import ConfigManager
from connectors.sharepoint.version import CONNECTOR_VERSION
from connectors.sharepoint.sharepoint_client import SharePointClient
import asyncio

# Reload config to pick up environment variables
config = ConfigManager.reload_config()
# Initialize DSX Connector instance
connector = DSXConnector(config)
sp_client = SharePointClient(config)


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
    dsx_logging.info(f"Starting up connector {base.name}")
    dsx_logging.info(f"{connector.connector_id} version: {CONNECTOR_VERSION}.")
    dsx_logging.info(f"{base.name} configuration: {config}.")
    dsx_logging.info(f"{base.name} startup completed.")

    # Derive SharePoint connection details from ASSET (URL) once at startup, and
    # pre-compute the resolved asset base path inside the drive applying FILTER.
    # This avoids re-parsing in handlers and keeps locations stable.
    try:
        asset = (config.asset or "").strip()
        if asset.startswith("http://") or asset.startswith("https://"):
            try:
                host, site, drive_name, rel_path = SharePointClient.parse_sharepoint_web_url(asset)
                # If env didn't set host/site, adopt from ASSET. Otherwise keep env but warn on mismatch.
                if not config.sp_hostname:
                    config.sp_hostname = host
                elif config.sp_hostname != host:
                    dsx_logging.warning(f"ASSET host '{host}' differs from configured '{config.sp_hostname}'; using configured.")

                if not config.sp_site_path:
                    config.sp_site_path = site
                elif config.sp_site_path != site:
                    dsx_logging.warning(f"ASSET site '{site}' differs from configured '{config.sp_site_path}'; using configured.")

                # Drive name if provided; otherwise let client pick default
                if drive_name and not config.sp_drive_name:
                    config.sp_drive_name = drive_name

                base_path = rel_path or ""
            except Exception as e:
                dsx_logging.warning(f"Failed to parse DSXCONNECTOR_ASSET URL; using raw asset/filter: {e}")
                base_path = asset
        else:
            base_path = asset

        # Apply filter as subpath
        flt = (config.filter or "").strip("/")
        if flt:
            base_path = f"{base_path.strip('/')}/{flt}" if base_path else flt
        config.resolved_asset_base = base_path.strip('/')
        if config.resolved_asset_base:
            dsx_logging.info(f"Resolved SharePoint asset base: '{config.resolved_asset_base}'")
        else:
            dsx_logging.info("Resolved SharePoint asset base: root of drive")
    except Exception as e:
        dsx_logging.warning(f"Failed to derive resolved asset base: {e}")

    # Attempt to resolve site/drive on startup so readiness can pass
    try:
        await sp_client._ensure_site_and_drive()
        base.meta_info = f"SharePoint site={config.sp_site_path}, drive={config.sp_drive_name or 'default'}"
    except Exception as e:
        dsx_logging.warning(f"SharePoint discovery failed on startup: {e}")
        base.meta_info = "SharePoint discovery pending"
    return base


@connector.shutdown
async def shutdown_event():
    """
    Shutdown handler for the DSX Connector.

    This function is called by dsx-connect when the connector is shutting down.
    Use this handler to clean up resources such as closing connections or stopping background tasks.

    Returns:
        None
    """
    dsx_logging.info(f"Shutting down connector {connector.connector_id}")
    try:
        await sp_client.aclose()
    except Exception:
        pass


@connector.config
async def config_handler(base: ConnectorInstanceModel):
    """Expose connector runtime config for the UI, including resolved asset base."""
    try:
        payload = base.model_dump()
    except Exception:
        from fastapi.encoders import jsonable_encoder
        payload = jsonable_encoder(base)
    extra = {
        "asset": config.asset,
        "filter": config.filter,
        "resolved_asset_base": config.resolved_asset_base,
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    return payload


@connector.full_scan
async def full_scan_handler() -> StatusResponse:
    """
    Full Scan handler for the DSX Connector.

    This function is invoked by DSX Connect when a full scan of the connector's repository is requested.
    If your connector supports scanning all files (e.g., a filesystem or cloud storage connector), implement
    the logic to enumerate all files and trigger individual scan requests, using the base
    connector scan_file_request function.

    Example:
        iterate through files in a repository, and send a scan_file_request to dsx-connect for each file

        ```python
        async for file_path in file_ops.get_filepaths_async('F:/FileShare', True):
            await connector.scan_file_request(ScanRequestModel(location=str(file_path), metainfo=file_path.name))
        ```

        You can choose whatever location makes sense, as long as this connector can use it
        in read_file to read the file, whereever it is located.  The flow works like this:
        full_scan is invoked by dsx_connect, as it wants a full scan on whatever respository this
        connector is assigned to.  This connector in turn, enumerates through all files and
        sends a ScanEventQueueModel for each to dsx-connect, and more specifically, a queue
        of scan requests that dsx-connect will process.  dsx-connect then processes each
        queue item, calling read_file for each file that needs to be read.

    Args:
        scan_event_queue_info (ScanRequestModel): Contains metadata and location information necessary
            to perform a full scan.

    Returns:
        SimpleResponse: A response indicating success if the full scan is initiated, or an error if the
            functionality is not supported. (For connectors without full scan support, return an error response.)
    """
    # Iterate files and enqueue scan requests
    try:
        base_path = config.resolved_asset_base or (config.asset or "")
        concurrency = max(1, int(getattr(config, 'scan_concurrency', 10) or 10))
        sem = asyncio.Semaphore(concurrency)
        tasks: list[asyncio.Task] = []

        async def enqueue(item_id: str, metainfo: str):
            async with sem:
                dsx_logging.debug(f"Enqueuing scan request for item {item_id}")
                await connector.scan_file_request(ScanRequestModel(location=item_id, metainfo=metainfo))

        async for item in sp_client.iter_files_recursive(base_path):
            if item.get("folder"):
                continue
            # Determine a repository-relative path to apply the filter
            item_path = item.get("path") or item.get("name") or ""
            rel = item_path.strip('/')
            from shared.file_ops import relpath_matches_filter
            if config.filter and not relpath_matches_filter(rel, config.filter):
                continue
            item_id = item.get("id")
            metainfo = item_path
            tasks.append(asyncio.create_task(enqueue(item_id, metainfo)))

        if tasks:
            await asyncio.gather(*tasks)
        return StatusResponse(status=StatusResponseEnum.SUCCESS, message='Full scan invoked and scan requests sent.')
    except Exception as e:
        return StatusResponse(status=StatusResponseEnum.ERROR, message=str(e))


@connector.item_action
async def item_action_handler(scan_event_queue_info: ScanRequestModel) -> ItemActionStatusResponse:
    """
    Item Action handler for the DSX Connector.

    This function is called by DSX Connect when a file is determined to be malicious
    (or some other condition which DSX Connect thinks of a need to take action on a
    file)
    The connector should implement the appropriate remediation action here (e.g., delete, move, or tag the file)
    based on the provided quarantine configuration.

    Args:
        scan_event_queue_info (ScanRequestModel): Contains the location and metadata of the item that requires action.

    Returns:
        SimpleResponse: A response indicating that the remediation action was performed successfully,
            or an error if the action is not implemented.
    """
    # DELETE
    if config.item_action == ItemActionEnum.DELETE:
        try:
            await sp_client.delete_file(scan_event_queue_info.location)
            return ItemActionStatusResponse(
                status=StatusResponseEnum.SUCCESS,
                item_action=config.item_action,
                message="File deleted.",
                description=f"Deleted item id {scan_event_queue_info.location}"
            )
        except Exception as e:
            return ItemActionStatusResponse(
                status=StatusResponseEnum.ERROR,
                item_action=config.item_action,
                message=str(e)
            )
    # MOVE
    if config.item_action == ItemActionEnum.MOVE:
        try:
            dest = config.item_action_move_metainfo
            await sp_client.move_file(scan_event_queue_info.location, dest)
            return ItemActionStatusResponse(
                status=StatusResponseEnum.SUCCESS,
                item_action=config.item_action,
                message="File moved.",
                description=f"Moved item {scan_event_queue_info.location} to {dest}"
            )
        except Exception as e:
            return ItemActionStatusResponse(
                status=StatusResponseEnum.ERROR,
                item_action=config.item_action,
                message=str(e)
            )
    return ItemActionStatusResponse(status=StatusResponseEnum.NOTHING,
                                    item_action=config.item_action,
                                    message="Item action not implemented for SharePoint")


@connector.read_file
async def read_file_handler(scan_event_queue_info: ScanRequestModel) -> StatusResponse | StreamingResponse:
    """
    Read File handler for the DSX Connector.

    This function is invoked by DSX Connect when it needs to retrieve the content of a file.
    The connector should implement logic here to read the file from its repository (e.g., file system,
    S3 bucket, etc.) and return its contents wrapped in a FileContentResponse.

    Example:
    ```python
        @connector.read_file
        def read_file_handler(scan_event_queue_info: ScanEventQueueModel):
            file_path = pathlib.Path(scan_event_queue_info.location)

            # Check if the file exists
            if not os.path.isfile(file_path):
                return StatusResponse(status=StatusResponseEnum.ERROR,
                                    message=f"File {file_path} not found")

                # Read the file content
            try:
                file_like = file_path.open("rb")  # Open file in binary mode
                return StreamingResponse(file_like, media_type="application/octet-stream")  # Stream file
            except Exception as e:
                return StatusResponse(status=StatusResponseEnum.ERROR,
                                      message=f"Failed to read file: {str(e)}")
    ```

    Args:
        scan_event_queue_info (ScanRequestModel): Contains the location and metadata needed to locate and read the file.

    Returns:
        FileContentResponse or SimpleResponse: A successful FileContentResponse containing the file's content,
            or a SimpleResponse with an error message if file reading is not supported.
    """
    try:
        resp = await sp_client.download_file(scan_event_queue_info.location)

        async def agen():
            async for chunk in resp.aiter_bytes():
                yield chunk

        return StreamingResponse(agen(), media_type="application/octet-stream")
    except Exception as e:
        return StatusResponse(status=StatusResponseEnum.ERROR, message=str(e))


@connector.repo_check
async def repo_check_handler() -> StatusResponse:
    """
    Repository connectivity check handler.

    This handler verifies that the configured repository location exists and this DSX Connector can connect to it.

    Returns:
        bool: True if the repository connectivity OK, False otherwise.
    """
    ok = await sp_client.test_connection()
    if ok:
        return StatusResponse(status=StatusResponseEnum.SUCCESS, message="SharePoint connectivity success")
    return StatusResponse(status=StatusResponseEnum.ERROR, message="SharePoint connectivity failed")

@connector.webhook_event
async def webhook_handler(event: dict):
    """
    Webhook Event handler for the DSX Connector.

    This function is invoked by external systems (e.g., third-party file repositories or notification services)
    when a new file event occurs. The connector should extract the necessary file details from the event payload
    (for example, a file ID or name) and trigger a scan request via DSX Connect using the connector.scan_file_request method.

    Args:
        event (dict): The JSON payload sent by the external system containing file event details.

    Returns:
        SimpleResponse: A response indicating that the webhook was processed and the file scan request has been initiated,
            or an error if processing fails.
    """
    dsx_logging.info("Processing webhook event")
    ident = event.get("id") or event.get("item_id") or event.get("path") or event.get("webUrl")
    if not ident:
        return StatusResponse(status=StatusResponseEnum.ERROR, message="Missing item identifier in webhook event")

    location_id: str
    metainfo: str | dict

    try:
        # If a full URL was provided, try to derive a drive path for display
        if isinstance(ident, str) and (ident.startswith("http://") or ident.startswith("https://")):
            try:
                _, _, _, rel = SharePointClient.parse_sharepoint_web_url(ident)
                metainfo = rel or ident
            except Exception:
                metainfo = ident
            # Try resolve to item id using path
            try:
                location_id = await sp_client.resolve_item_id(metainfo if isinstance(metainfo, str) else str(metainfo))
            except Exception:
                return StatusResponse(status=StatusResponseEnum.ERROR, message="Unable to resolve item id from URL")
        elif isinstance(ident, str) and ("/" in ident or ":" in ident):
            # Treat as drive path
            metainfo = ident
            location_id = await sp_client.resolve_item_id(ident)
        else:
            # Treat as item id; best-effort to compute a friendly path for display
            location_id = str(ident)
            try:
                path = await sp_client.get_item_path(location_id)
                metainfo = path or event.get("name") or location_id
            except Exception:
                metainfo = event.get("name") or location_id

        await connector.scan_file_request(ScanRequestModel(location=location_id, metainfo=metainfo))
        return StatusResponse(status=StatusResponseEnum.SUCCESS, message="Webhook processed")
    except Exception as e:
        return StatusResponse(status=StatusResponseEnum.ERROR, message=f"Webhook error: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("connectors.framework.dsx_connector:connector_api", host="0.0.0.0",
                port=8620, reload=True)
