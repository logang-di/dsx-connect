# connector_registration.py - All sync, much cleaner
import json
import redis
import time
from dsx_connect.config import config
from dsx_connect.models.connector_models import ConnectorInstanceModel
from fastapi.encoders import jsonable_encoder
from dsx_connect.utils.logging import dsx_logging

REDIS_KEY_PREFIX = "dsx:connector"


def register_or_refresh_connector_from_redis(connector_instance: ConnectorInstanceModel, ttl: int = 10):
    """Simple sync function - no async complexity"""
    client = redis.Redis.from_url(config.redis_url, decode_responses=False)
    key = f"{REDIS_KEY_PREFIX}:{connector_instance.uuid}"

    try:
        exists = client.exists(key)

        if not exists:
            dsx_logging.info(f"Registering NEW connector: {connector_instance.name} ({connector_instance.uuid})")
            client.set(key, json.dumps(jsonable_encoder(connector_instance)), ex=ttl)

            subscriber_count = client.publish("connector_registered", json.dumps(jsonable_encoder(connector_instance)))
            dsx_logging.info(f"Published connector registration to {subscriber_count} subscribers")

            if subscriber_count == 0:
                dsx_logging.warning("No subscribers found for 'connector_registered' channel!")

            return "registered"
        else:
            dsx_logging.debug(f"Refreshing TTL for existing connector: {connector_instance.name}")
            client.expire(key, ttl)
            return "refreshed"

    except Exception as e:
        dsx_logging.error(f"Redis operation failed for connector {connector_instance.uuid}: {e}", exc_info=True)
        raise
    finally:
        client.close()


# Update connector_registration.py

def unregister_connector_from_redis(uuid: str):
    """Simple sync function with unregister event"""
    client = redis.Redis.from_url(config.redis_url, decode_responses=False)
    try:
        # First try to get connector info before deleting (for better notifications)
        key = f"{REDIS_KEY_PREFIX}:{uuid}"
        connector_data = client.get(key)

        deleted_count = client.delete(key)

        if deleted_count > 0:
            dsx_logging.info(f"Unregistered connector {uuid} from Redis")

            # Try to get connector name for better notification
            connector_name = "Unknown"
            if connector_data:
                try:
                    parsed_data = json.loads(connector_data)
                    connector_name = parsed_data.get("name", "Unknown")
                except:
                    pass

            # Publish unregister event for immediate frontend cleanup
            unregister_event = {
                "type": "unregistered",
                "uuid": str(uuid),
                "name": connector_name,
                "timestamp": time.time()
            }
            subscriber_count = client.publish("connector_registered", json.dumps(unregister_event))
            dsx_logging.info(f"Published connector unregistration to {subscriber_count} subscribers")

        else:
            # Don't log as warning - this is normal if key expired or was already cleaned up
            dsx_logging.debug(f"Connector {uuid} key not found (may have already expired)")

    except Exception as e:
        dsx_logging.error(f"Failed to unregister connector {uuid}: {e}")
        raise
    finally:
        client.close()