from __future__ import annotations
import base64, os, uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List

import jwt
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse
from authlib.oauth2 import AuthorizationServer
from authlib.oauth2.rfc6749 import grants
from config import AuthConfig

settings = AuthConfig()
router = APIRouter()

# In-memory stores (replace with DB tables)
CLIENTS: Dict[str, Dict[str, Any]] = {}   # {client_id: {client_secret, scope, name}}
TOKENS:  List[Dict[str, Any]] = []        # [{access_token, client_id, scope, exp}]

def _mint_jwt(sub: str, scope: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=settings.jwt_ttl)
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "scope": scope,
        "kid": "oauth2",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

class ClientCredentials(grants.ClientCredentialsGrant):
    TOKEN_ENDPOINT_AUTH_METHODS = ["client_secret_post", "client_secret_basic"]

    def authenticate_client(self):
        method = self.get_token_endpoint_auth_method()
        if method == "client_secret_basic":
            client_id, client_secret = self.parse_basic_auth()
        else:
            client_id = self.request.form.get("client_id")
            client_secret = self.request.form.get("client_secret")
        data = CLIENTS.get(client_id)
        if not data or data["client_secret"] != client_secret:
            self.raise_error("invalid_client")
        self.request.client = type("Client", (), {
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": data.get("scope", ""),
        })
        return self.request.client

    # Issue a JWT access token
    def create_access_token(self, token, client, request):
        scope = token.get("scope", client.scope or "")
        token["access_token"] = _mint_jwt(client.client_id, scope)
        token["token_type"] = "Bearer"
        return token

authz = AuthorizationServer()
authz.register_grant(ClientCredentials)

@router.post("/oauth/token")
async def token_endpoint(request: Request):
    # Authlib can read the Starlette/FastAPI request directly—no OAuth2Request import needed
    resp = authz.create_token_response(request)
    # Persist for debugging/observability if you want (optional):
    if resp.status_code == 200 and isinstance(resp.json, dict) and "access_token" in resp.json:
        TOKENS.append({
            "access_token": resp.json["access_token"],
            "client_id": getattr(getattr(request, "oauth2_request", None), "client_id", None),
            "scope": resp.json.get("scope", ""),
        })
    return JSONResponse(status_code=resp.status_code, content=resp.json)

@router.post("/oauth/dev-register")
async def dev_register(connector_name: str = "connector", scope: str = ""):
    if settings.app_env != "dev":
        raise HTTPException(403, "Disabled outside dev")
    client_id = str(uuid.uuid4())
    client_secret = base64.b64encode(os.urandom(32)).decode()
    CLIENTS[client_id] = {"client_secret": client_secret, "scope": scope, "name": connector_name}
    return {"client_id": client_id, "client_secret": client_secret}

@router.post("/oauth/dev-revoke")
async def dev_revoke(client_id: str):
    if settings.app_env != "dev":
        raise HTTPException(403, "Disabled outside dev")
    if client_id not in CLIENTS:
        raise HTTPException(404, "Client not found")
    del CLIENTS[client_id]
    return {"message": "Client revoked"}

