import logging
import math
from datetime import date
from statistics import mean

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from backend.app.models.schemas import Crop
from backend.app.services import weather_service

# ---------------------------------------------------------------------------
# Sri Lankan monthly climate profiles (shared across outlook + timeline)
# SW monsoon May-Sep, NE monsoon Nov-Feb, inter-monsoonal Mar-Apr / Oct
# ---------------------------------------------------------------------------
MONTH_PROFILES: dict[int, dict] = {
    1:  {"min_c": 22, "max_c": 31, "daylight_h": 11.8, "rh_pct": 70, "rainfall": "Dry / High Sunshine",              "condition": "Warm & Dry"},
    2:  {"min_c": 22, "max_c": 32, "daylight_h": 12.0, "rh_pct": 68, "rainfall": "Dry / High Sunshine",              "condition": "Warm & Dry"},
    3:  {"min_c": 23, "max_c": 33, "daylight_h": 12.1, "rh_pct": 74, "rainfall": "Intermittent Showers",             "condition": "Warm & Humid"},
    4:  {"min_c": 24, "max_c": 33, "daylight_h": 12.2, "rh_pct": 76, "rainfall": "Moderate Inter-monsoonal Showers", "condition": "Warm & Humid"},
    5:  {"min_c": 24, "max_c": 32, "daylight_h": 12.4, "rh_pct": 83, "rainfall": "SW Monsoon — Heavy Rain",          "condition": "Warm & Wet"},
    6:  {"min_c": 24, "max_c": 31, "daylight_h": 12.5, "rh_pct": 82, "rainfall": "SW Monsoon — Moderate Rain",       "condition": "Warm & Wet"},
    7:  {"min_c": 24, "max_c": 31, "daylight_h": 12.5, "rh_pct": 80, "rainfall": "SW Monsoon — Moderate Rain",       "condition": "Warm & Wet"},
    8:  {"min_c": 24, "max_c": 31, "daylight_h": 12.3, "rh_pct": 79, "rainfall": "SW Monsoon — Light to Moderate",   "condition": "Warm & Humid"},
    9:  {"min_c": 23, "max_c": 31, "daylight_h": 12.1, "rh_pct": 78, "rainfall": "Moderate Showers",                 "condition": "Warm & Humid"},
    10: {"min_c": 23, "max_c": 31, "daylight_h": 12.0, "rh_pct": 79, "rainfall": "NE Inter-monsoonal Showers",       "condition": "Warm & Humid"},
    11: {"min_c": 22, "max_c": 30, "daylight_h": 11.8, "rh_pct": 80, "rainfall": "NE Monsoon — Moderate Rain",       "condition": "Warm & Wet"},
    12: {"min_c": 22, "max_c": 30, "daylight_h": 11.7, "rh_pct": 77, "rainfall": "NE Monsoon — Light Rain",          "condition": "Warm & Dry"},
}

_DEFAULT_PROFILE = {"min_c": 23, "max_c": 31, "daylight_h": 12.0, "rh_pct": 78, "rainfall": "Moderate", "condition": "Warm & Humid"}


def _forecast_summary(forecast: list[dict]) -> dict:
    if not forecast:
        return {
            "avg_min_temp_c": None,
            "avg_max_temp_c": None,
            "avg_daylight_hours": None,
        }
    return {
        "avg_min_temp_c": mean([d["min_temp_c"] for d in forecast if d.get("min_temp_c") is not None]),
        "avg_max_temp_c": mean([d["max_temp_c"] for d in forecast if d.get("max_temp_c") is not None]),
        "avg_daylight_hours": mean(
            [d["daylight_duration_seconds"] / 3600.0 for d in forecast if d.get("daylight_duration_seconds") is not None]
        ),
    }


def _temperature_score(avg_temp: float, crop: Crop) -> float:
    if crop.min_temp_c is None or crop.max_temp_c is None:
        return 70.0
    if crop.min_temp_c <= avg_temp <= crop.max_temp_c:
        return 100.0
    if avg_temp < crop.min_temp_c:
        return max(0.0, 100.0 - abs(crop.min_temp_c - avg_temp) * 10.0)
    return max(0.0, 100.0 - abs(avg_temp - crop.max_temp_c) * 10.0)


def _sunlight_score(avg_daylight_hours: float | None, crop: Crop) -> float:
    if avg_daylight_hours is None or crop.sunlight_hours_min is None:
        return 70.0
    if avg_daylight_hours >= crop.sunlight_hours_min:
        return 100.0
    deficit = crop.sunlight_hours_min - avg_daylight_hours
    return max(0.0, 100.0 - deficit * 20.0)


