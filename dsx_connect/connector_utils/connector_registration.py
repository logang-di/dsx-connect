# utils/connector_registry.py
import json
from redis.asyncio import Redis
from dsx_connect.config import config
from dsx_connect.models.connector_models import ConnectorInstanceModel
from fastapi.encoders import jsonable_encoder


REDIS_KEY_PREFIX = "dsx:connector"


async def register_or_refresh_connector_from_redis(connector_model: ConnectorInstanceModel, ttl: int = 10):
    redis = Redis.from_url(config.redis_url)
    key = f"{REDIS_KEY_PREFIX}:{connector_model.uuid}"
    exists = await redis.exists(key)

    if not exists:
        await redis.set(key, json.dumps(jsonable_encoder(connector_model)), ex=ttl)
        await redis.publish("connector_registered", json.dumps(jsonable_encoder(connector_model)))
        return "registered"
    else:
        await redis.expire(key, ttl)
        return "refreshed"


async def unregister_connector_from_redis(uuid: str):
    redis = Redis.from_url(config.redis_url)
    await redis.delete(f"dsx:connector:{uuid}")