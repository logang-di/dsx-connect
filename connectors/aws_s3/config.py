from pydantic import HttpUrl, Field
from pydantic_settings import BaseSettings
from dsx_connect.models.connector_models import ItemActionEnum


class AWSS3ConnectorConfig(BaseSettings):
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
    name: str = 'aws-s3-connector'
    connector_url: HttpUrl = Field(default="http://0.0.0.0:8591",
                                   description="Base URL (http(s)://ip.add.ddr.ess|URL:port) of this connector entry point")
    dsx_connect_url: HttpUrl = Field(default="http://0.0.0.0:8586",
                                     description="Complete URL (http(s)://ip.add.ddr.ess|URL:port) of the dsxa entry point")
    item_action: ItemActionEnum = ItemActionEnum.MOVE
    item_action_move_metainfo: str = "dsxconnect-quarantine"

    test_mode: bool = False

    # define the asset this connector can perform full scan on... may also be used to filter on access scanning (webhook events)
    asset: str = "lg-test-02"
    filter: str = ""
    recursive: bool = True

    ### Connector specific configuration
    s3_endpoint_url: str | None = None
    s3_endpoint_verify: bool = True


class Config:
    env_prefix = "DSXCONNECTOR_"
    env_file = ".env"
    env_file_encoding = "utf-8"
    extra = "forbid"


# Singleton with reload capability
class ConfigManager:
    _config: AWSS3ConnectorConfig = None

    @classmethod
    def get_config(cls) -> AWSS3ConnectorConfig:
        if cls._config is None:
            cls._config = AWSS3ConnectorConfig()
        return cls._config

    @classmethod
    def reload_config(cls) -> AWSS3ConnectorConfig:
        cls._config = AWSS3ConnectorConfig()
        return cls._config


config = ConfigManager.get_config()