def _container_score(container_type: str, crop: Crop) -> float:
    scores = {
        "pot": {"leafy": 100.0, "fruiting": 80.0, "root": 50.0},
        "grow_bag": {"leafy": 100.0, "fruiting": 90.0, "root": 75.0},
        "ground": {"leafy": 100.0, "fruiting": 100.0, "root": 100.0},
    }
    return scores.get(container_type, scores["grow_bag"]).get(crop.category, 75.0)


def _recommended_layout(space_sqm: float, spacing_cm: int | None) -> tuple[int, str]:
    if not spacing_cm or spacing_cm <= 0:
        return 1, "1 container"
    plant_area_sqm = (spacing_cm / 100.0) ** 2
    count = max(1, int(space_sqm / plant_area_sqm))
    if count == 1:
        return 1, "1 container"
    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)
    return count, f"{rows} rows × {cols} cols ({count} plants)"


def _companion_synergy(crop: Crop, other_crops: list[Crop]) -> list[str]:
    if not crop.companion_crops:
        return []
    companion_names = [name.strip().lower() for name in crop.companion_crops.split(",")]
    notes = []
    for other in other_crops:
        if other.id == crop.id:
            continue
        if other.name.lower() in companion_names:
            notes.append(f"Grows well with {other.name}")
    return notes


async def generate_crop_plan(
    lat: float,
    lon: float,
    container_type: str,
    space_sqm: float,
    target_month: int,
    db: Session,
) -> dict:
    """Generate a ranked crop plan for a given location, container, and space."""
    # Decide data source: live forecast for the current month, historical
    # archive for any other target month.
    current_month = date.today().month
    use_live = (target_month == current_month)

    try:
        if use_live:
            microclimate = await weather_service.fetch_microclimate(lat, lon)
        else:
            microclimate = await weather_service.fetch_climate_normals(lat, lon, target_month)
    except Exception as e:
        logger.warning("[Weather API Error] lat=%s lon=%s month=%s: %s", lat, lon, target_month, e)
        microclimate = {"current": {}, "forecast": []}

    summary = _forecast_summary(microclimate.get("forecast", []))

    # Extract daily average RH from the forecast (one value per day)
    daily_rh: list[float | None] = [
        day.get("avg_rh_percent") for day in microclimate.get("forecast", [])
    ]

    # Extract daily precipitation from the forecast (one value per day)
    daily_precip: list[float | None] = [
        day.get("precipitation_mm") for day in microclimate.get("forecast", [])
    ]

    crops = db.query(Crop).all()
    avg_temp = (
        (summary["avg_min_temp_c"] + summary["avg_max_temp_c"]) / 2.0
        if summary["avg_min_temp_c"] is not None and summary["avg_max_temp_c"] is not None
        else None
    )

    scored = []
    for crop in crops:
        temp_score = _temperature_score(avg_temp or 25.0, crop)
        sun_score = _sunlight_score(summary["avg_daylight_hours"], crop)
        cont_score = _container_score(container_type, crop)
        score = int(round(temp_score * 0.4 + sun_score * 0.3 + cont_score * 0.3))

        pot_count, layout = _recommended_layout(space_sqm, crop.spacing_cm)
        notes = []
        if crop.days_to_harvest:
            notes.append(f"Harvestable in ~{crop.days_to_harvest} days")
        if crop.watering_frequency_days:
            notes.append(f"Water roughly every {crop.watering_frequency_days} day(s)")

        scored.append(
            {
                "crop": crop,
                "suitability_score": score,
                "recommended_pot_count": pot_count,
                "layout": layout,
                "companion_synergy": [],
                "notes": "; ".join(notes) if notes else "",
            }
        )

    scored.sort(key=lambda x: x["suitability_score"], reverse=True)

    # Fill companion synergy using top viable crops (score > 50)
    viable = [s["crop"] for s in scored if s["suitability_score"] > 50]
    for s in scored:
        s["companion_synergy"] = _companion_synergy(s["crop"], viable)

    # Build weekly forecast for the frontend weather cards
    weekly_forecast = []
    for day in microclimate.get("forecast", []):
        weekly_forecast.append({
            "date": day.get("date"),
            "min_temp_c": day.get("min_temp_c"),
            "max_temp_c": day.get("max_temp_c"),
            "daylight_hours": round(day.get("daylight_duration_seconds", 0) / 3600.0, 1) if day.get("daylight_duration_seconds") else None,
            "avg_rh_percent": day.get("avg_rh_percent"),
            "precipitation_mm": day.get("precipitation_mm"),
        })

    # Build 4-week growing season outlook for the target month
    growing_season_outlook = _build_growing_season_outlook(target_month, summary)

    # Build dynamic crop timeline for the #1 recommended crop
    top_crop = scored[0]["crop"] if scored else None
    crop_timeline = (
        _build_crop_timeline(top_crop, target_month, summary, daily_rh=daily_rh, daily_precip=daily_precip, lat=lat)
        if top_crop else None
    )

    return {
        "location": {"lat": lat, "lon": lon},
        "container_type": container_type,
        "space_sqm": space_sqm,
        "target_month": target_month,
        "is_current_month": use_live,
        "forecast_summary": summary,
        "weekly_forecast": weekly_forecast,
        "growing_season_outlook": growing_season_outlook,
        "crop_timeline": crop_timeline,
        "recommendations": scored,
    }


