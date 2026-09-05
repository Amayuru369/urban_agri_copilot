"""Authentication core: password hashing, HS256 JWT encode/decode, and
FastAPI dependencies for resolving the current user.

Design notes:
- Uses `bcrypt` (already in the environment) for password hashing.
- Implements a compact HS256 JWT signer with `hmac` + `hashlib` because
  PyJWT is not a project dependency. The token format is standard
  `base64url(header).base64url(payload).base64url(signature)` so it can be
  inspected by any JWT tool.
- `get_current_user` accepts an `Authorization: Bearer <token>` header and
  returns the matching User, or None when no/invalid token is supplied.
  This lets routers opt into auth while still supporting legacy callers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models.garden import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _resolve_secret() -> str:
    """Return the JWT signing secret from env or a stable development fallback."""
    secret = (
        os.getenv("JWT_SECRET")
        or getattr(settings, "JWT_SECRET", None)
        or getattr(settings, "SECRET_KEY", None)
    )
    if not secret:
        # Development fallback — override via JWT_SECRET env var in production.
        secret = "urbanagri-dev-secret-change-me"
    return str(secret)


JWT_SECRET: str = _resolve_secret()
JWT_ALGORITHM: str = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))  # 12 hours


# ---------------------------------------------------------------------------
# Password Hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash for the given plaintext password."""
    if plain is None:
        raise ValueError("Password must not be None")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    """Constant-time bcrypt comparison. Returns False for missing hashes."""
    if not hashed or not plain:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as exc:
        logger.warning("[Auth] bcrypt verification failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# JWT helpers (HS256, self-contained)
# ---------------------------------------------------------------------------

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def create_access_token(subject: str | int, extra_claims: Optional[dict[str, Any]] = None,
                       expires_minutes: Optional[int] = None) -> str:
    """Build and sign an HS256 JWT for the given subject."""
    now = datetime.now(timezone.utc)
    ttl = expires_minutes if expires_minutes is not None else ACCESS_TOKEN_EXPIRE_MINUTES
    expires_at = now + timedelta(minutes=ttl)

    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    signature = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """Verify signature + expiry and return the payload, or None if invalid."""
    if not token or token.count(".") != 2:
        return None

    header_b64, payload_b64, signature_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")

    expected_sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        provided_sig = _b64url_decode(signature_b64)
    except Exception:
        return None

    if not hmac.compare_digest(expected_sig, provided_sig):
        logger.debug("[Auth] JWT signature mismatch.")
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None

    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp < time.time():
        logger.debug("[Auth] JWT expired at %s (now=%s).", exp, int(time.time()))
        return None

    return payload


# ---------------------------------------------------------------------------
# FastAPI Dependencies
# ---------------------------------------------------------------------------

# auto_error=False so unauthenticated callers get None instead of a 401 —
# routers can then decide whether to fall back to legacy X-User-Id behaviour.
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Resolve the authenticated user from the Authorization header.

    Returns None when no token is present or the token is invalid/expired,
    which lets endpoints implement backward-compatible fallbacks.
    """
    if credentials is None or not credentials.credentials:
        return None

    payload = decode_access_token(credentials.credentials)
    if not payload:
        return None

    subject = payload.get("sub")
    if subject is None:
        return None

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        return None

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        logger.debug("[Auth] Token referenced unknown user id=%s", user_id)
        return None

    return user


def require_current_user(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """Strict variant that raises 401 when no valid token is supplied."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_admin_user(
    user: User = Depends(require_current_user),
) -> User:
    """Guard that only allows admin users through."""
    if not getattr(user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user
