from starlette.responses import StreamingResponse

from connectors.aws_s3.aws_s3_client import AWSS3Client
from connectors.framework.dsx_connector import DSXConnector
from dsx_connect.utils import file_ops
from dsx_connect.models.connector_models import ScanRequestModel, ItemActionEnum, ConnectorModel, ConnectorStatusEnum
from dsx_connect.utils.async_ops import run_async
from dsx_connect.utils.logging import dsx_logging
from dsx_connect.models.responses import StatusResponse, StatusResponseEnum, ItemActionStatusResponse
from connectors.aws_s3.config import ConfigManager
from connectors.aws_s3.version import CONNECTOR_VERSION
from dsx_connect.utils.streaming import stream_blob

# Reload config to pick up environment variables
config = ConfigManager.reload_config()
connector_id = config.name  # I'm not really sure what I want to use this for yet

# Initialize DSX Connector instance
connector = DSXConnector(connector_name=config.name,
                         connector_id=connector_id,
                         base_connector_url=config.connector_url,
                         dsx_connect_url=config.dsx_connect_url,
                         test_mode=config.test_mode)

aws_s3_client = AWSS3Client(s3_endpoint_url=config.s3_endpoint_url, s3_endpoint_verify=config.s3_endpoint_verify)


@connector.startup
async def startup_event(base: ConnectorModel) -> ConnectorModel:
    """
    Startup handler for the DSX Connector.

    This function is invoked by dsx-connector during the startup phase of the connector.
    It should be used to initialize any required resources, such as setting up connections,
    starting background tasks, or performing initial configuration checks.

    Returns:
        ConnectorModel: the base dsx-connector will have populated this model, modify as needed and return
    """
    dsx_logging.info(f"Starting up connector {connector.connector_id}")

    dsx_logging.info(f"{connector.connector_id} version: {CONNECTOR_VERSION}.")
    dsx_logging.info(f"{connector.connector_id} configuration: {config}.")
    dsx_logging.info(f"{connector.connector_name}:{connector.connector_id} startup completed.")

    base.status = ConnectorStatusEnum.READY
    base.meta_info = f"S3 Bucket: {config.s3_bucket}, prefix: {config.s3_prefix}"
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
    for key in aws_s3_client.keys(config.s3_bucket, prefix=config.s3_prefix, recursive=config.s3_recursive):
        file_name = key['Key']
        full_path = f"{config.s3_bucket}/{file_name}"
        status_response = await connector.scan_file_request(
            ScanRequestModel(location=str(f"{file_name}"), metainfo=full_path))
        dsx_logging.debug(f'Sent scan request for {full_path}, result: {status_response}')

    return StatusResponse(status=StatusResponseEnum.SUCCESS, message='Full scan invoked and scan requests sent.')


