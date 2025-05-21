import io
import logging
import pathlib
import hashlib
import os

from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceNotFoundError

from dsx_connect.utils import file_ops
from dsx_connect.utils.logging import dsx_logging
import tenacity

CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', 1024 * 1024))


class AzureBlobClient:
    def __init__(self, connection_string: str = None):
        self._chunk_size = CHUNK_SIZE
        if not connection_string:
            connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not connection_string:
            dsx_logging.error("AzureBlobClient must be initialized with an AZURE_STORAGE_CONNECTION_STRING")
            raise ValueError("Missing Azure Storage connection string.")
        self.service_client = BlobServiceClient.from_connection_string(connection_string)
        dsx_logging.info("Initialized AzureBlobClient with given connection string")

    def containers(self) -> list:
        return [container.name for container in self.service_client.list_containers()]

    def key_exists(self, container: str, blob_name: str) -> bool:
        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            return blob_client.exists()
        except Exception as e:
            dsx_logging.error(f"Error checking if blob exists: {e}")
            raise

    def delete_blob(self, container: str, blob_name: str) -> bool:
        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            blob_client.delete_blob()
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
    def get_blob(self, container: str, blob_name: str) -> io.BytesIO:
        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            stream = blob_client.download_blob()
            content = io.BytesIO(stream.readall())
            if len(content.getvalue()) == 0:
                raise ValueError(f"Retrieved blob {blob_name} is empty.")
            content.seek(0)
            return content
        except Exception as e:
            dsx_logging.error(f"Error downloading blob: {e}")
            raise

    def keys(self, container: str, prefix: str = '', recursive: bool = True):
        try:
            container_client = self.service_client.get_container_client(container)
            blob_list = container_client.list_blobs(name_starts_with=prefix)
            for blob in blob_list:
                if recursive or '/' not in blob.name[len(prefix):].strip('/'):
                    yield {'Key': blob.name, 'Size': blob.size}
        except Exception as e:
            dsx_logging.error(f"Error listing blobs: {e}")
            raise

    def move_blob(self, src_container: str, src_blob: str, dest_container: str, dest_blob: str) -> bool:
        try:
            src_client = self.service_client.get_blob_client(container=src_container, blob=src_blob)
            dest_client = self.service_client.get_blob_client(container=dest_container, blob=dest_blob)

            dest_client.start_copy_from_url(src_client.url)
            src_client.delete_blob()
            return True
        except Exception as e:
            dsx_logging.error(f"Error moving blob: {e}")
            return False

    def upload_bytes(self, content: io.BytesIO, container: str, blob_name: str):
        blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
        blob_client.upload_blob(content.getvalue(), overwrite=True)

    def upload_file(self, filepath: pathlib.Path, container: str, blob_name: str):
        try:
            content = file_ops.read_file(filepath)
            self.upload_bytes(content, container, blob_name)
        except Exception as e:
            dsx_logging.error(f"Error uploading file {filepath} to container {container}: {e}")
            raise

    def upload_folder(self, folder: pathlib.Path, container: str, recursive: bool = True):
        file_paths = file_ops.get_filepaths(folder, recursive=recursive)
        for path in file_paths:
            if path.is_file():
                self.upload_file(path, container, path.name)

    def calculate_sha256(self, container: str, blob_name: str) -> str:
        try:
            content = self.get_blob(container, blob_name)
            sh = hashlib.sha256()
            for chunk in iter(lambda: content.read(self._chunk_size), b''):
                sh.update(chunk)
            return sh.hexdigest()
        except Exception as e:
            msg = f"Error retrieving {blob_name} from {container} and calculating hash: {e}"
            dsx_logging.error(msg)
            raise FileNotFoundError(msg)

    def tag_blob(self, container: str, blob_name: str, tags: dict = None) -> bool:
        try:
            blob_client = self.service_client.get_blob_client(container=container, blob=blob_name)
            blob_client.set_blob_metadata(metadata=tags or {"scanned": "true"})
            return True
        except Exception as e:
            dsx_logging.error(f"Error tagging blob {blob_name}: {e}")
            return False

    def test_connection(self, container: str) -> bool:
        try:
            return self.service_client.get_container_client(container).exists()
        except Exception as e:
            dsx_logging.error(f"Error testing Azure Blob container connection: {e}")
            raise


# for testing only
if __name__ == "__main__":
    ab_client = AzureBlobClient()

    keys = ab_client.keys(container="lg-test-01", recursive=True, prefix="sub1")
    for key in keys:
        print(f"Key {key['Key']}")
