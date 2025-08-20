import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import AuthConfig

settings = AuthConfig()
_bearer = HTTPBearer(auto_error=False)

def require_jwt(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> str:
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(401, "Missing bearer token")
    try:
        payload = jwt.decode(
            creds.credentials,
            settings.jwt_secret,
            audience=settings.jwt_audience,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
        )
    except Exception as e:
        raise HTTPException(401, f"Invalid token: {e}")
    return payload["sub"]  # connector_id/client_id
