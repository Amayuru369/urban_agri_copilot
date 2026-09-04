"""Authentication router — login and current-profile endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.auth import (
    create_access_token,
    hash_password,
    require_current_user,
    verify_password,
)
from backend.app.core.database import get_db
from backend.app.models.garden import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Credentials payload for POST /login."""

    username: str = Field(..., min_length=1, max_length=100, description="Judge profile name")
    password: str = Field(..., min_length=1, max_length=200)


class LoginResponse(BaseModel):
    """Response body for a successful login."""

    access_token: str
    token_type: str = "bearer"
    user_id: int
    name: str
    is_admin: bool


class MeResponse(BaseModel):
    """Response body for GET /users/me."""

    id: int
    name: str
    telegram_chat_id: Optional[str] = None
    is_admin: bool


class RegisterRequest(BaseModel):
    """Credentials payload for POST /register (self-serve grower signup)."""

    username: str = Field(..., min_length=1, max_length=100, description="Desired profile name")
    password: str = Field(..., min_length=6, max_length=200, description="Password (min 6 characters)")
    telegram_chat_id: Optional[str] = Field(
        default=None, max_length=64, description="Optional Telegram chat ID for alert notifications"
    )


class RegisterResponse(BaseModel):
    """Response body for a successful registration."""

    message: str
    user_id: int
    name: str
    is_admin: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate a judge/grower by name + password and return a JWT."""
    username = (payload.username or "").strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must not be empty.",
        )

    # Case-insensitive match on the profile name
    user = (
        db.query(User)
        .filter(User.name.ilike(username))
        .order_by(User.id.asc())
        .first()
    )

    if not user:
        logger.info("[Auth] Login attempt for unknown user '%s'", username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(payload.password, user.hashed_password):
        logger.info("[Auth] Bad password for user id=%s name=%s", user.id, user.name)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        subject=user.id,
        extra_claims={
            "name": user.name,
            "is_admin": bool(user.is_admin),
        },
    )

    logger.info("[Auth] Login success for user id=%s name=%s", user.id, user.name)

    return LoginResponse(
        access_token=token,
        user_id=user.id,
        name=user.name,
        is_admin=bool(user.is_admin),
    )


@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    """Self-serve registration for secondary (non-admin) grower accounts.

    Hashes the password with bcrypt, rejects duplicate usernames, and always
    creates the profile with `is_admin=False` so new signups can never grant
    themselves admin privileges. Admins remain seeded separately at startup.
    """
    username = (payload.username or "").strip()
    if not username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must not be empty.",
        )

    # Reject duplicate usernames (case-insensitive)
    existing = (
        db.query(User)
        .filter(User.name.ilike(username))
        .order_by(User.id.asc())
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That username is already taken. Please choose another.",
        )

    user = User(
        name=username,
        telegram_chat_id=(payload.telegram_chat_id or "").strip() or None,
        hashed_password=hash_password(payload.password),
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("[Auth] Registered new grower id=%s name=%s", user.id, user.name)

    return RegisterResponse(
        message="Registration successful. You can now sign in.",
        user_id=user.id,
        name=user.name,
        is_admin=bool(user.is_admin),
    )


@router.get("/users/me", response_model=MeResponse)
def get_me(current_user: User = Depends(require_current_user)):
    """Return the authenticated profile — used by the frontend to decide UI role."""
    return MeResponse(
        id=current_user.id,
        name=current_user.name,
        telegram_chat_id=current_user.telegram_chat_id,
        is_admin=bool(current_user.is_admin),
    )