@connector.item_action
async def item_action_handler(scan_event_queue_info: ScanRequestModel) -> StatusResponse:
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
    full_path = scan_event_queue_info.metainfo

    if not aws_s3_client.key_exists(config.s3_bucket, scan_event_queue_info.location):
        return ItemActionStatusResponse(status=StatusResponseEnum.ERROR, item_action=config.item_action,
                                        message="Item action failed.",
                                        description=f"File does not exist at {full_path}")

    if config.item_action == ItemActionEnum.DELETE:
        dsx_logging.debug(f'Item action {ItemActionEnum.DELETE} on {full_path} invoked.')
        if aws_s3_client.delete_object(config.s3_bucket, scan_event_queue_info.location):
            return ItemActionStatusResponse(status=StatusResponseEnum.SUCCESS, item_action=config.item_action,
                                            message="File deleted.",
                                            description=f"File deleted from {config.s3_bucket}: {scan_event_queue_info.location}")
    elif config.item_action == ItemActionEnum.MOVE:
        dsx_logging.debug(f'Item action {ItemActionEnum.MOVE} on {full_path} invoked.')
        dest_key = f"{config.item_action_move_prefix}/{scan_event_queue_info.location}"
        aws_s3_client.move_object(src_bucket=config.s3_bucket, src_key=scan_event_queue_info.location,
                                  dest_bucket=config.s3_bucket,
                                  dest_key=dest_key)
        return ItemActionStatusResponse(status=StatusResponseEnum.SUCCESS, item_action=config.item_action,
                                        message="File moved.",
                                        description=f"File moved from {config.s3_bucket}: {scan_event_queue_info.location} to {config.s3_bucket}: {dest_key}")
    elif config.item_action == ItemActionEnum.TAG:
        dsx_logging.debug(f'Item action {ItemActionEnum.TAG} on {full_path} invoked.')
        aws_s3_client.tag_object(config.s3_bucket, scan_event_queue_info.location, tags={"Verdict": "Malicious"})
        return ItemActionStatusResponse(status=StatusResponseEnum.SUCCESS, item_action=config.item_action,
                                        message="File tagged.",
                                        description=f"File tagged at {config.s3_bucket}: {scan_event_queue_info.location}")
    elif config.item_action == ItemActionEnum.MOVE_TAG:
        dsx_logging.debug(f'Item action {ItemActionEnum.MOVE_TAG} on {full_path} invoked.')
        dest_key = f"{config.item_action_move_prefix}/{scan_event_queue_info.location}"

        aws_s3_client.move_object(src_bucket=config.s3_bucket, src_key=scan_event_queue_info.location,
                                  dest_bucket=config.s3_bucket, dest_key=dest_key)

        aws_s3_client.tag_object(config.s3_bucket, dest_key, tags={"Verdict": "Malicious"})

        return ItemActionStatusResponse(status=StatusResponseEnum.SUCCESS, item_action=config.item_action,
                                        message=f'Item action {config.item_action} was invoked. File {full_path} successfully tagged.')

    return ItemActionStatusResponse(status=StatusResponseEnum.NOTHING, item_action=config.item_action,
                                    message=f"Item action did nothing or not implemented")


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
    # Read the file content
    try:
        bytes_obj = aws_s3_client.get_object(bucket=config.s3_bucket, key=scan_event_queue_info.location)
        return StreamingResponse(stream_blob(bytes_obj), media_type="application/octet-stream")  # Stream file
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
    if aws_s3_client.test_s3_connection(config.s3_bucket):
        return StatusResponse(
            status=StatusResponseEnum.SUCCESS,
            message=f"Connection to bucket: {config.s3_bucket} successful",
            description=""
        )

    return StatusResponse(
        status=StatusResponseEnum.ERROR,
        message=f"Connection to bucket: {config.s3_bucket} NOT successful",
        description=""
    )


@connector.webhook_event
async def webhook_handler(event: dict):
    """
    Webhook Event handler for the DSX Connector.

    This function is invoked by external systems (e.g., third-party file repositories or notification services)
    when a new file event occurs. The connector should extract the necessary file details from the event payload
    (for example, a file ID or name) and trigger a scan request via DSX Connect using the connector.scan_file_request method.

    Handles AWS S3-style event:
    {
        "Records": [
            {
                "s3": {
                    "bucket": { "name": "my-bucket" },
                    "object": { "key": "path/to/file.txt" }
                }
            }
        ]
    }

    Args:
        event (dict): The JSON payload sent by the external system containing file event details.

    Returns:
        SimpleResponse: A response indicating that the webhook was processed and the file scan request has been initiated,
            or an error if processing fails.

    """
    dsx_logging.debug(f"Processing webhook event: {event}")
    try:
        record = event["Records"][0]
        s3 = record["s3"]
        bucket = s3["bucket"]["name"]
        key = s3["object"]["key"]

        if not bucket or not key:
            raise ValueError("Missing bucket or key in S3 event")

        location = f"{key}"
        metainfo = str({"bucket": bucket, "key": key})

        dsx_logging.info(f"Received S3 event for {location}")
        response = await connector.scan_file_request(
            ScanRequestModel(location=location, metainfo=metainfo)
        )

        return StatusResponse(
            status=response.status,
            message="S3 webhook processed",
            description=f"Scan request sent for {location}"
        )
    except (KeyError, IndexError, TypeError) as parse_err:
        dsx_logging.error(f"Malformed S3 event payload: {parse_err}")
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Invalid S3 event format",
            description=str(parse_err)
        )
    except Exception as e:
        dsx_logging.error(f"Unexpected error in webhook handler: {e}", exc_info=True)
        return StatusResponse(
            status=StatusResponseEnum.ERROR,
            message="Internal error during webhook handling",
            description=str(e)
        )


@connector.config
async def config_handler():
    # override this with any specific configuration details you want to add
    return {
        "connector_name": connector.connector_name,
        "connector_id": connector.connector_id,
        "uuid": connector.uuid,
        "dsx_connect_url": connector.dsx_connect_url,
        "asset": config.asset,
        "filter": config.filter,
        "version": CONNECTOR_VERSION
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("connectors.framework.dsx_connector:connector_api", host="0.0.0.0",
                port=8591, reload=False)
