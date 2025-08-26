from pydantic_settings import BaseSettings
from typing import Optional


class APIKeySettings(BaseSettings):
    api_key: Optional[str] = None

    class Config:
        env_prefix = "DSXCONNECTOR_"
        env_file = ".env"
