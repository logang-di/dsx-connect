import os
import uuid
from pathlib import Path

UUID_FILE_PATH = Path("data/connector_uuid.txt")


def get_or_create_connector_uuid() -> str:
    """
    Returns a stable UUID for the connector. If the UUID file exists, it reads and returns it.
    If the file is missing, it generates a new UUID, saves it, and returns it.
    """
    UUID_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    if UUID_FILE_PATH.exists():
        return UUID_FILE_PATH.read_text().strip()

    new_uuid = str(uuid.uuid4())
    UUID_FILE_PATH.write_text(new_uuid)
    return new_uuid
