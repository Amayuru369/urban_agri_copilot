"""FastAPI router for the Plant Monitoring & Alert System.

Endpoints:
- POST /garden/plants — Register a new tracked plant.
- GET  /garden/dashboard — Evaluate state and return all plants with alerts.
- PATCH /garden/alerts/{alert_id}/resolve — Mark an alert as resolved.

All plant-facing endpoints accept an optional `X-User-Id` header to scope
results to a specific judge profile. When the header is missing or invalid,
the endpoints fall back to returning all records (backward compatible).
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.core.auth import get_current_user
from backend.app.models.garden import PlantAlert, TrackedPlant, User
from backend.app.services.garden_monitor import evaluate_garden_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/garden", tags=["garden"])


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class PlantCreate(BaseModel):
    """Request body for registering a new tracked plant."""

    crop_name: str = Field(..., examples=["Tomato"])
    planted_date: date = Field(..., description="Date the plant was sown or transplanted")
    pot_size_liters: float = Field(default=5.0, ge=0.5, le=200.0)
    quantity: int = Field(default=1, ge=1, le=1000, description="Number of pots this entry represents")
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    location_name: str | None = Field(default="Home Garden", description="District or garden nickname")
    telegram_chat_id: str | None = Field(default=None, description="Optional Telegram chat ID for out-of-band alerts")


class PlantResponse(BaseModel):
    """Response schema for a tracked plant with computed fields."""

    id: int
    crop_name: str
    planted_date: date
    pot_size_liters: float
    quantity: int
    latitude: float
    longitude: float
    location_name: str | None
    telegram_chat_id: str | None
    active: bool
    days_active: int
    progress_pct: float
    unresolved_alerts: list[dict]


class AlertResolveResponse(BaseModel):
    """Response after resolving an alert."""

    id: int
    resolved: bool
    message: str


# ---------------------------------------------------------------------------
# Auth-aware scoping helper
# ---------------------------------------------------------------------------


def _resolve_scope_user_id(
    current_user: Optional[User],
    x_user_id: Optional[int],
) -> Optional[int]:
    """Resolve which user_id (if any) the current request should be scoped to.

    Precedence:
    1. Authenticated non-admin → locked to their own user.id (ignores header).
    2. Authenticated admin    → honours X-User-Id header (None = see everything).
    3. No JWT                 → legacy fallback: honour X-User-Id header.
    """
    if current_user is not None:
        if not getattr(current_user, "is_admin", False):
            return current_user.id
        # Admin — allow header-based filtering (None means global overview)
        return x_user_id if x_user_id is not None else None

    # Legacy path — no JWT supplied
    return x_user_id


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/plants", status_code=201)
async def create_plant(
    payload: PlantCreate,
    db: Session = Depends(get_db),
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Register a new tracked plant for monitoring.

    Owner resolution:
    - Authenticated non-admin growers always own their own plants.
    - Authenticated admins can plant on behalf of a profile via X-User-Id.
    - Legacy callers (no JWT) can still use X-User-Id, or leave the plant
      unscoped for backward compatibility.
    """
    owner_id = _resolve_scope_user_id(current_user, x_user_id)

    owner_chat_id: Optional[str] = None
    if owner_id is not None:
        owner = db.query(User).filter(User.id == owner_id).first()
        if owner:
            owner_chat_id = owner.telegram_chat_id

    # Fall back to the profile's chat ID when the payload didn't provide one
    effective_chat_id = payload.telegram_chat_id or owner_chat_id

    plant = TrackedPlant(
        crop_name=payload.crop_name,
        planted_date=payload.planted_date,
        pot_size_liters=payload.pot_size_liters,
        quantity=payload.quantity,
        latitude=payload.latitude,
        longitude=payload.longitude,
        location_name=(payload.location_name.strip() if payload.location_name and payload.location_name.strip() else "Home Garden"),
        telegram_chat_id=effective_chat_id,
        active=True,
        user_id=owner_id,
    )
    db.add(plant)
    db.commit()
    db.refresh(plant)

    # Immediately evaluate weather risks & milestones for the newly registered plant
    try:
        await evaluate_garden_state(db, trigger_type="MANUAL")
    except Exception as eval_err:
        logger.warning(f"Immediate evaluation failed on plant creation: {eval_err}")

    return {
        "id": plant.id,
        "crop_name": plant.crop_name,
        "planted_date": plant.planted_date.isoformat(),
        "user_id": plant.user_id,
        "quantity": plant.quantity,
        "message": "Plant registered successfully.",
    }


