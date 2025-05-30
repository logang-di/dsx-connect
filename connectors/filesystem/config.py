import pathlib

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings
from dsx_connect.models.connector_models import ItemActionEnum


class FilesystemConnectorConfig(BaseSettings):
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
    item_action: ItemActionEnum = ItemActionEnum.NOTHING
    dsx_connect_url: HttpUrl = Field(default="http://0.0.0.0:8586/",
                                     description="Complete URL (http(s)://ip.add.ddr.ess|URL:port) of the dsxa entry point")
    test_mode: bool = True

    ## Config settings specific to this Connector
    location: pathlib.Path = Field(default=pathlib.Path("/Users/logangilbert/Documents/SAMPLES/1SAMPLES"),
                                   description="Directory to scan for files")
    monitor: bool = False # if true, Connector will monitor location for new or modified files.
    scan_existing: bool = Field(default=False, description="If True, scan existing files in location on startup")
    recursive: bool = Field(default=True, description="If True, scan subdirectories recursively")
    item_action_move_dir: pathlib.Path = Field(default=pathlib.Path("/Users/logangilbert/Documents/SAMPLES/quarantine"),
                                               description="Directory to move files when item_action is MOVE")

    @field_validator("location", "item_action_move_dir")
    def validate_location(cls, v):
        if not v.is_dir():
            raise ValueError(f"Path {v} must be a valid directory")
        return v

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


