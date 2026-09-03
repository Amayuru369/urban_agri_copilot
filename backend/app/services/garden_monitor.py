"""Plant Monitoring & Alert Service.

Background evaluation engine that:
1. Tracks plant growth days and cross-references a milestone matrix[cite: 1].
2. Fetches live weather risk indicators from Open-Meteo[cite: 1].
3. Persists alerts to the database (in-app channel)[cite: 1].
4. Sends Telegram bot notifications (out-of-band channel)[cite: 1].
"""

import asyncio
import logging
from datetime import date

import httpx
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.garden import PlantAlert, TrackedPlant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Milestone Schedule — day targets per crop with care actions
# ---------------------------------------------------------------------------

MILESTONE_SCHEDULE: dict[str, list[dict]] = {
    "Tomato": [
        {
            "day": 7,
            "label": "Germination complete",
            "action": "Remove humidity dome; begin gentle light exposure (4-6h indirect sun).",
            "severity": "info",
        },
        {
            "day": 21,
            "label": "Transplant readiness",
            "action": "Move to final container (≥15L). Begin weekly dilute feed. Stake the main stem.",
            "severity": "info",
        },
        {
            "day": 45,
            "label": "First flowering",
            "action": "Switch to potassium-rich feed. Gently shake flowers for pollination. Watch for blossom-end rot.",
            "severity": "info",
        },
        {
            "day": 60,
            "label": "Fruit set visible",
            "action": "Prune suckers below first truss. Mulch to retain moisture. Ensure consistent watering.",
            "severity": "info",
        },
        {
            "day": 75,
            "label": "Harvest window opens",
            "action": "Pick ripe fruit daily (uniform colour, slight give). Reduce watering to concentrate flavour.",
            "severity": "info",
        },
    ],
    "Chilli": [
        {
            "day": 10,
            "label": "Germination complete",
            "action": "Move to warm, bright spot (≥20°C). Thin to one seedling per cell.",
            "severity": "info",
        },
        {
            "day": 30,
            "label": "Vegetative establishment",
            "action": "Transplant to ≥10L pot. Apply balanced NPK. Begin weekly neem oil spray for mite prevention.",
            "severity": "info",
        },
        {
            "day": 55,
            "label": "Flowering onset",
            "action": "Switch to bloom booster (high P/K). Avoid overhead watering to protect flowers.",
            "severity": "info",
        },
        {
            "day": 70,
            "label": "Fruit development",
            "action": "Support heavy branches with ties. Maintain even soil moisture to prevent blossom drop.",
            "severity": "info",
        },
        {
            "day": 90,
            "label": "Harvest ready",
            "action": "Pick when pods are firm and fully coloured. Use scissors to avoid stem damage.",
            "severity": "info",
        },
    ],
    "Gotukola": [
        {
            "day": 5,
            "label": "Sprouting visible",
            "action": "Keep soil consistently damp (never dry). Provide partial shade (50-70% light).",
            "severity": "info",
        },
        {
            "day": 14,
            "label": "Runner establishment",
            "action": "Thin crowded patches. Apply thin compost top-dressing. Ensure container drainage is clear.",
            "severity": "info",
        },
        {
            "day": 25,
            "label": "Canopy fill",
            "action": "Begin light harvesting of outer leaves. Feed with compost tea every 5 days.",
            "severity": "info",
        },
        {
            "day": 35,
            "label": "Full harvest cycle",
            "action": "Harvest handful-sized clumps daily. Replant runners to extend bed. Watch for leaf spot in wet weather.",
            "severity": "info",
        },
    ],
}

# Fallback for unlisted crops: generic milestones at 7, 21, 45, 70 days
_DEFAULT_MILESTONES = [
    {"day": 7, "label": "Germination check", "action": "Verify sprouts emerged; maintain even moisture.", "severity": "info"},
    {"day": 21, "label": "Vegetative growth", "action": "Begin weekly liquid feed; check for pests under leaves.", "severity": "info"},
    {"day": 45, "label": "Mid-cycle check", "action": "Assess container size; top-dress with compost if root-bound.", "severity": "info"},
    {"day": 70, "label": "Maturation", "action": "Monitor daily for harvest readiness. Reduce water for flavour concentration.", "severity": "info"},
]


# ---------------------------------------------------------------------------
# Weather Risk Evaluation (Batch Supported)
# ---------------------------------------------------------------------------