def _build_growing_season_outlook(target_month: int, summary: dict) -> list[dict]:
    """
    Generate a 4-week growing season outlook for the target month.
    Each week represents a growth phase: Germination, Vegetative, Flowering, Maturation.
    Climate data is based on typical Sri Lankan urban patterns for the target month.
    """
    profile = MONTH_PROFILES.get(target_month, _DEFAULT_PROFILE)

    # Use actual forecast averages if available, otherwise use profile
    avg_min = summary.get("avg_min_temp_c") or profile["min_c"]
    avg_max = summary.get("avg_max_temp_c") or profile["max_c"]
    avg_daylight = summary.get("avg_daylight_hours") or profile["daylight_h"]

    # 4 growth phases
    phases = [
        {
            "week_number": 1,
            "phase": "Germination / Seeding",
            "phase_emoji": "🌱",
            "phase_color": "bg-green-100 text-green-800 border-green-200",
        },
        {
            "week_number": 2,
            "phase": "Vegetative Growth",
            "phase_emoji": "🌿",
            "phase_color": "bg-emerald-100 text-emerald-800 border-emerald-200",
        },
        {
            "week_number": 3,
            "phase": "Flowering / Fruit Set",
            "phase_emoji": "🌸",
            "phase_color": "bg-pink-100 text-pink-800 border-pink-200",
        },
        {
            "week_number": 4,
            "phase": "Maturation / Harvest",
            "phase_emoji": "🍅",
            "phase_color": "bg-orange-100 text-orange-800 border-orange-200",
        },
    ]

    outlook = []
    for p in phases:
        # Slight variation in temp across weeks
        week_offset = (p["week_number"] - 2.5) * 0.5  # -0.75 to +0.75
        week_min = round(avg_min + week_offset, 0)
        week_max = round(avg_max + week_offset, 0)

        outlook.append({
            "week_number": p["week_number"],
            "phase": p["phase"],
            "phase_emoji": p["phase_emoji"],
            "phase_color": p["phase_color"],
            "avg_temp_range": f"{int(week_min)}–{int(week_max)}°C",
            "min_temp_c": round(week_min, 1),
            "max_temp_c": round(week_max, 1),
            "relative_humidity_pct": profile.get("rh_pct", 78),
            "rainfall_pattern": profile["rainfall"],
            "daylight_avg": f"{avg_daylight:.1f}h",
            "condition_summary": profile["condition"],
        })

    return outlook


def _estimate_rh_from_lat_temp(lat: float | None, avg_temp: float | None) -> int:
    """Estimate baseline relative humidity from latitude and average temperature.

    Tropical coastal regions (Sri Lanka, ~6-10°N) typically sustain 75-85% RH
    year-round.  Higher latitudes and continental interiors trend lower.
    Warmer temperatures at the same latitude slightly depress RH due to higher
    saturation vapour pressure.
    """
    if lat is None:
        return 75  # global default

    abs_lat = abs(lat)
    # Base RH decreases with latitude (tropics humid → temperate drier)
    if abs_lat < 10:
        base = 80
    elif abs_lat < 25:
        base = 72
    elif abs_lat < 40:
        base = 65
    else:
        base = 58

    # Warm temperatures nudge RH down slightly (more evaporation capacity)
    if avg_temp is not None and avg_temp > 28:
        base -= round((avg_temp - 28) * 1.5)

    return max(40, min(90, base))


