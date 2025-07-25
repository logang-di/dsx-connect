from enum import Enum
from uuid import UUID

from pydantic import BaseModel, HttpUrl, Field


class ItemActionEnum(str, Enum):
    NOT_IMPLEMENTED = 'not implemented'
    NOTHING = 'nothing'
    DELETE = 'delete'
    MOVE = 'move'
    TAG = 'tag'
    MOVE_TAG = 'movetag'


class ItemActionModel(BaseModel):
    action_type: ItemActionEnum = ItemActionEnum.NOTHING
    action_meta: str = None


class ConnectorStatusEnum(str, Enum):
    READY: str = 'ready'
    STARTING: str = 'starting'
    STOPPED: str = 'stopped'
    FAILED_INIT: str = 'failed'


class ConnectorModel(BaseModel):
    name: str = 'connector'
    uuid: UUID | None = None
    meta_info: str | None = None
    url: str = ''
    status: ConnectorStatusEnum = ConnectorStatusEnum.STARTING


class ScanRequestModel(BaseModel):
    connector: ConnectorModel = None
    location: str
    metainfo: str
    connector_url: str = None
