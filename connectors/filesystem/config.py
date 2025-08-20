import pathlib

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings

from connectors.framework.base_config import BaseConnectorConfig
from dsx_connect.models.connector_models import ItemActionEnum
# from dsx_connect.utils.file_ops import _tokenize_filter
from shared.dsx_logging import dsx_logging


class FilesystemConnectorConfig(BaseConnectorConfig):
    """
    Configuration for connector.  Note that configuration is a pydantic base setting class, so we get the benefits of
    type checking, as well as code completion in an IDE.  pydantic settings also allows for overriding these default
    settings via environment variables or a .env file.

    If you wish to add a prefix to the environment variable overrides, change the value of env_prefix below.

    Example:
        env_prefix = "DSXCONNECTOR_"
        ...
        export DSXCONNECTOR_LOCATION = 'some path'

    You can also read in an optional .env file, which will be ignored is not available
    """
    name: str = 'filesystem-connector'
    connector_url: HttpUrl = Field(default="http://0.0.0.0:8590",
                                   description="Base URL (http(s)://ip.add.ddr.ess|URL:port) of this connector entry point")
    dsx_connect_url: HttpUrl = Field(default="http://0.0.0.0:8586/",
                                     description="Complete URL (http(s)://ip.add.ddr.ess|URL:port) of the dsxa entry point")
    item_action: ItemActionEnum = ItemActionEnum.NOTHING
    item_action_move_metainfo: str = "dsxconnect-quarantine"

    # asset: str = Field("/Users/logangilbert/Documents/SAMPLES",
    #                    description="Directory to scan for files")
    # filter: str = "test/2025*/*"
    asset: str = Field("/Users/logangilbert/Documents/SAMPLES",
                       description="Directory to scan for files")
    filter: str = "PDF"
    scan_by_path: bool = False

    ## Config settings specific to this Connector
    asset_display_name: str = "" # filessytem connector poses an issue for frontend since
    # the asset is actually map to folder on the connector's running container/pod, which is mapped to a folder on the host.
    # what we ant to display on the frontend, is the host folder, not the app/scan_folder.
    monitor: bool = False # if true, Connector will monitor location for new or modified files.

    # @field_validator("filter")
    # @classmethod
    # def validate_filter(cls, v: str) -> str:
    #     # cheap sanity (e.g., no unbalanced quotes, etc.)
    #     # or even call _tokenize_filter(v) here to ensure it parses
    #     try:
    #         _tokenize_filter(v)
    #         return v
    #     except Exception as e:
    #         # log a warning and return empty (scan all)
    #         dsx_logging.warning(f"Ignoring malformed filter '{v}': {e}")
    #         return ""

    class Config:
        env_prefix = "DSXCONNECTOR_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "forbid"


# Singleton with reload capability
class ConfigManager:
    _config: FilesystemConnectorConfig = None

    @classmethod
    def get_config(cls) -> FilesystemConnectorConfig:
        if cls._config is None:
            cls._config = FilesystemConnectorConfig()
        return cls._config

    @classmethod
    def reload_config(cls) -> FilesystemConnectorConfig:
        cls._config = FilesystemConnectorConfig()
        return cls._config


config = ConfigManager.get_config()
