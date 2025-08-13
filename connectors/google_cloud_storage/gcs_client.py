from google.cloud import storage
from google.api_core.exceptions import NotFound, GoogleAPIError
import io, hashlib, pathlib, os
from dsx_connect.utils import file_ops
from dsx_connect.utils.app_logging import dsx_logging

CHUNK_SIZE = int(os.getenv('CHUNK_SIZE', 1024 * 1024))


class GCSClient:
    def __init__(self):
        self.client = storage.Client()
        dsx_logging.debug("Initialized GCS client")

    def buckets(self):
        return [bucket.name for bucket in self.client.list_buckets()]

    def key_exists(self, bucket: str, key: str) -> bool:
        try:
            blob = self.client.bucket(bucket).blob(key)
            return blob.exists()
        except GoogleAPIError as e:
            dsx_logging.error(f"GCS key_exists error: {e}")
            raise

    def keys(self, bucket: str, prefix: str = '', recursive: bool = False, include_folders: bool = False) -> list[dict]:
        """
        List objects in a GCS bucket

        Args:
            bucket (str): The GCS bucket name.
            prefix (str): Prefix to filter objects.  for example, if bucketA has subfolders sub1, and in that is sub2
            set prefix to: "sub1/sub2" (without a leading /)
            recursive (bool): If False, emulate folder-level listing (using '/').
            include_folders (bool): If True, return "folders" as keys as well
        Returns:
            list[dict]: List of objects with 'Key' and 'Size'.
        """
        delimiter = None if recursive else '/'
        blobs = self.client.list_blobs(bucket, prefix=prefix, delimiter=delimiter)

        for blob in blobs:
            if not include_folders and blob.name.endswith('/'):
                continue
            yield {'Key': blob.name, 'Size': blob.size}

    def get_object(self, bucket: str, key: str) -> io.BytesIO:
        try:
            blob = self.client.bucket(bucket).blob(key)
            content = io.BytesIO()
            blob.download_to_file(content)
            content.seek(0)
            return content
        except NotFound:
            raise FileNotFoundError(f"{key} not found in bucket {bucket}")
        except Exception as e:
            dsx_logging.error(f"GCS get_object error: {e}")
            raise

    def delete_object(self, bucket: str, key: str) -> bool:
        try:
            blob = self.client.bucket(bucket).blob(key)
            blob.delete()
            return True
        except NotFound:
            return False
        except Exception as e:
            dsx_logging.error(f"GCS delete_object error: {e}")
            raise

    def move_object(self, src_bucket: str, src_key: str, dest_bucket: str, dest_key: str) -> bool:
        try:
            source_bucket = self.client.bucket(src_bucket)
            source_blob = source_bucket.blob(src_key)
            destination_bucket = self.client.bucket(dest_bucket)

            # Copy using the destination bucket's method
            destination_bucket.copy_blob(source_blob, destination_bucket, dest_key)

            # Delete the source blob
            source_blob.delete()

            return True
        except Exception as e:
            dsx_logging.error(f"GCS move_object error: {e}")
            return False

    def tag_object(self, bucket: str, key: str, tags: dict = None) -> bool:
        try:
            blob = self.client.bucket(bucket).blob(key)
            blob.metadata = tags or {'scanned': 'true'}
            blob.patch()
            return True
        except Exception as e:
            dsx_logging.error(f"GCS tag_object error: {e}")
            return False

    def upload_bytes(self, content: io.BytesIO, key: str, bucket: str):
        blob = self.client.bucket(bucket).blob(key)
        content.seek(0)
        blob.upload_from_file(content)

    def upload_file(self, filepath: pathlib.Path, key: str, bucket: str):
        try:
            blob = self.client.bucket(bucket).blob(key)
            blob.upload_from_filename(str(filepath))
        except Exception as e:
            dsx_logging.error(f"GCS upload_file error: {e}")
            raise

    def upload_folder(self, folder: pathlib.Path, bucket: str, recursive: bool = True):
        for path in file_ops.get_filepaths(folder, recursive=recursive):
            if path.is_file():
                self.upload_file(path, path.name, bucket)

    def calculate_sha256(self, bucket: str, key: str) -> str:
        try:
            content = self.get_object(bucket, key)
            sh = hashlib.sha256()
            for chunk in iter(lambda: content.read(CHUNK_SIZE), b''):
                sh.update(chunk)
            return sh.hexdigest()
        except Exception as e:
            raise FileNotFoundError(f"SHA256 calc error: {e}")

    def test_gcs_connection(self, bucket: str) -> bool:
        try:
            bucket_obj = self.client.bucket(bucket)
            blobs = list(self.client.list_blobs(bucket_obj, max_results=1))
            return True  # If no exception, access works (even if no blobs exist)
        except Exception as e:
            dsx_logging.error(f"Failed to connect to GCS bucket {bucket}: {e}")
        return False


# for testing only
if __name__ == "__main__":
    gcs_client = GCSClient()

    keys = gcs_client.keys(bucket="lg-test-01", recursive=True, prefix="sub1/sub2")
    for key in keys:
        print(f"Key {key['Key']}")
