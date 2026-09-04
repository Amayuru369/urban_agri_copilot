"""Daily Morning Garden Digest Service.

Aggregates active crops, unresolved alerts, and today's local forecast
into a scannable Telegram briefing dispatched every morning at 07:00.

This module is fully additive — it does not alter existing monitor logic,
risk evaluation rules, or the audit trail.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

import httpx
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.garden import PlantAlert, TrackedPlant
from backend.app.services.garden_monitor import send_telegram_alert

logger = logging.getLogger(__name__)

# Default fallback coordinates (Colombo, Sri Lanka) when no plants are registered.
DEFAULT_LAT = 6.9271
DEFAULT_LON = 79.8612


# ---------------------------------------------------------------------------
# Weather Fetch (independent of monitor batch to keep concerns isolated)
# ---------------------------------------------------------------------------

async def _fetch_today_forecast(lat: float, lon: float) -> dict:
    """Fetch today's forecast (max/min temp, rain sum & probability) from Open-Meteo."""
    url = f"{settings.OPEN_METEO_BASE_URL}/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(url, params=params)
            res.raise_for_status()
            data = res.json()
    except Exception as e:
        logger.warning("[Morning Digest] Open-Meteo forecast fetch failed for (%s, %s): %s", lat, lon, e)
        return {
            "max_temp": None,
            "min_temp": None,
            "precip_mm": None,
            "precip_prob": None,
            "error": True,
        }

    daily = data.get("daily", {}) or {}

    def _first(key: str):
        values = daily.get(key) or []
        return values[0] if values else None

    return {
        "max_temp": _first("temperature_2m_max"),
        "min_temp": _first("temperature_2m_min"),
        "precip_mm": _first("precipitation_sum"),
        "precip_prob": _first("precipitation_probability_max"),
        "error": False,
    }


# ---------------------------------------------------------------------------
# Digest Builder
# ---------------------------------------------------------------------------

def _fmt_temp(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}°C"
    except (TypeError, ValueError):
        return "—"


def _fmt_precip(mm, prob) -> str:
    parts = []
    if mm is not None:
        try:
            parts.append(f"{float(mm):.1f} mm")
        except (TypeError, ValueError):
            parts.append("— mm")
    else:
        parts.append("— mm")

    if prob is not None:
        try:
            parts.append(f"{int(float(prob))}% chance")
        except (TypeError, ValueError):
            pass

    return " · ".join(parts)


def _build_digest(
    today: date,
    location_forecasts: list[dict],
    total_crop_count: int,
    alerts: list[PlantAlert]
) -> str:
    """Compose the HTML-formatted Telegram morning briefing grouped by site."""
    header = f"🌅 <b>Morning Garden Digest — {today.strftime('%A, %d %B %Y')}</b>"

    # Location sections (weather + plant list per site)
    location_blocks = []
    for section in location_forecasts:
        loc_name = section["location_name"]
        weather = section["weather"]
        crops = section["crops"]

        if weather.get("error"):
            weather_line = "  ☁️ <i>Weather unavailable</i>"
        else:
            weather_line = (
                f"  🌡️ High / Low: {_fmt_temp(weather.get('max_temp'))} / {_fmt_temp(weather.get('min_temp'))}\n"
                f"  🌧️ Rain: {_fmt_precip(weather.get('precip_mm'), weather.get('precip_prob'))}"
            )

        crop_lines = []
        for p in crops:
            try:
                days = (today - p.planted_date).days
            except Exception:
                days = 0
            crop_lines.append(f"  • <b>{p.crop_name}</b> — Day {days}")

        block = (
            f"\n\n📍 <b>{loc_name}</b>\n"
            f"{weather_line}\n"
            f"  <u>Crops ({len(crops)}):</u>\n" + "\n".join(crop_lines)
        )
        location_blocks.append(block)

    if not location_blocks:
        body = "\n\n🌿 <b>Active Crops</b>\n  No crops registered yet."
    else:
        body = "".join(location_blocks)

    # Alerts summary
    if alerts:
        alerts_block = (
            f"\n\n⚠️ <b>Unresolved Alerts:</b> {len(alerts)} pending — "
            "open the Garden Dashboard for action steps."
        )
    else:
        alerts_block = "\n\n✅ <b>Unresolved Alerts:</b> None. All crops healthy."

    footer = "\n\n<i>Have a fruitful day! 🌱</i>"

    return header + body + alerts_block + footer


