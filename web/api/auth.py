"""
Firebase ID-token verification.

The frontend signs players in with Firebase Authentication (Google) and sends the
resulting ID token as `Authorization: Bearer <token>`. Firebase signs these with RS256;
the matching public certificates are published by Google, so no shared secret is needed.

Set FIREBASE_PROJECT_ID to turn auth on. If it is unset, auth is OFF (local dev):
tokens are ignored and everyone is a guest.
"""
import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

import jwt  # PyJWT
import requests
from cryptography.x509 import load_pem_x509_certificate
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)

# Google publishes the public certs for Firebase ID tokens here (kid -> PEM certificate).
DEFAULT_CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
CERT_REFRESH_MIN_SECONDS = 60      # never re-fetch more often than this, even for unknown key ids
CERT_DEFAULT_TTL = 3600


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None


def project_id() -> Optional[str]:
    return os.getenv("FIREBASE_PROJECT_ID") or None


def auth_enabled() -> bool:
    return project_id() is not None


class _CertCache:
    """Caches Google's signing certificates and refreshes them when they expire or a new kid appears."""

    def __init__(self):
        self._keys: Dict[str, object] = {}
        self._expires = 0.0
        self._fetched = 0.0
        self._lock = threading.Lock()

    def key_for(self, kid: str, url: str):
        with self._lock:
            now = time.time()
            stale = now >= self._expires
            unknown = kid not in self._keys and now - self._fetched >= CERT_REFRESH_MIN_SECONDS
            if stale or unknown:
                self._refresh(url, now)
            if kid not in self._keys:
                raise jwt.InvalidTokenError("Unknown signing key")
            return self._keys[kid]

    def _refresh(self, url: str, now: float) -> None:
        try:
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            certs = res.json()
        except (requests.RequestException, ValueError) as exc:
            if self._keys:       # keep serving with the last good keys through a transient outage
                self._fetched = now
                return
            raise jwt.InvalidTokenError(f"Could not fetch signing keys: {exc}") from exc
        self._keys = {kid: load_pem_x509_certificate(pem.encode()).public_key() for kid, pem in certs.items()}
        max_age = CERT_DEFAULT_TTL
        for part in res.headers.get("Cache-Control", "").split(","):
            part = part.strip()
            if part.startswith("max-age="):
                try:
                    max_age = int(part[len("max-age="):])
                except ValueError:
                    pass
        self._fetched = now
        self._expires = now + max_age


_certs = _CertCache()


def reset_cert_cache() -> None:
    """Test hook."""
    global _certs
    _certs = _CertCache()


def verify_token(token: str) -> AuthUser:
    """Returns the user for a valid Firebase ID token; raises a jwt error otherwise."""
    pid = project_id()
    if not pid:
        raise jwt.InvalidTokenError("FIREBASE_PROJECT_ID is not set")
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256" or not header.get("kid"):
        # Rejects `alg: none` and HS256 tokens forged with a public key as the "secret".
        raise jwt.InvalidTokenError("Unsupported token algorithm")
    key = _certs.key_for(header["kid"], os.getenv("FIREBASE_CERTS_URL", DEFAULT_CERTS_URL))
    payload = jwt.decode(token, key, algorithms=["RS256"], audience=pid,
                         issuer=f"https://securetoken.google.com/{pid}",
                         options={"require": ["exp", "iat", "sub"]})
    if not payload["sub"]:
        raise jwt.InvalidTokenError("Token has no subject")
    return AuthUser(id=payload["sub"], email=payload.get("email"),
                    name=payload.get("name"), avatar_url=payload.get("picture"))


def optional_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> Optional[AuthUser]:
    """Signed-in user, or None for guests. A bad token is a 401, never silently a guest."""
    if creds is None or not auth_enabled():
        return None
    try:
        return verify_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Your session expired, please sign in again")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def require_user(user: Optional[AuthUser] = Depends(optional_user)) -> AuthUser:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to use this")
    return user
