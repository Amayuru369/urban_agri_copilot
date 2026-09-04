"""FastAPI router for the Plant Monitoring & Alert System.

Endpoints:
- POST /garden/plants — Register a new tracked plant.
- GET  /garden/dashboard — Evaluate state and return all plants with alerts.
- PATCH /garden/alerts/{alert_id}/resolve — Mark an alert as resolved.
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models.garden import PlantAlert, TrackedPlant
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
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/plants", status_code=201)
async def create_plant(payload: PlantCreate, db: Session = Depends(get_db)):
    """Register a new tracked plant for monitoring."""
    plant = TrackedPlant(
        crop_name=payload.crop_name,
        planted_date=payload.planted_date,
        pot_size_liters=payload.pot_size_liters,
        latitude=payload.latitude,
        longitude=payload.longitude,
        location_name=payload.location_name or "Home Garden",
        telegram_chat_id=payload.telegram_chat_id,
        active=True,
    )
    db.add(plant)
    db.commit()
    db.refresh(plant)
    
    # STOP HERE. Do not add the try/except weather check.
    # The frontend will trigger the evaluation automatically.
    
    return {
        "id": plant.id,
        "crop_name": plant.crop_name,
        "planted_date": plant.planted_date.isoformat(),
        "message": "Plant registered successfully.",
    }


@router.get("/dashboard")
async def get_dashboard(refresh: bool = False, db: Session = Depends(get_db)):
    """Return dashboard state. Only evaluates live weather when refresh=True."""
    if refresh:
        from backend.main import _run_garden_monitor
        await _run_garden_monitor(trigger_type="MANUAL")

    today = date.today()
    plants = db.query(TrackedPlant).filter(TrackedPlant.active == True).all()  # noqa: E712

    # Import here to avoid circular import at module level
    from backend.app.services.garden_monitor import MILESTONE_SCHEDULE, _DEFAULT_MILESTONES

    dashboard = []
    for plant in plants:
        days_active = (today - plant.planted_date).days
        if days_active < 0:
            days_active = 0

        # Calculate progress percentage based on expected harvest timeline
        crop_lower = plant.crop_name.lower()
        schedule = _DEFAULT_MILESTONES
        for standard_crop, sched in MILESTONE_SCHEDULE.items():
            if standard_crop.lower() in crop_lower:
                schedule = sched
                break
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
            "latitude": plant.latitude,
            "longitude": plant.longitude,
            "location_name": getattr(plant, "location_name", "Home Garden"),
            "telegram_chat_id": plant.telegram_chat_id,
            "active": plant.active,
            "days_active": days_active,
            "progress_pct": progress_pct,
            "unresolved_alerts": unresolved_alerts,
        })

    return {
        "evaluated_on": today.isoformat(),
        "active_plants": len(dashboard),
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
def archive_plant(plant_id: int, db: Session = Depends(get_db)):
    """Archive/remove a tracked plant from active monitoring."""
    plant = db.query(TrackedPlant).filter(TrackedPlant.id == plant_id).first()
    if not plant:
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
def get_saved_locations(db: Session = Depends(get_db)):
    """Fetch unique active garden plots to populate the UI dropdown."""
    plants = (
        db.query(TrackedPlant.location_name, TrackedPlant.latitude, TrackedPlant.longitude)
        .filter(TrackedPlant.active == True)
        .all()
    )

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