"""
Supabase JWT verification.

Supabase projects sign access tokens one of two ways:
  * legacy: HS256 with the project's shared "JWT secret"  -> set SUPABASE_JWT_SECRET
  * current: asymmetric keys (ES256/RS256), published as a JWKS at
    <SUPABASE_URL>/auth/v1/.well-known/jwks.json           -> set SUPABASE_URL
We read the token's `alg` header and verify accordingly, so either works.

If neither variable is set, auth is OFF (local dev): tokens are ignored and
everyone is a guest.
"""
import os
from dataclasses import dataclass
from typing import Optional

import jwt  # PyJWT
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

_bearer = HTTPBearer(auto_error=False)
_jwks_clients = {}


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None


def auth_enabled() -> bool:
    return bool(os.getenv("SUPABASE_JWT_SECRET") or os.getenv("SUPABASE_URL"))


def _jwks_client(url: str) -> PyJWKClient:
    if url not in _jwks_clients:
        _jwks_clients[url] = PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", cache_keys=True)
    return _jwks_clients[url]


def verify_token(token: str) -> AuthUser:
    """Returns the user for a valid Supabase access token; raises jwt errors otherwise."""
    url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    secret = os.getenv("SUPABASE_JWT_SECRET")
    alg = jwt.get_unverified_header(token).get("alg")
    kwargs = {"audience": "authenticated"}
    if url:
        kwargs["issuer"] = f"{url}/auth/v1"
    if alg == "HS256":
        if not secret:
            raise jwt.InvalidTokenError("HS256 token but SUPABASE_JWT_SECRET is not set")
        payload = jwt.decode(token, secret, algorithms=["HS256"], **kwargs)
    elif alg in ("ES256", "RS256"):
        if not url:
            raise jwt.InvalidTokenError(f"{alg} token but SUPABASE_URL is not set")
        key = _jwks_client(url).get_signing_key_from_jwt(token).key
        payload = jwt.decode(token, key, algorithms=[alg], **kwargs)
    else:
        raise jwt.InvalidTokenError(f"Unsupported token algorithm {alg}")
    meta = payload.get("user_metadata") or {}
    return AuthUser(
        id=payload["sub"],
        email=payload.get("email"),
        name=meta.get("full_name") or meta.get("name"),
        avatar_url=meta.get("avatar_url") or meta.get("picture"),
    )


def optional_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Optional[AuthUser]:
    """Signed-in user, or None for guests. A bad token is a 401, never silently a guest."""
    if creds is None or not auth_enabled():
        return None
    try:
        return verify_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Your session expired, please sign in again")
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def require_user(user: Optional[AuthUser] = Depends(optional_user)) -> AuthUser:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to use this")
    return user
