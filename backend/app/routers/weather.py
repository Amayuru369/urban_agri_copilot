from fastapi import APIRouter, HTTPException, Query

from backend.app.models.schemas import MicroClimate
from backend.app.services import weather_service

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/current", response_model=MicroClimate)
async def current_weather(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    """Return current micro-climate and a 7-day forecast for a location."""
    return await weather_service.fetch_microclimate(lat, lon)