async def fetch_weather_risk_batch(coords_list: list[tuple[float, float]]) -> dict[tuple[float, float], dict]:
    """Fetch forecasts from Open-Meteo in a single batched HTTP request."""
    if not coords_list:
        return {}

    # Open-Meteo accepts comma-separated latitudes and longitudes
    lats = ",".join(str(c[0]) for c in coords_list)
    lons = ",".join(str(c[1]) for c in coords_list)

    url = f"{settings.OPEN_METEO_BASE_URL}/forecast"
    params = {
        "latitude": lats,
        "longitude": lons,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_gusts_10m_max",
        "timezone": "auto",
        "forecast_days": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning("[Garden Monitor] Batched weather fetch failed: %s", e)
        default_fallback = {
            "heavy_rain": False,
            "extreme_heat": False,
            "extreme_cold": False,
            "high_wind": False,
            "precipitation_mm": 0.0,
            "max_temp_c": 0.0,
            "min_temp_c": 0.0,
            "wind_gusts_kmh": 0.0,
            "description": "Weather data unavailable",
        }
        return {coord: default_fallback for coord in coords_list}

    # Open-Meteo returns a single dict if 1 location, or a list of dicts if multiple
    items = data if isinstance(data, list) else [data]
    result_map = {}

    for coord, item in zip(coords_list, items):
        daily = item.get("daily", {})
        precip_list = daily.get("precipitation_sum", [])
        max_temp_list = daily.get("temperature_2m_max", [])
        min_temp_list = daily.get("temperature_2m_min", [])
        wind_gusts_list = daily.get("wind_gusts_10m_max", [])

        precip_mm = precip_list[0] if (precip_list and precip_list[0] is not None) else 0.0
        max_temp_c = max_temp_list[0] if (max_temp_list and max_temp_list[0] is not None) else 0.0
        min_temp_c = min_temp_list[0] if (min_temp_list and min_temp_list[0] is not None) else 15.0
        wind_gusts_kmh = wind_gusts_list[0] if (wind_gusts_list and wind_gusts_list[0] is not None) else 0.0

        heavy_rain = precip_mm > 15.0
        extreme_heat = max_temp_c > 38.0
        extreme_cold = min_temp_c < 2.0
        high_wind = wind_gusts_kmh > 55.0

        parts = []
        if heavy_rain:
            parts.append(f"Heavy rain ({precip_mm:.0f}mm expected)")
        if extreme_heat:
            parts.append(f"Extreme heat ({max_temp_c:.1f}°C max)")
        if extreme_cold:
            parts.append(f"Frost/Cold risk ({min_temp_c:.1f}°C min)")
        if high_wind:
            parts.append(f"Damaging winds ({wind_gusts_kmh:.0f} km/h gusts)")

        description = "; ".join(parts) if parts else "Conditions normal"

        result_map[coord] = {
            "heavy_rain": heavy_rain,
            "extreme_heat": extreme_heat,
            "extreme_cold": extreme_cold,
            "high_wind": high_wind,
            "precipitation_mm": precip_mm,
            "max_temp_c": max_temp_c,
            "min_temp_c": min_temp_c,
            "wind_gusts_kmh": wind_gusts_kmh,
            "description": description,
        }

    return result_map
# ---------------------------------------------------------------------------
# Telegram Notification
# ---------------------------------------------------------------------------

async def send_telegram_alert(chat_id: str, text: str) -> bool:
    """Send an alert message via Telegram Bot API."""
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    if not bot_token:
        logger.debug("[Garden Monitor] TELEGRAM_BOT_TOKEN not configured; skipping Telegram alert.")
        return False

    if not chat_id:
        chat_id = getattr(settings, "TELEGRAM_CHAT_ID", None)
        
    if not chat_id:
        logger.debug("[Garden Monitor] No Telegram Chat ID provided or found in settings; skipping.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                return True
            logger.warning(
                "[Garden Monitor] Telegram send failed (HTTP %d): %s",
                response.status_code,
                response.text[:200],
            )
    except Exception as e:
        logger.warning("[Garden Monitor] Telegram send error for chat %s: %s", chat_id, e)

    return False


# ---------------------------------------------------------------------------
# Core Evaluation Engine
# ---------------------------------------------------------------------------

async def evaluate_garden_state(db: Session) -> int:
    """Evaluate all active tracked plants and generate alerts."""
    today = date.today()
    plants = db.query(TrackedPlant).filter(TrackedPlant.active == True).all()  # noqa: E712

    if not plants:
        logger.info("[Garden Monitor] No active tracked plants found.")
        return 0

    valid_plants = [p for p in plants if (today - p.planted_date).days >= 0]
    if not valid_plants:
        return 0

    # 1. Deduplicate coordinates and execute ONE batch request for all plants
    unique_coords = list({(p.latitude, p.longitude) for p in valid_plants})
    weather_map = await fetch_weather_risk_batch(unique_coords)

    new_alert_count = 0

    for plant in valid_plants:
        days_active = (today - plant.planted_date).days

        # --- Milestone Check ---
        schedule = MILESTONE_SCHEDULE.get(plant.crop_name, _DEFAULT_MILESTONES)
        for milestone in schedule:
            target_day = milestone["day"]
            if abs(days_active - target_day) <= 2:
                existing = (
                    db.query(PlantAlert)
                    .filter(
                        PlantAlert.plant_id == plant.id,
                        PlantAlert.alert_type == "milestone",
                        PlantAlert.triggered_on == today,
                        PlantAlert.message.contains(milestone["label"]),
                    )
                    .first()
                )
                if not existing:
                    message = (
                        f"🌱 {plant.crop_name} — Day {days_active}: "
                        f"{milestone['label']} (target: day {target_day})"
                    )
                    alert = PlantAlert(
                        plant_id=plant.id,
                        alert_type="milestone",
                        severity=milestone["severity"],
                        message=message,
                        action_required=milestone["action"],
                        resolved=False,
                        triggered_on=today,
                    )
                    db.add(alert)
                    new_alert_count += 1

                    target_chat = plant.telegram_chat_id or getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None)
                    if target_chat:
                        loc_name = plant.location_name or "Home Garden"
                        map_url = f"https://www.google.com/maps?q={plant.latitude},{plant.longitude}"
                        tg_text = (
                            f"🌱 <b>{plant.crop_name}</b> — Day {days_active}\n"
                            f"<a href='{map_url}'>📍 {loc_name}</a>\n\n"
                            f"<b>{milestone['label']}</b>\n"
                            f"👉 {milestone['action']}"
                        )
                        asyncio.create_task(send_telegram_alert(target_chat, tg_text))
                break

        # --- Weather Risk Check ---
        weather_risk = weather_map.get((plant.latitude, plant.longitude))
        if not weather_risk:
            continue

        has_risk = (
            weather_risk["heavy_rain"]
            or weather_risk["extreme_heat"]
            or weather_risk["extreme_cold"]
            or weather_risk["high_wind"]
        )

        if has_risk:
            actions = []
            if weather_risk["heavy_rain"]:
                actions.append("Move pots under shelter or cover with breathable cloth. Ensure drainage holes are clear.")
            if weather_risk["extreme_heat"]:
                actions.append("Move to partial shade during 11AM-3PM. Water deeply in early morning. Apply mulch.")
            if weather_risk["extreme_cold"]:
                actions.append("Bring containers indoors or wrap pots in frost cloth to insulate root zones.")
            if weather_risk["high_wind"]:
                actions.append("Stake tall stems, secure trellis netting, and place pots against a sheltered wall.")

            action = " ".join(actions)
            severity = "critical" if (weather_risk["extreme_cold"] or (weather_risk["heavy_rain"] and weather_risk["high_wind"])) else "warning"
            message = f"Weather alert for {plant.crop_name}: {weather_risk['description']}"

            existing = (
                db.query(PlantAlert)
                .filter(
                    PlantAlert.plant_id == plant.id,
                    PlantAlert.alert_type == "weather",
                    PlantAlert.triggered_on == today,
                    PlantAlert.resolved == False,
                )
                .first()
            )
            if not existing:
                alert = PlantAlert(
                    plant_id=plant.id,
                    alert_type="weather",
                    severity=severity,
                    message=message,
                    action_required=action,
                    resolved=False,
                    triggered_on=today,
                )
                db.add(alert)
                new_alert_count += 1

                target_chat = plant.telegram_chat_id or getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None)
                if target_chat:
                    loc_name = plant.location_name or "Home Garden"
                    map_url = f"https://www.google.com/maps?q={plant.latitude},{plant.longitude}"
                    
                    if weather_risk.get("extreme_cold") and weather_risk.get("high_wind"):
                        alert_icon = "❄️🌪️"
                        alert_title = "Severe Frost & Gale Warning"
                    elif weather_risk.get("heavy_rain") and weather_risk.get("high_wind"):
                        alert_icon = "🌧️🌪️"
                        alert_title = "Storm & Heavy Rain Warning"
                    elif weather_risk.get("high_wind"):
                        alert_icon = "🌪️"
                        alert_title = "High Wind Warning"
                    elif weather_risk.get("extreme_cold"):
                        alert_icon = "❄️"
                        alert_title = "Frost / Cold Risk"
                    elif weather_risk.get("heavy_rain"):
                        alert_icon = "🌧️"
                        alert_title = "Heavy Rain Expected"
                    elif weather_risk.get("extreme_heat"):
                        alert_icon = "🔥"
                        alert_title = "Extreme Heat Alert"
                    else:
                        alert_icon = "⚠️"
                        alert_title = "Weather Alert"
                    
                    tg_text = (
                        f"{alert_icon} <b>{alert_title}</b> — {plant.crop_name}\n"
                        f"<a href='{map_url}'>📍 {loc_name}</a>\n\n"
                        f"{weather_risk['description']}\n"
                        f"👉 {action}"
                    )
                    asyncio.create_task(send_telegram_alert(target_chat, tg_text))

    db.commit()
    logger.info("[Garden Monitor] Evaluation complete. %d new alert(s) generated.", new_alert_count)
    return new_alert_count


# ---------------------------------------------------------------------------
# Scheduler Entrypoint
# ---------------------------------------------------------------------------

async def run_scheduled_evaluation():
    """Background task entrypoint with its own managed DB session."""
    from backend.app.core.database import SessionLocal

    logger.info("[Scheduler] Running daily morning garden evaluation...")
    db: Session = SessionLocal()
    try:
        count = await evaluate_garden_state(db)
        logger.info("[Scheduler] Morning evaluation complete. %d new alert(s) generated.", count)
    except Exception as e:
        logger.exception("[Scheduler] Error during scheduled garden evaluation: %s", e)
    finally:
        db.close()