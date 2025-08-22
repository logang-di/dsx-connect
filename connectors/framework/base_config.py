from pydantic import HttpUrl, Field
from pydantic_settings import BaseSettings
from dsx_connect.models.connector_models import ItemActionEnum


class BaseConnectorConfig(BaseSettings):
    """
    Configuration for connector.  Note that configuration is a pydantic base setting class, so we get the benefits of
    type checking, as well as code completion in an IDE.  pydantic settings also allows for overriding these default
    settings via environment variables or a .env file.

    If you wish to add a prefix to the environment variable overrides, change the value of env_prefix below.

    Example:
        env_prefix = "DSXCONNECTOR_"
        ...
        export DSXCONNECTOR_LOCATION = 'some path'

    You can also read in an optional .env file, which will be ignored if not available
    """
    name: str = 'dsx-connector'
    connector_url: HttpUrl = Field(
        default="http://0.0.0.0:8588",
        description="Base URL (http(s)://ip.add.ddr.ess|URL:port) of this connector entry point"
    )
    dsx_connect_url: HttpUrl = Field(
        default="http://0.0.0.0:8586",
        description="Complete URL (http(s)://ip.add.ddr.ess|URL:port) of the dsx-connect entry point"
    )
    item_action: ItemActionEnum = ItemActionEnum.NOTHING
    item_action_move_metainfo: str = "dsxconnect-quarantine"
    asset: str = ""
    filter: str = ""

    class Config:
        env_prefix = "DSXCONNECTOR_"
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "forbid"
