from pydantic import HttpUrl, Field
from pydantic_settings import BaseSettings

from connectors.framework.base_config import BaseConnectorConfig
from shared.models.connector_models import ItemActionEnum


class GoogleCloudStorageConnectorConfig(BaseConnectorConfig):
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
    name: str = 'google-cloud-storage-connector'
    connector_url: HttpUrl = Field(default="http://0.0.0.0:8595",
                                   # dsx-connect is running within dockers, and this connector is being run on the docker's host system
                                   description="Base URL (http(s)://ip.add.ddr.ess|URL:port) of this connector entry point")
    # connector_url: HttpUrl = Field(default="http://host.docker.internal:8595", # dsx-connect is running within dockers, and this connector is being run on the docker's host system
    #                                description="Base URL (http(s)://ip.add.ddr.ess|URL:port) of this connector entry point")
    dsx_connect_url: HttpUrl = Field(default="http://0.0.0.0:8586",
                                     description="Complete URL (http(s)://ip.add.ddr.ess|URL:port) of the dsxa entry point")
    # dsx_connect_url: HttpUrl = Field(default="http://dsx-connect.127.0.0.1.nip.io:8080",
    #                                  description="Complete URL (http(s)://ip.add.ddr.ess|URL:port) of the dsxa entry point")

    item_action: ItemActionEnum = ItemActionEnum.TAG
    item_action_move_metainfo: str = "dsxconnect-quarantine"

    asset: str = "lg-test-01"
    filter: str = ""
    recursive: bool = True

    test_mode: bool = False

    class Config:
        env_prefix = "DSXCONNECTOR_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "forbid"


# Singleton with reload capability
class ConfigManager:
    _config: GoogleCloudStorageConnectorConfig = None

    @classmethod
    def get_config(cls) -> GoogleCloudStorageConnectorConfig:
        if cls._config is None:
            cls._config = GoogleCloudStorageConnectorConfig()
        return cls._config

    @classmethod
    def reload_config(cls) -> GoogleCloudStorageConnectorConfig:
        cls._config = GoogleCloudStorageConnectorConfig()
        return cls._config


config = ConfigManager.get_config()
