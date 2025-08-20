# dsx_connect/security/hmac.py
from __future__ import annotations
import base64, hashlib, hmac, os, time
from fastapi import Request, HTTPException
from dsx_connect.config import AuthConfig

_settings = AuthConfig()
_HEADER = "Authorization"

def _b64(h: bytes) -> str:
    return base64.b64encode(h).decode()

def make_hmac_header(key_id: str, secret: str, method: str, path_q: str, body: bytes) -> str:
    """
    Create DSX-HMAC header over: METHOD|PATH?Q|ts|nonce|<body>
    """
    ts = str(int(time.time()))
    nonce = _b64(os.urandom(12))
    msg = f"{method.upper()}|{path_q}|{ts}|{nonce}|".encode() + (body or b"")
    sig = _b64(hmac.new(secret.encode(), msg, hashlib.sha256).digest())
    return f"DSX-HMAC key_id={key_id}, ts={ts}, nonce={nonce}, sig={sig}"

async def verify_hmac_request(request: Request, secret_lookup) -> str:
    """
    Verify DSX-HMAC header using provided secret_lookup(key_id)->secret.
    Returns the key_id on success; raises 401 on failure.
    """
    auth = request.headers.get(_HEADER)
    if not auth or not auth.startswith("DSX-HMAC "):
        raise HTTPException(status_code=401, detail="Missing HMAC header")

    try:
        parts = dict(kv.strip().split("=", 1) for kv in auth[len("DSX-HMAC "):].split(","))
        key_id, ts, nonce, sig = parts["key_id"], parts["ts"], parts["nonce"], parts["sig"]
        ts_i = int(ts)
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed HMAC header")

    if abs(int(time.time()) - ts_i) > int(_settings.hmac_max_skew):
        raise HTTPException(status_code=401, detail="Stale request")

    secret = secret_lookup(key_id)
    if not secret:
        raise HTTPException(status_code=401, detail="Unknown key_id")

    body = await request.body()
    path_q = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    msg = f"{request.method.upper()}|{path_q}|{ts}|{nonce}|".encode() + (body or b"")
    expected = _b64(hmac.new(secret.encode(), msg, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="Bad signature")
    return key_id