@router.get("/dashboard")
async def get_dashboard(
    refresh: bool = False,
    db: Session = Depends(get_db),
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Return dashboard state, scoped by JWT role with X-User-Id fallback.

    - Non-admin growers only see their own plants.
    - Admins see everything (or a specific profile when X-User-Id is sent).
    - Unauthenticated legacy callers fall back to the X-User-Id header.

    Only evaluates live weather when refresh=True.
    """
    if refresh:
        # Import lazily to avoid circular import issues
        from backend.main import _run_garden_monitor
        await _run_garden_monitor(trigger_type="MANUAL")
        db.expire_all()

         # Send the requesting user a Telegram summary of their current alert count
        scope_user_id = _resolve_scope_user_id(current_user, x_user_id)
        if scope_user_id is not None:
            from backend.app.services.garden_monitor import send_telegram_alert

            alert_count = (
                db.query(PlantAlert)
                .join(TrackedPlant, PlantAlert.plant_id == TrackedPlant.id)
                .filter(
                    TrackedPlant.user_id == scope_user_id,
                    TrackedPlant.active == True,  # noqa: E712
                    PlantAlert.resolved == False,  # noqa: E712
                )
                .count()
            )

            target_chat = (
                current_user.telegram_chat_id
                if current_user and getattr(current_user, "telegram_chat_id", None)
                else None
            )
            if target_chat:
                summary_text = (
                    f"🔄 <b>Garden Refreshed</b>\n\n"
                    f"You have <b>{alert_count}</b> active alert(s) in your garden."
                )
                try:
                    await send_telegram_alert(target_chat, summary_text)
                except Exception as tg_err:
                    logger.warning(f"Failed to send refresh summary to Telegram: {tg_err}")


    scope_user_id = _resolve_scope_user_id(current_user, x_user_id)

    today = date.today()
    query = db.query(TrackedPlant).filter(TrackedPlant.active == True)  # noqa: E712

    if scope_user_id is not None:
        query = query.filter(TrackedPlant.user_id == scope_user_id)

    plants = query.all()

    # Import here to avoid circular import at module level
    from backend.app.services.garden_monitor import MILESTONE_SCHEDULE, _DEFAULT_MILESTONES

    dashboard = []
    for plant in plants:
        days_active = (today - plant.planted_date).days
        if days_active < 0:
            days_active = 0

        # Calculate progress percentage based on expected harvest timeline
        schedule = MILESTONE_SCHEDULE.get(plant.crop_name, _DEFAULT_MILESTONES)
        max_day = schedule[-1]["day"] if schedule else 90
        progress_pct = min(100.0, round((days_active / max_day) * 100, 1))

        # Fetch unresolved alerts for this plant
        alerts = (
            db.query(PlantAlert)
            .filter(
                PlantAlert.plant_id == plant.id,
                PlantAlert.resolved == False,  # noqa: E712
            )
            .order_by(PlantAlert.triggered_on.desc())
            .all()
        )

        unresolved_alerts = [
            {
                "id": a.id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "action_required": a.action_required,
                "triggered_on": a.triggered_on.isoformat(),
            }
            for a in alerts
        ]

        dashboard.append({
            "id": plant.id,
            "crop_name": plant.crop_name,
            "planted_date": plant.planted_date.isoformat(),
            "pot_size_liters": plant.pot_size_liters,
            "quantity": getattr(plant, "quantity", 1) or 1,
            "latitude": plant.latitude,
            "longitude": plant.longitude,
            "location_name": getattr(plant, "location_name", "Home Garden"),
            "telegram_chat_id": plant.telegram_chat_id,
            "user_id": getattr(plant, "user_id", None),
            "active": plant.active,
            "days_active": days_active,
            "progress_pct": progress_pct,
            "unresolved_alerts": unresolved_alerts,
        })

    return {
        "evaluated_on": today.isoformat(),
        "active_plants": len(dashboard),
        "total_pot_count": sum(p["quantity"] for p in dashboard),
        "scoped_user_id": scope_user_id,
        "plants": dashboard,
    }


@router.patch("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    """Mark a specific alert as resolved."""
    alert = db.query(PlantAlert).filter(PlantAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")

    if alert.resolved:
        return {"id": alert.id, "resolved": True, "message": "Alert was already resolved."}

    alert.resolved = True
    db.commit()
    db.refresh(alert)

    return {"id": alert.id, "resolved": True, "message": "Alert resolved successfully."}

@router.get("/plants/{plant_id}/alerts/history")
def get_plant_alert_history(plant_id: int, db: Session = Depends(get_db)):
    """Fetch all alerts (both resolved and active) for a specific plant."""
    alerts = (
        db.query(PlantAlert)
        .filter(PlantAlert.plant_id == plant_id)
        .order_by(PlantAlert.id.desc())
        .all()
    )
    return {
        "alerts": [
            {
                "id": a.id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "message": a.message,
                "action_required": a.action_required,
                "resolved": a.resolved,
                "triggered_on": a.triggered_on.isoformat(),
            }
            for a in alerts
        ]
    }


@router.patch("/alerts/{alert_id}/reopen")
def reopen_alert(alert_id: int, db: Session = Depends(get_db)):
    """Restore an alert if it was marked as Done by accident."""
    alert = db.query(PlantAlert).filter(PlantAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")

    alert.resolved = False
    db.commit()
    db.refresh(alert)

    return {"id": alert.id, "resolved": False, "message": "Alert reopened successfully."}

@router.delete("/plants/{plant_id}")
def archive_plant(
    plant_id: int,
    db: Session = Depends(get_db),
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Archive/remove a tracked plant from active monitoring.

    Access rules:
    - Non-admin growers can only archive plants they own.
    - Admins can archive any plant (or scope via X-User-Id).
    - Legacy unauthenticated callers keep working with the X-User-Id header.
    """
    plant = db.query(TrackedPlant).filter(TrackedPlant.id == plant_id).first()
    if not plant:
        raise HTTPException(status_code=404, detail=f"Plant {plant_id} not found.")

    # Strict isolation for authenticated non-admin growers: they may ONLY
    # archive plants they own. The X-User-Id header is ignored entirely and
    # even legacy unowned plants (user_id is None) are off-limits, so a grower
    # can never modify another profile's data. Existence is hidden via 404.
    if current_user is not None and not getattr(current_user, "is_admin", False):
        if plant.user_id != current_user.id:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id} not found.")
    else:
        # Admin or legacy unauthenticated caller — honour X-User-Id scoping.
        scope_user_id = _resolve_scope_user_id(current_user, x_user_id)
        if scope_user_id is not None and plant.user_id is not None and plant.user_id != scope_user_id:
            raise HTTPException(status_code=404, detail=f"Plant {plant_id} not found.")

    plant.active = False
    db.commit()
    return {"id": plant.id, "active": False, "message": "Plant archived successfully."}