def _action_tip(phase: str, rainfall: str, rh: int, condition: str) -> str:
    """Generate a concise, actionable care tip for a specific growth week.

    Combines the crop's current growth phase with the prevailing climate
    conditions to produce practical balcony-gardening advice.
    """
    phase_lower = phase.lower()
    rain_lower = rainfall.lower()
    is_wet = "monsoon" in rain_lower or "rain" in rain_lower or "shower" in rain_lower
    is_dry = "dry" in rain_lower or "sunshine" in rain_lower

    if "germination" in phase_lower or "seedling" in phase_lower:
        if is_wet:
            return "Protect seedling trays from direct heavy downpours; ensure container drainage holes stay clear."
        if is_dry:
            return "Keep potting mix consistently moist; shade tender sprouts from scorching midday heat."
        return "Maintain even soil moisture; avoid waterlogging delicate root systems."

    if "vegetative" in phase_lower:
        if rh > 75 or is_wet:
            return "Water only at the root base early morning to avoid leaf fungus; ensure air circulation."
        if is_dry:
            return "Rapid leaf expansion phase; feed weekly with compost tea or dilute organic nitrogen booster."
        return "Side-dress with compost; pinch lateral shoots to encourage bushier growth."

    if "flowering" in phase_lower or "fruit set" in phase_lower:
        if is_wet:
            return "Stake stems against monsoon gusts; avoid splashing flowers to safeguard pollination."
        return "Top-dress with potassium/wood ash or compost; maintain even soil moisture to stop bloom drop."

    if "maturation" in phase_lower or "harvest" in phase_lower:
        return "Cut leaves or pick pods early morning for peak crispness; reduce heavy watering."

    return "Monitor plant health daily and adjust care as conditions change."


def build_timeline_for_crop(
    crop_name: str,
    harvest_days: int,
    target_month: int,
    summary: dict | None = None,
    daily_rh: list[float | None] | None = None,
    daily_precip: list[float | None] | None = None,
    lat: float | None = None,
) -> dict | None:
    """
    Build a dynamic week-by-week growth timeline for any crop.
    Accepts crop_name + harvest_days directly (no ORM dependency).
    Total weeks = ceil(days_to_harvest / 7). Weeks are divided proportionally into
    four stages: Germination, Vegetative, Flowering, and Harvest.
    Each week is mapped against the target month's climatic averages.

    If *daily_rh* is provided (one value per forecast day, from Open-Meteo
    hourly readings), the first ``len(daily_rh) // 7`` weeks will use the
    actual weekly average.  Remaining weeks fall back to a latitude +
    temperature estimate.
    """
    if not harvest_days or harvest_days < 1:
        return None

    total_weeks = math.ceil(harvest_days / 7)
    total_weeks = max(total_weeks, 2)

    # Proportional stage division (percentages of total weeks)
    germ_weeks = max(1, round(total_weeks * 0.15))
    veg_weeks = max(1, round(total_weeks * 0.35))
    flower_weeks = max(1, round(total_weeks * 0.30))
    harvest_weeks = max(1, total_weeks - germ_weeks - veg_weeks - flower_weeks)

    stages = []
    for _ in range(germ_weeks):
        stages.append({"phase": "Germination / Seedling", "phase_emoji": "🌱", "phase_color": "bg-green-100 text-green-800 border-green-200"})
    for _ in range(veg_weeks):
        stages.append({"phase": "Vegetative Growth", "phase_emoji": "🌿", "phase_color": "bg-emerald-100 text-emerald-800 border-emerald-200"})
    for _ in range(flower_weeks):
        stages.append({"phase": "Flowering / Fruit Set", "phase_emoji": "🌸", "phase_color": "bg-pink-100 text-pink-800 border-pink-200"})
    for _ in range(harvest_weeks):
        stages.append({"phase": "Maturation / Harvest", "phase_emoji": "🍅", "phase_color": "bg-orange-100 text-orange-800 border-orange-200"})

    profile = MONTH_PROFILES.get(target_month, _DEFAULT_PROFILE)
    summary = summary or {}

    avg_min = summary.get("avg_min_temp_c") or profile["min_c"]
    avg_max = summary.get("avg_max_temp_c") or profile["max_c"]
    avg_daylight = summary.get("avg_daylight_hours") or profile["daylight_h"]

    # Build per-week RH from actual daily data where available
    daily_rh = daily_rh or []
    api_weekly_rh = _weekly_rh_from_daily(daily_rh)

    # Build per-week precipitation totals from daily data
    daily_precip = daily_precip or []
    api_weekly_precip = _weekly_precip_from_daily(daily_precip)

    avg_temp_c = None
    if avg_min is not None and avg_max is not None:
        avg_temp_c = (avg_min + avg_max) / 2.0
    fallback_rh = _estimate_rh_from_lat_temp(lat, avg_temp_c)

    weeks = []
    for i in range(total_weeks):
        stage = stages[i] if i < len(stages) else stages[-1]
        progress = i / max(total_weeks - 1, 1)
        temp_drift = (progress - 0.5) * 2.0
        week_min = round(avg_min + temp_drift, 1)
        week_max = round(avg_max + temp_drift, 1)

        # Use actual API data for this week if available, otherwise fallback
        if i < len(api_weekly_rh) and api_weekly_rh[i] is not None:
            week_rh = max(30, min(98, round(api_weekly_rh[i])))
        else:
            # Stable fallback with tiny natural oscillation (±1%)
            rh_jitter = round(math.sin(i * 2.3) * 1.0)
            week_rh = max(30, min(98, fallback_rh + rh_jitter))

        day_start = i * 7 + 1
        day_end = min((i + 1) * 7, harvest_days)

        # Compute per-week precipitation and dynamic rainfall label
        if i < len(api_weekly_precip):
            week_precip = api_weekly_precip[i]
        else:
            # Rough fallback: spread monthly expectation evenly per week
            week_precip = 0.0
        week_rainfall = _rainfall_label(week_precip, profile["rainfall"])

        weeks.append({
            "week_number": i + 1,
            "phase": stage["phase"],
            "phase_emoji": stage["phase_emoji"],
            "phase_color": stage["phase_color"],
            "day_range": f"Day {day_start}–{day_end}",
            "avg_temp_range": f"{int(week_min)}–{int(week_max)}°C",
            "min_temp_c": week_min,
            "max_temp_c": week_max,
            "relative_humidity_pct": week_rh,
            "rainfall_pattern": week_rainfall,
            "precipitation_mm": week_precip,
            "daylight_avg": f"{avg_daylight:.1f}h",
            "condition_summary": profile["condition"],
            "action_tip": _action_tip(stage["phase"], week_rainfall, week_rh, profile["condition"]),
        })

    return {
        "crop_name": crop_name,
        "harvest_days": harvest_days,
        "total_weeks": total_weeks,
        "weeks": weeks,
    }


