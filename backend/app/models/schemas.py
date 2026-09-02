from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.sql import func

from backend.app.core.database import Base


class Crop(Base):
    __tablename__ = "crops"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    scientific_name = Column(String, nullable=True)
    category = Column(String, nullable=False)
    min_temp_c = Column(Float, nullable=True)
    max_temp_c = Column(Float, nullable=True)
    sunlight_hours_min = Column(Float, nullable=True)
    days_to_harvest = Column(Integer, nullable=True)
    typical_yield_per_pot_g = Column(Float, nullable=True)
    companion_crops = Column(String, nullable=True)
    spacing_cm = Column(Integer, nullable=True)
    watering_frequency_days = Column(Integer, nullable=True)


class MarketPrice(Base):
    __tablename__ = "market_prices"

    id = Column(Integer, primary_key=True, index=True)
    crop_name = Column(String, nullable=False)
    retail_price_per_kg = Column(Float, nullable=False)
    wholesale_price_per_kg = Column(Float, nullable=False)
    currency = Column(String, nullable=False, default="LKR")
    last_updated = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserGarden(Base):
    __tablename__ = "user_gardens"

    id = Column(Integer, primary_key=True, index=True)
    garden_name = Column(String, nullable=False)
    location_lat = Column(Float, nullable=False)
    location_lon = Column(Float, nullable=False)
    container_type = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------

class CropBase(BaseModel):
    name: str
    scientific_name: str | None = None
    category: str
    min_temp_c: float | None = None
    max_temp_c: float | None = None
    sunlight_hours_min: float | None = None
    days_to_harvest: int | None = None
    typical_yield_per_pot_g: float | None = None
    companion_crops: str | None = None
    spacing_cm: int | None = None
    watering_frequency_days: int | None = None


class CropOut(CropBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class MarketPriceBase(BaseModel):
    crop_name: str
    retail_price_per_kg: float
    wholesale_price_per_kg: float
    currency: str


class MarketPriceOut(MarketPriceBase):
    id: int
    last_updated: datetime | None = None
    margin_per_kg: float | None = None
    margin_percent: float | None = None

    model_config = ConfigDict(from_attributes=True)


class UserGardenBase(BaseModel):
    garden_name: str
    location_lat: float
    location_lon: float
    container_type: str


class UserGardenOut(UserGardenBase):
    id: int
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class WeatherCurrent(BaseModel):
    temperature_c: float | None = None
    relative_humidity_percent: float | None = None
    precipitation_probability_percent: float | None = None
    time: str | None = None


class ForecastDay(BaseModel):
    date: str
    min_temp_c: float | None = None
    max_temp_c: float | None = None
    daylight_duration_seconds: float | None = None


class MicroClimate(BaseModel):
    latitude: float
    longitude: float
    current: WeatherCurrent
    forecast: list[ForecastDay]


# ---------------------------------------------------------------------------
# Phase 2: planner, diagnosis, and remedy schemas
# ---------------------------------------------------------------------------

class CropPlanRequest(BaseModel):
    lat: float
    lon: float
    container_type: str
    space_sqm: float
    target_month: int


class CropTimelineRequest(BaseModel):
    crop_name: str
    harvest_days: int | None = None
    target_month: int
    lat: float | None = None
    lon: float | None = None


class CropRecommendation(BaseModel):
    crop: CropOut
    suitability_score: int
    recommended_pot_count: int
    layout: str
    companion_synergy: list[str]
    notes: str


class CropPlan(BaseModel):
    location: dict
    container_type: str
    space_sqm: float
    target_month: int
    is_current_month: bool = True
    forecast_summary: dict
    weekly_forecast: list[dict] | None = None
    growing_season_outlook: list[dict] | None = None
    crop_timeline: dict | None = None
    recommendations: list[CropRecommendation]


class Diagnosis(BaseModel):
    crop_detected: str | None
    crop_confidence: float = 0.85
    issue_type: str
    disease_confidence: float = 0.80
    confidence: float = 0.80
    symptoms: list[str]
    severity: str
    recommended_action: str
    crop_match_status: str = "auto_detected"
    crop_match_message: str | None = None


class DiagnosisResponse(BaseModel):
    diagnosis: Diagnosis
    linked_remedy: dict


class RemedyRequest(BaseModel):
    issue_type: str
    available_scraps: list[str] | None = None


class RemedyRecipe(BaseModel):
    issue_type: str
    remedy_name: str
    ingredients: list[str]
    preparation_steps: list[str]
    application_schedule: str
    safety_notes: list[str]
    matched_scraps: list[str] | None = None


class ReportGenerateRequest(BaseModel):
    plan_data: dict
    diagnosis_data: dict | None = None
    savings_data: dict
    output_filename: str | None = "garden_report.pdf"
