import asyncio
import io
import logging
import pathlib
import hashlib
import os
from functools import partial

from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceNotFoundError

from dsx_connect.utils import file_ops, async_ops
from shared.dsx_logging import dsx_logging
import tenacity
import os, base64, binascii
from azure.storage.blob import BlobServiceClient
from azure.core.credentials import AzureNamedKeyCredential

CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', 1024 * 1024))




def _clean(s: str) -> str:
    # Undo common IDE artifacts
    return s.strip().strip('"').strip("'").replace("\\;", ";")

def _maybe_b64_decode(s: str) -> str:
    try:
        raw = base64.b64decode(s, validate=True).decode("utf-8")
        # sanity check that it looks like a connection string
        if "AccountName=" in raw and ";" in raw:
            return raw
    except (binascii.Error, UnicodeDecodeError):
        pass
    return s

def load_blob_service_client() -> BlobServiceClient:
    # 1) plain env
    conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    if conn:
        try:
            return BlobServiceClient.from_connection_string(_clean(conn))
        except Exception:
            pass  # fall through to b64/file/pair

    # 2) base64 env (preferred key name)
    decoded = _maybe_b64_decode(conn)
    if conn:
        try:
            return BlobServiceClient.from_connection_string(_clean(decoded))
        except Exception:
            pass

    # 3) name/key pair (no semicolons at all)
    name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    key  = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
    if name and key:
        cred = AzureNamedKeyCredential(name, key)
        return BlobServiceClient(account_url=f"https://{name}.blob.core.windows.net", credential=cred)


    raise RuntimeError("No Azure Storage credentials found")



class AzureBlobClient:
    def __init__(self, connection_string: str = None):
        self._chunk_size = CHUNK_SIZE
        # if not connection_string:
        #     connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        # if not connection_string:
        #     dsx_logging.error("AzureBlobClient must be initialized with an AZURE_STORAGE_CONNECTION_STRING")
        #     raise ValueError("Missing Azure Storage connection string.")

        self.service_client = load_blob_service_client()
        # self.service_client = BlobServiceClient.from_connection_string(connection_string)
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
        blob_client = self.service_client.get_blob_client(
            container=container, blob=blob_name
        )
        # ask the SDK to download in 8 parallel ranges
        downloader = blob_client.download_blob(max_concurrency=8)
        content = io.BytesIO()
        # stream the chunks into your BytesIO
        for chunk in downloader.chunks():
            content.write(chunk)
        content.seek(0)
        return content

    async def get_blob_async(self, container: str, blob_name: str) -> io.BytesIO:
        loop = asyncio.get_running_loop()
        # partial(self._get_blob_sync, ...) fixes the first two args
        return await loop.run_in_executor(
            None,
            partial(self.get_blob, container, blob_name)
        )



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
