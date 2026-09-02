import logging
from datetime import datetime, date

import httpx

from backend.app.core.config import settings

logger = logging.getLogger(__name__)


async def fetch_microclimate(lat: float, lon: float) -> dict:
    """Fetch current conditions and a 7-day forecast from Open-Meteo."""
    url = f"{settings.OPEN_METEO_BASE_URL}/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m",
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability",
        "daily": "temperature_2m_min,temperature_2m_max,daylight_duration,precipitation_sum",
        "timezone": "auto",
        "forecast_days": 7,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    current = data.get("current", {})
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})

    # Locate the current hour in the hourly arrays so we can report the
    # precipitation probability for that hour.
    current_time = current.get("time", "")
    hourly_times = hourly.get("time", [])
    precip_probability = None
    if current_time and hourly_times:
        current_hour_prefix = current_time[:13]  # e.g. "2026-08-28T12"
        for idx, t in enumerate(hourly_times):
            if t[:13] == current_hour_prefix:
                precip_probability = hourly.get("precipitation_probability", [])[idx]
                break

    # Compute daily average RH from 24 hourly readings per day.
    hourly_rh = hourly.get("relative_humidity_2m", [])
    daily_rh_avg = _chunk_daily_average(hourly_rh, hourly_times)

    forecast = []
    daily_dates = daily.get("time", [])
    daily_mins = daily.get("temperature_2m_min", [])
    daily_maxs = daily.get("temperature_2m_max", [])
    daily_daylight = daily.get("daylight_duration", [])
    daily_precip = daily.get("precipitation_sum", [])

    for i, date in enumerate(daily_dates):
        forecast.append(
            {
                "date": date,
                "min_temp_c": daily_mins[i] if i < len(daily_mins) else None,
                "max_temp_c": daily_maxs[i] if i < len(daily_maxs) else None,
                "daylight_duration_seconds": daily_daylight[i]
                if i < len(daily_daylight)
                else None,
                "avg_rh_percent": daily_rh_avg[i] if i < len(daily_rh_avg) else None,
                "precipitation_mm": daily_precip[i] if i < len(daily_precip) else None,
            }
        )

    return {
        "latitude": lat,
        "longitude": lon,
        "current": {
            "temperature_c": current.get("temperature_2m"),
            "relative_humidity_percent": current.get("relative_humidity_2m"),
            "precipitation_probability_percent": precip_probability,
            "time": current_time,
        },
        "forecast": forecast,
    }


def _chunk_daily_average(hourly_values: list, hourly_times: list) -> list[float | None]:
    """Average hourly readings into daily means.

    Open-Meteo returns hourly arrays with 24 readings per day (one per hour),
    aligned to the daily ``time`` array.  We group consecutive 24-element
    chunks and return the mean of each chunk.
    """
    if not hourly_values or not hourly_times:
        return []

    # Build a date→readings map from the hourly timestamps
    day_buckets: dict[str, list[float]] = {}
    for idx, t in enumerate(hourly_times):
        if idx >= len(hourly_values):
            break
        val = hourly_values[idx]
        if val is None:
            continue
        day = t[:10]  # "YYYY-MM-DD"
        day_buckets.setdefault(day, []).append(val)

    averages: list[float | None] = []
    seen_days = sorted(day_buckets.keys())
    for day in seen_days:
        vals = day_buckets[day]
        averages.append(round(sum(vals) / len(vals), 1) if vals else None)

    return averages


async def fetch_climate_normals(lat: float, lon: float, target_month: int) -> dict:
    """Fetch historical climate data for *target_month* using last year's
    archive from Open-Meteo.

    The Forecast API only returns current + next-16-day data, so for any
    target month that is not the current calendar month we query the
    Historical Weather API instead.  Returns the same structure as
    ``fetch_microclimate`` so the planner can use both interchangeably.
    """
    today = date.today()
    # Use last year for the target month (most recent complete year of data)
    hist_year = today.year - 1
    start = date(hist_year, target_month, 1)
    # End on the 28th to avoid short-month edge cases
    end = date(hist_year, target_month, 28)

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_min,temperature_2m_max,daylight_duration,precipitation_sum",
        "hourly": "relative_humidity_2m",
        "timezone": "auto",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning(
            "[Climate API Error] fetch_climate_normals(%s, %s, month=%s) failed: %s",
            lat, lon, target_month, e,
        )
        # Return empty structure — caller will fall back to MONTH_PROFILES
        return {"latitude": lat, "longitude": lon, "current": {}, "forecast": []}

    hourly = data.get("hourly", {})
    daily = data.get("daily", {})

    # Compute daily average RH from hourly readings
    hourly_rh = hourly.get("relative_humidity_2m", [])
    hourly_times = hourly.get("time", [])
    daily_rh_avg = _chunk_daily_average(hourly_rh, hourly_times)

    forecast = []
    daily_dates = daily.get("time", [])
    daily_mins = daily.get("temperature_2m_min", [])
    daily_maxs = daily.get("temperature_2m_max", [])
    daily_daylight = daily.get("daylight_duration", [])
    daily_precip = daily.get("precipitation_sum", [])

    for i, day_date in enumerate(daily_dates):
        forecast.append({
            "date": day_date,
            "min_temp_c": daily_mins[i] if i < len(daily_mins) else None,
            "max_temp_c": daily_maxs[i] if i < len(daily_maxs) else None,
            "daylight_duration_seconds": daily_daylight[i] if i < len(daily_daylight) else None,
            "avg_rh_percent": daily_rh_avg[i] if i < len(daily_rh_avg) else None,
            "precipitation_mm": daily_precip[i] if i < len(daily_precip) else None,
        })

    return {
        "latitude": lat,
        "longitude": lon,
        "current": {},
        "forecast": forecast,
    }
