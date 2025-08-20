# dsx_connect/messaging/notifiers.py
from __future__ import annotations
from dataclasses import dataclass
from typing import AsyncIterator
import json, time
from fastapi.encoders import jsonable_encoder
from .topics import Topics
from .bus import Bus  # your class-based async bus

@dataclass(slots=True)
class Notifiers:
    bus: Bus

    # -------- publish ---------------------------------------------------------
    async def publish_scan_results(self, scan_result) -> int:
        payload = json.dumps(jsonable_encoder(scan_result), separators=(",", ":"))
        return await self.bus.publish(Topics.NOTIFY_SCAN_RESULT, payload)

    async def publish_connector_notify(self, *, event: str, uuid: str, name: str, url: str) -> int:
        payload = json.dumps(
            {"type": event, "uuid": uuid, "name": name, "url": url, "ts": time.time()},
            separators=(",", ":"),
        )
        return await self.bus.publish(Topics.NOTIFY_CONNECTORS, payload)

    # -------- subscribe (yields parsed JSON dicts) ----------------------------
    async def subscribe_scan_results(self) -> AsyncIterator[dict]:
        async for raw in self.bus.listen(Topics.NOTIFY_SCAN_RESULT):
            try:
                yield json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
            except Exception:
                continue  # drop bad frames

    async def subscribe_connector_notify(self) -> AsyncIterator[dict]:
        async for raw in self.bus.listen(Topics.NOTIFY_CONNECTORS):
            try:
                yield json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
            except Exception:
                continue