def _weekly_rh_from_daily(daily_rh: list[float | None]) -> list[float | None]:
    """Convert daily RH values into weekly averages (7 days per week)."""
    if not daily_rh:
        return []
    weekly: list[float | None] = []
    for start in range(0, len(daily_rh), 7):
        chunk = [v for v in daily_rh[start:start + 7] if v is not None]
        weekly.append(round(mean(chunk), 1) if chunk else None)
    return weekly


def _weekly_precip_from_daily(daily_precip: list[float | None]) -> list[float]:
    """Sum daily precipitation values into weekly totals (7 days per week)."""
    if not daily_precip:
        return []
    weekly: list[float] = []
    for start in range(0, len(daily_precip), 7):
        chunk = [v for v in daily_precip[start:start + 7] if v is not None]
        weekly.append(round(sum(chunk), 1) if chunk else 0.0)
    return weekly


def _rainfall_label(weekly_precip_mm: float, monthly_label: str) -> str:
    """Return a week-specific rainfall label based on actual weekly precipitation.

    - < 5 mm:  dry / sunny spells
    - 5–20 mm: scattered / light showers
    - >= 20 mm: keep the seasonal badge (monsoon / heavy showers)
    """
    if weekly_precip_mm < 5:
        return "Sunny / Dry Spells"
    if weekly_precip_mm < 20:
        return "Scattered Showers"
    # Heavy rain — keep the seasonal context from the monthly profile
    return monthly_label


def _build_crop_timeline(
    top_crop: Crop,
    target_month: int,
    summary: dict,
    daily_rh: list[float | None] | None = None,
    daily_precip: list[float | None] | None = None,
    lat: float | None = None,
) -> dict | None:
    """Thin wrapper: delegates to build_timeline_for_crop using Crop ORM fields."""
    return build_timeline_for_crop(
        crop_name=top_crop.name,
        harvest_days=top_crop.days_to_harvest or 0,
        target_month=target_month,
        summary=summary,
        daily_rh=daily_rh,
        daily_precip=daily_precip,
        lat=lat,
    )
