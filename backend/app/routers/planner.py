import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models.schemas import Crop, CropPlan, CropPlanRequest, CropTimelineRequest
from backend.app.services import planner_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planner", tags=["planner"])


@router.post("/recommend", response_model=CropPlan)
async def recommend_crop_plan(
    payload: CropPlanRequest,
    db: Session = Depends(get_db),
):
    """Generate a ranked crop plan for a location, container, and available space."""
    return await planner_service.generate_crop_plan(
        lat=payload.lat,
        lon=payload.lon,
        container_type=payload.container_type,
        space_sqm=payload.space_sqm,
        target_month=payload.target_month,
        db=db,
    )


@router.post("/timeline")
def get_crop_timeline(
    payload: CropTimelineRequest,
    db: Session = Depends(get_db),
):
    """Return a dynamic growth timeline for a specific crop and target month.

    If *harvest_days* is not supplied, the value is looked up from the crop
    database.  Custom / unknown crops require the caller to pass harvest_days
    explicitly.  Optional *lat*/*lon* enable a latitude-aware humidity
    fallback when live weather data is unavailable.
    """
    harvest_days = payload.harvest_days

    # If harvest_days not provided, try to look it up from the DB
    if harvest_days is None:
        crop = db.query(Crop).filter(Crop.name.ilike(payload.crop_name)).first()
        if crop and crop.days_to_harvest:
            harvest_days = crop.days_to_harvest
        else:
            raise HTTPException(
                status_code=400,
                detail=f"harvest_days is required for '{payload.crop_name}' — crop not found in library.",
            )

    timeline = planner_service.build_timeline_for_crop(
        crop_name=payload.crop_name,
        harvest_days=harvest_days,
        target_month=payload.target_month,
        lat=payload.lat,
    )
    if timeline is None:
        raise HTTPException(status_code=400, detail="Could not build timeline — check harvest_days > 0.")
    return timeline

