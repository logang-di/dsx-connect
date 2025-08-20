from pydantic import BaseModel
from enum import Enum

from dsx_connect.models.connector_models import ItemActionEnum


class StatusResponseEnum(str, Enum):
    SUCCESS: str = 'success'
    ERROR: str = 'error'
    NOTHING: str = 'nothing'


class StatusResponse(BaseModel):
    status: StatusResponseEnum
    message: str
    description: str | None = None
    id: str | None = None


class ItemActionStatusResponse(StatusResponse):
    item_action: ItemActionEnum = ItemActionEnum.NOTHING
