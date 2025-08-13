import io
import logging
import pathlib
import hashlib
import os
import ssl

import certifi
from azure.core.pipeline.transport._aiohttp import AioHttpTransport
from azure.storage.blob.aio import BlobServiceClient as AsyncBlobServiceClient
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceNotFoundError

from typing import AsyncIterator

from dsx_connect.utils import file_ops
from dsx_connect.utils.app_logging import dsx_logging
import tenacity

CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', 1024 * 1024))


class AzureBlobAsyncClient:
    def __init__(self, connection_string: str = None):
        self._chunk_size = CHUNK_SIZE
        if not connection_string:
            connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connection_string:
            dsx_logging.error("AzureBlobAsyncClient must be initialized with an AZURE_STORAGE_CONNECTION_STRING")
            raise ValueError("Missing Azure Storage connection string.")
        self.connection_string = connection_string
        self.service_client: AsyncBlobServiceClient = None

    async def init(self):
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        transport = AioHttpTransport(ssl_context=ssl_context)
        self.service_client = AsyncBlobServiceClient.from_connection_string(
            self.connection_string, transport=transport
        )
        dsx_logging.info("Initialized AzureBlobAsyncClient with given connection string")

    async def containers(self) -> list[str]:
        """
        Return the names of all containers in the storage account.
        """
        if self.service_client is None:
            raise RuntimeError("AzureBlobAsyncClient not initialized; call await init() first")

        names: list[str] = []
        async for container_props in self.service_client.list_containers():
            names.append(container_props.name)
        return names

    async def key_exists(self, container: str, blob_name: str) -> bool:
        if self.service_client is None:
            raise RuntimeError("AzureBlobAsyncClient not initialized; call await init() first")

        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            return await blob_client.exists()
        except Exception as e:
            dsx_logging.error(f"Error checking if blob exists: {e}")
            raise

    async def delete_blob(self, container: str, blob_name: str) -> bool:
        if self.service_client is None:
            raise RuntimeError("AzureBlobAsyncClient not initialized; call await init() first")

        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            await blob_client.delete_blob()
            return True
        except ResourceNotFoundError:
            return False
        except Exception as e:
            dsx_logging.error(f"Error deleting blob: {e}")
            raise

    @tenacity.retry(stop=tenacity.stop_after_attempt(3),
                    wait=tenacity.wait_exponential(multiplier=1, min=1, max=10),
                    reraise=True,
                    before_sleep=tenacity.before_sleep_log(dsx_logging, logging.WARN))
    async def get_blob(self, container: str, blob_name: str) -> io.BytesIO:
        if self.service_client is None:
            raise RuntimeError("AzureBlobAsyncClient not initialized; call await init() first")

        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            stream = await blob_client.download_blob()
            content = io.BytesIO(await stream.readall())
            if len(content.getvalue()) == 0:
                raise ValueError(f"Retrieved blob {blob_name} is empty.")
            content.seek(0)
            return content
        except Exception as e:
            dsx_logging.error(f"Error downloading blob asynchronously: {e}")
            raise

    async def keys(self, container: str, prefix: str = '', recursive: bool = True) -> AsyncIterator[dict[str, int]]:
        """
        Asynchronously list blobs in a container.

        Yields dicts with 'Key' and 'Size' for each blob that matches the prefix.
        If recursive is False, it will only return “top‐level” blobs under that prefix.
        """
        if self.service_client is None:
            raise RuntimeError("AzureBlobAsyncClient not initialized; call await init() first")

        container_client = self.service_client.get_container_client(container)
        try:
            # list_blobs is an async iterator in the aio SDK
            async for blob_props in container_client.list_blobs(name_starts_with=prefix):
                # if not recursive, skip blobs with additional “/” segments
                suffix = blob_props.name[len(prefix):].lstrip('/')  # remove leading slash
                if recursive or '/' not in suffix:
                    yield {'Key': blob_props.name, 'Size': blob_props.size}
        except Exception as e:
            dsx_logging.error(f"Error listing blobs asynchronously: {e}")
            raise

    async def move_blob(self, src_container: str, src_blob: str, dest_container: str, dest_blob: str) -> bool:
        if self.service_client is None:
            raise RuntimeError("AzureBlobAsyncClient not initialized; call await init() first")

        try:
            src_client = self.service_client.get_blob_client(container=src_container, blob=src_blob)
            dest_client = self.service_client.get_blob_client(container=dest_container, blob=dest_blob)

            await dest_client.start_copy_from_url(src_client.url)
            await src_client.delete_blob()
            return True
        except Exception as e:
            dsx_logging.error(f"Error moving blob: {e}")
            return False

    # def upload_bytes(self, content: io.BytesIO, container: str, blob_name: str):
    #     blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
    #     blob_client.upload_blob(content.getvalue(), overwrite=True)
    #
    # def upload_file(self, filepath: pathlib.Path, container: str, blob_name: str):
    #     try:
    #         content = file_ops.read_file(filepath)
    #         self.upload_bytes(content, container, blob_name)
    #     except Exception as e:
    #         dsx_logging.error(f"Error uploading file {filepath} to container {container}: {e}")
    #         raise
    #
    # def upload_folder(self, folder: pathlib.Path, container: str, recursive: bool = True):
    #     file_paths = file_ops.get_filepaths(folder, recursive=recursive)
    #     for path in file_paths:
    #         if path.is_file():
    #             self.upload_file(path, container, path.name)
    #
    # def calculate_sha256(self, container: str, blob_name: str) -> str:
    #     try:
    #         content = self.get_blob(container, blob_name)
    #         sh = hashlib.sha256()
    #         for chunk in iter(lambda: content.read(self._chunk_size), b''):
    #             sh.update(chunk)
    #         return sh.hexdigest()
    #     except Exception as e:
    #         msg = f"Error retrieving {blob_name} from {container} and calculating hash: {e}"
    #         dsx_logging.error(msg)
    #         raise FileNotFoundError(msg)

    async def tag_blob(self, container: str, blob_name: str, tags: dict = None) -> bool:
        if self.service_client is None:
            raise RuntimeError("AzureBlobAsyncClient not initialized; call await init() first")

        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            await blob_client.set_blob_metadata(metadata=tags or {"scanned": "true"})
            return True
        except Exception as e:
            dsx_logging.error(f"Error tagging blob {blob_name}: {e}")
            return False

    async def test_connection(self, container: str) -> bool:
        try:
            return await self.service_client.get_container_client(container).exists()
        except Exception as e:
            dsx_logging.error(f"Error testing Azure Blob container connection: {e}")
            raise


# for testing only
# if __name__ == "__main__":
#     ab_client = AzureBlobAsyncClient()
#
#     keys = ab_client.keys(container="lg-test-01", recursive=True, prefix="sub1")
#     for key in keys:
#         print(f"Key {key['Key']}")
