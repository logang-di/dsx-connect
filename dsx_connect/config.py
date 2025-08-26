# dsx_connect/config.py
from enum import Enum
from pathlib import Path
from typing import Final

from pydantic import AnyUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.dev_env import load_devenv


class AppEnv(str, Enum):
    dev = "dev"
    stg = "stg"
    prod = "prod"


class AuthConfig(BaseSettings):

    enrollment_token: str = "dev-enroll"

    jwt_secret: str = "dev-change-me"
    jwt_audience: str = "dsx-connect"
    jwt_issuer: str = "dsx-connect"
    jwt_ttl: int = 900  # 15m

    hmac_max_skew: int = 60

    class Config:
        env_prefix = "DSXCONNECT_AUTH__"
        case_sensitive = False


class ConfigDatabaseType(str, Enum):
    MEMORY_COLLECTION = "memory"
    TINYDB = "tinydb"
    SQLITE3 = "sqlite3"
    MONGODB = "mongodb"


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    type: str = ConfigDatabaseType.SQLITE3
    loc: str = "data/dsx-connect.db.sql"
    retain: int = 1000
    scan_stats_db: str = "data/scan-stats.db.json"


class ScannerConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    scan_binary_url: str = "http://0.0.0.0:8080/scan/binary/v2"


class SyslogConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    syslog_server_url: str = "127.0.0.1"
    syslog_server_port: int = 514


class CeleryTaskConfig(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="__")
    broker: AnyUrl = "redis://localhost:6379/5"
    backend: AnyUrl = "redis://localhost:6379/6"
    scan_request_max_retries: int = 2
    dlq_expire_after_days: int = 7
    connector_retry_backoff_base: int = 60
    dsxa_retry_backoff_base: int = 2
    server_error_retry_backoff_base: int = 30
    retry_connector_connection_errors: bool = True
    retry_connector_server_errors: bool = True
    retry_connector_client_errors: bool = False
    retry_dsxa_connection_errors: bool = True
    retry_dsxa_timeout_errors: bool = True
    retry_dsxa_server_errors: bool = True
    retry_dsxa_client_errors: bool = False
    retry_queue_dispatch_errors: bool = False


# class SecurityConfig(BaseSettings):
#     model_config = SettingsConfigDict(env_nested_delimiter="__")
#     item_action_severity_threshold: DPASeverityEnum = DPASeverityEnum.MEDIUM


class DSXConnectConfig(BaseSettings):

    app_env: AppEnv = AppEnv.dev

    database: DatabaseConfig = DatabaseConfig()
    scanner: ScannerConfig = ScannerConfig()
    workers: CeleryTaskConfig = CeleryTaskConfig()

    # App datastore / pubsub (not Celery)
    redis_url: AnyUrl = "redis://localhost:6379/3"
    syslog: SyslogConfig = SyslogConfig()

    # TLS/SSL for API server
    use_tls: bool = False
    tls_certfile: str | None = None
    tls_keyfile: str | None = None

    # Top-level settings config: env prefix + nested delimiter
    model_config = SettingsConfigDict(
        env_prefix="DSXCONNECT_",
        env_nested_delimiter="__",
    )


# Singleton accessor
from functools import lru_cache


@lru_cache
def get_config() -> DSXConnectConfig:
    load_devenv(Path(__file__).with_name('.devenv'))
    return DSXConnectConfig()

@lru_cache
def get_auth_config() -> AuthConfig:
    return AuthConfig()

def reload_config() -> DSXConnectConfig:
    get_config.cache_clear()
    load_devenv(Path(__file__).with_name('.devenv'))
    return get_config()


def app_env() -> str:
    # resolves to "dev" | "stg" | "prod"
    return get_config().app_env.value


# helper to grab the runtime environment
APP_ENV: Final = app_env()

