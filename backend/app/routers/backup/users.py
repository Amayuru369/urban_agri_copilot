"""FastAPI router for Judge/User profile management.

Endpoints:
- GET    /users            — List existing profiles.
- POST   /users            — Create a new profile.
- PATCH  /users/{user_id}  — Update a profile (self or admin).
- DELETE /users/{user_id}  — Remove a profile and its plants (admin only).

Also exposes `get_current_user` dependency for other routers to scope queries.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models.garden import Location, TrackedPlant, User

# JWT-based auth dependency (aliased to avoid clashing with the legacy
# header-based `get_current_user` defined below for backward compatibility).
from backend.app.core.auth import get_current_user as get_jwt_user, hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    """Request body for creating a new profile."""

    name: str = Field(..., min_length=1, max_length=100, examples=["Judge A"])
    telegram_chat_id: Optional[str] = Field(
        default=None, max_length=64, description="Optional Telegram chat ID for this profile"
    )
    password: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional login password. When provided it is bcrypt-hashed so the "
                    "grower can sign in. Admin-created profiles pass a default password.",
    )


class UserUpdate(BaseModel):
    """Request body for PATCH /users/{user_id}. All fields are optional;
    only the ones supplied are changed. A blank/None password leaves the
    existing credential untouched.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    telegram_chat_id: Optional[str] = Field(default=None, max_length=64)
    password: Optional[str] = Field(default=None, min_length=6, max_length=200)


class UserResponse(BaseModel):
    """Response schema for a user profile."""

    id: int
    name: str
    telegram_chat_id: Optional[str] = None
    created_at: Optional[datetime] = None
    is_admin: bool = False


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def get_current_user(
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Resolve the active profile from the X-User-Id header.

    Returns None when the header is missing or the ID does not match any user,
    which lets callers fall back to unscoped (backward-compatible) behaviour.
    """
    if x_user_id is None:
        return None

    user = db.query(User).filter(User.id == x_user_id).first()
    if not user:
        # Log but do not raise — keep endpoints backward compatible with
        # stale localStorage values.
        logger.debug("[Users] X-User-Id=%s did not match any profile; ignoring.", x_user_id)
        return None

    return user


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db)):
    """Return all existing judge profiles ordered by creation date."""
    users = db.query(User).order_by(User.created_at.asc(), User.id.asc()).all()
    return [
        UserResponse(
            id=u.id,
            name=u.name,
            telegram_chat_id=u.telegram_chat_id,
            created_at=u.created_at,
            is_admin=bool(u.is_admin),
        )
        for u in users
    ]


@router.post("", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    """Create a new User profile.

    Admins use this to spin up grower profiles on the fly. When a `password`
    is supplied it is bcrypt-hashed so the new grower can sign in; profiles
    created without one remain login-less (backward compatible). New profiles
    are always non-admin.
    """
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Profile name must not be empty.")

    hashed_password = None
    if payload.password:
        from backend.app.core.auth import hash_password
        hashed_password = hash_password(payload.password)

    user = User(
        name=name,
        telegram_chat_id=(payload.telegram_chat_id or "").strip() or None,
        hashed_password=hashed_password,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("[Users] Created profile id=%s name=%s", user.id, user.name)

    return UserResponse(
        id=user.id,
        name=user.name,
        telegram_chat_id=user.telegram_chat_id,
        created_at=user.created_at,
        is_admin=bool(user.is_admin),
    )


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_jwt_user),
):
    """Update a profile's name, Telegram chat ID, and/or password.

    Authorization: allowed when the caller is an admin OR is editing their own
    profile. Any other authenticated caller (or an unauthenticated one) gets
    403. A supplied `password` is bcrypt-hashed before saving; leaving it blank
    keeps the existing credential.
    """
    if current_user is None:
        raise HTTPException(status_code=403, detail="Authentication required to edit a profile.")

    is_admin = bool(getattr(current_user, "is_admin", False))
    if not (is_admin or current_user.id == user_id):
        raise HTTPException(status_code=403, detail="You are not allowed to edit this profile.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

    # Only touch fields explicitly provided in the request body.
    fields_set = payload.model_fields_set

    if "name" in fields_set and payload.name is not None:
        new_name = payload.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Profile name must not be empty.")
        user.name = new_name

    if "telegram_chat_id" in fields_set:
        # Allow clearing the chat ID by sending an empty string / null.
        user.telegram_chat_id = (payload.telegram_chat_id or "").strip() or None

    if "password" in fields_set and payload.password:
        user.hashed_password = hash_password(payload.password)

    db.commit()
    db.refresh(user)

    logger.info("[Users] Updated profile id=%s by actor id=%s (admin=%s)", user.id, current_user.id, is_admin)

    return UserResponse(
        id=user.id,
        name=user.name,
        telegram_chat_id=user.telegram_chat_id,
        created_at=user.created_at,
        is_admin=bool(user.is_admin),
    )


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_jwt_user),
):
    """Delete a profile along with its plants (and their alerts) and locations.

    Authorization: admins only. Any other caller receives 403.
    """
    if current_user is None or not bool(getattr(current_user, "is_admin", False)):
        raise HTTPException(status_code=403, detail="Administrator privileges required to delete a profile.")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

    # Remove associated plants first — PlantAlert cascades via delete-orphan on
    # the TrackedPlant.alerts relationship, so alerts go too.
    plants = db.query(TrackedPlant).filter(TrackedPlant.user_id == user_id).all()
    plant_count = len(plants)
    for plant in plants:
        db.delete(plant)

    # Remove any saved locations owned by this profile.
    locations = db.query(Location).filter(Location.user_id == user_id).all()
    location_count = len(locations)
    for loc in locations:
        db.delete(loc)

    db.delete(user)
    db.commit()

    logger.info(
        "[Users] Deleted profile id=%s (plants=%s, locations=%s) by admin id=%s",
        user_id, plant_count, location_count, current_user.id,
    )

    return {
        "id": user_id,
        "deleted": True,
        "plants_removed": plant_count,
        "locations_removed": location_count,
        "message": f"Profile '{user.name}' and its associated data were deleted.",
    }