# ---------------------------------------------------------------------------
# Chat ID Resolution
# ---------------------------------------------------------------------------

def _resolve_chat_ids(plants: list[TrackedPlant]) -> set[str]:
    """Collect unique Telegram chat IDs from plants and settings fallback."""
    chat_ids: set[str] = set()

    for plant in plants:
        cid = (
            getattr(plant, "telegram_chat_id", None)
            or getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None)
            or getattr(settings, "TELEGRAM_CHAT_ID", None)
        )
        if cid:
            chat_ids.add(str(cid))

    if not chat_ids:
        fallback = (
            getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None)
            or getattr(settings, "TELEGRAM_CHAT_ID", None)
        )
        if fallback:
            chat_ids.add(str(fallback))

    return chat_ids


# ---------------------------------------------------------------------------
# Public Entrypoint (invoked by APScheduler at 07:00 daily)
# ---------------------------------------------------------------------------

async def send_morning_garden_digest() -> None:
    """Aggregate garden state and dispatch the morning briefing via Telegram."""
    from backend.app.core.database import SessionLocal

    logger.info("[Morning Digest] Starting daily 07:00 garden digest...")
    db: Session = SessionLocal()

    try:
        today = date.today()

        # 1. Query active crops and open alerts
        plants = db.query(TrackedPlant).filter(TrackedPlant.active == True).all()  # noqa: E712
        alerts = db.query(PlantAlert).filter(PlantAlert.resolved == False).all()  # noqa: E712

        # 2. Cluster crops by (location_name, rounded_coords)
        # Snapping to 2 decimal places (~1.1 km) absorbs minor GPS drift
        clusters: dict[tuple[str, float, float], list[TrackedPlant]] = defaultdict(list)
        for p in plants:
            raw_lat = p.latitude if p.latitude is not None else DEFAULT_LAT
            raw_lon = p.longitude if p.longitude is not None else DEFAULT_LON
            snap_lat = round(float(raw_lat), 2)
            snap_lon = round(float(raw_lon), 2)
            loc_name = (getattr(p, "location_name", None) or "Home Garden").strip()
            clusters[(loc_name, snap_lat, snap_lon)].append(p)

        # Detect if the same location label was reused across different regions
        name_frequency: dict[str, int] = defaultdict(int)
        for name, _, _ in clusters.keys():
            name_frequency[name] += 1

        # 3. Fetch localized weather forecast per geographic cluster
        location_forecasts: list[dict] = []
        if clusters:
            for (name, snap_lat, snap_lon), crop_list in clusters.items():
                # Add coordinates only if the user used the same name in multiple distant places
                display_name = f"{name} ({snap_lat}, {snap_lon})" if name_frequency[name] > 1 else name
                weather = await _fetch_today_forecast(snap_lat, snap_lon)
                location_forecasts.append({
                    "location_name": display_name,
                    "weather": weather,
                    "crops": crop_list,
                })
        else:
            # Fallback if no crops exist
            weather = await _fetch_today_forecast(DEFAULT_LAT, DEFAULT_LON)
            location_forecasts.append({
                "location_name": "Default Garden Area",
                "weather": weather,
                "crops": [],
            })

        # 4. Compose briefing message
        digest_msg = _build_digest(today, location_forecasts, len(plants), alerts)

        # 5. Dispatch to resolved Telegram chats
        chat_ids = _resolve_chat_ids(plants)
        if not chat_ids:
            logger.info("[Morning Digest] No Telegram chat IDs resolved; digest not dispatched.")
            return

        sent = 0
        for chat_id in chat_ids:
            try:
                ok = await send_telegram_alert(chat_id, digest_msg)
                if ok:
                    sent += 1
            except Exception as e:
                logger.warning("[Morning Digest] Telegram dispatch failed for chat %s: %s", chat_id, e)

        logger.info(
            "[Morning Digest] Dispatched to %d/%d chat(s). Sites=%d, Plants=%d, Alerts=%d.",
            sent, len(chat_ids), len(clusters), len(plants), len(alerts),
        )

    except Exception as e:
        logger.exception("[Morning Digest] Unexpected error while generating digest: %s", e)
    finally:
        db.close()