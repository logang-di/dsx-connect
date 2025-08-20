# dsx_connect/connectors/client.py
from contextlib import contextmanager, asynccontextmanager
import json, threading, asyncio
from typing import Optional, Mapping, Any, Literal, Tuple, Union

import httpx

from dsx_connect.config import APP_ENV, get_auth_config   # <- use DSX app env, not AuthConfig.app_env
from dsx_connect.security.hmac import make_hmac_header
from shared.routes import service_url

HttpMethod = Literal["GET","POST","PUT","PATCH","DELETE"]

# Pools
_sync_pool: dict[str, httpx.Client] = {}
_async_pool: dict[str, httpx.AsyncClient] = {}
_sync_lock = threading.Lock()
_async_lock = asyncio.Lock()

# Resolve environment once (it's a small helper over get_config())
DEV = APP_ENV == "dev"
_AUTH = get_auth_config()  # kept in case you later want defaults from auth settings

def _signed_headers(
        url: str,
        method: str,
        body: bytes,
        key_id: Optional[str],
        secret: Optional[str],
        dev: bool,
) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if dev:
        return headers

    # In prod, require per-connector credentials (or inject your fallback here)
    if not key_id or not secret:
        raise RuntimeError("Missing connector HMAC credentials")

    # Path + optional query for signature
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    path_q = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    headers["Authorization"] = make_hmac_header(key_id, secret, method, path_q, body)
    return headers


def _conn_parts(conn: Union[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    if isinstance(conn, str):
        return conn, None, None
    return (
        getattr(conn, "url", conn),
        getattr(conn, "hmac_key_id", None),
        getattr(conn, "hmac_secret", None),
    )


@asynccontextmanager
async def get_async_connector_client(conn):
    async with _async_lock:
        url, key_id, secret = _conn_parts(conn)
        http = _async_pool.setdefault(url, httpx.AsyncClient(verify=False, timeout=30.0))

    class AClient:
        async def request(self, method: HttpMethod, path: str,
                          json_body: Optional[Mapping[str, Any]] = None,
                          headers: Optional[dict[str, str]] = None) -> httpx.Response:
            full_url = service_url(url, path)
            body = b"" if json_body is None else json.dumps(json_body, separators=(",", ":")).encode()
            hdrs = _signed_headers(full_url, method, body, key_id, secret, DEV)
            if headers: hdrs.update(headers)
            return await http.request(method, full_url, content=(body or None), headers=hdrs)

        async def get(self, path, headers=None):
            return await self.request("GET", path, None, headers)

        async def post(self, path, json_body=None, headers=None):
            return await self.request("POST", path, json_body, headers)

        async def put(self, path, json_body=None, headers=None):
            return await self.request("PUT", path, json_body, headers)

        async def delete(self, path, headers=None):
            return await self.request("DELETE", path, None, headers)

        async def get_json(self, path):
            """Get JSON response. Raises httpx exceptions transparently."""
            response = await self.request("GET", path)
            response.raise_for_status()
            return response.json()

        async def post_json(self, path, json_body=None):
            """Post JSON and get JSON response. Raises httpx exceptions transparently."""
            response = await self.request("POST", path, json_body)
            response.raise_for_status()
            return response.json()

    try:
        yield AClient()
    finally:
        pass


@contextmanager
def get_connector_client(conn):
    with _sync_lock:
        url, key_id, secret = _conn_parts(conn)
        http = _sync_pool.setdefault(url, httpx.Client(verify=False, timeout=30.0))

    class SClient:
        def request(self, method: HttpMethod, path: str,
                    json_body: Optional[Mapping[str, Any]] = None,
                    headers: Optional[dict[str, str]] = None) -> httpx.Response:
            full_url = service_url(url, path)
            body = b"" if json_body is None else json.dumps(json_body, separators=(",", ":")).encode()
            hdrs = _signed_headers(full_url, method, body, key_id, secret, DEV)
            if headers: hdrs.update(headers)
            return http.request(method, full_url, content=(body or None), headers=hdrs)

        def get(self, path, headers=None):                 return self.request("GET", path, None, headers)
        def post(self, path, json_body=None, headers=None): return self.request("POST", path, json_body, headers)
        def put(self, path, json_body=None, headers=None):  return self.request("PUT", path, json_body, headers)
        def delete(self, path, headers=None):               return self.request("DELETE", path, None, headers)

        def get_json(self, path):            r = self.get(path); r.raise_for_status(); return r.json()
        def post_json(self, path, json_body=None): r = self.post(path, json_body); r.raise_for_status(); return r.json()

    try:
        yield SClient()
    finally:
        pass