@router.get("/audit-logs")
def get_audit_logs(db: Session = Depends(get_db)):
    """Return the last 10 system audit log entries (most recent first)."""
    from backend.app.models.garden import SystemAuditLog

    logs = (
        db.query(SystemAuditLog)
        .order_by(SystemAuditLog.timestamp.desc())
        .limit(10)
        .all()
    )
    return {
        "audit_logs": [
            {
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "trigger_type": log.trigger_type,
                "action": log.action,
                "details": log.details,
                "status": log.status,
            }
            for log in logs
        ]
    }

@router.get("/locations")
def get_saved_locations(
    db: Session = Depends(get_db),
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    current_user: Optional[User] = Depends(get_current_user),
):
    """Fetch unique active garden plots to populate the UI dropdown.

    Scoped to the JWT-authenticated grower's own plants, or to X-User-Id for
    admins / legacy callers.
    """
    scope_user_id = _resolve_scope_user_id(current_user, x_user_id)

    query = (
        db.query(TrackedPlant.location_name, TrackedPlant.latitude, TrackedPlant.longitude)
        .filter(TrackedPlant.active == True)  # noqa: E712
    )
    if scope_user_id is not None:
        query = query.filter(TrackedPlant.user_id == scope_user_id)

    plants = query.all()

    seen = set()
    locations = []
    for loc_name, lat, lon in plants:
        name = (loc_name or "Home Garden").strip()
        safe_lat = round(float(lat if lat is not None else 6.9271), 4)
        safe_lon = round(float(lon if lon is not None else 79.8612), 4)

        key = (name.lower(), round(safe_lat, 2), round(safe_lon, 2))
        if key not in seen:
            seen.add(key)
            locations.append({
                "location_name": name,
                "latitude": safe_lat,
                "longitude": safe_lon,
            })

    return locations
