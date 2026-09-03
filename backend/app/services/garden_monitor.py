"""Plant Monitoring & Alert Service.

Background evaluation engine that:
1. Tracks plant growth days and cross-references a milestone matrix.
2. Fetches live weather risk indicators from Open-Meteo.
3. Persists alerts to the database (in-app channel).
4. Sends Telegram bot notifications (out-of-band channel).
"""

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
# Weather Risk Evaluation
# ---------------------------------------------------------------------------




    # --- TEMPORARY TEST RETURN ---
   
async def fetch_weather_risk(lat: float, lon: float) -> dict:
    """Fetch today's forecast from Open-Meteo and return risk flags.

    Returns:
        {
            "heavy_rain": bool,       # precipitation_sum > 20mm
            "extreme_heat": bool,     # temperature_2m_max > 33.5°C
            "precipitation_mm": float,
            "max_temp_c": float,
            "description": str,       # human-readable risk summary
        }
    """
    url = f"{settings.OPEN_METEO_BASE_URL}/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,precipitation_sum",
        "timezone": "auto",
        "forecast_days": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning("[Garden Monitor] Weather fetch failed for (%s, %s): %s", lat, lon, e)
        return {
            "heavy_rain": False,
            "extreme_heat": False,
            "precipitation_mm": 0.0,
            "max_temp_c": 0.0,
            "description": "Weather data unavailable",
        }

    daily = data.get("daily", {})
    precip_list = daily.get("precipitation_sum", [])
    max_temp_list = daily.get("temperature_2m_max", [])

    precip_mm = precip_list[0] if precip_list else 0.0
    max_temp_c = max_temp_list[0] if max_temp_list else 0.0

    heavy_rain = precip_mm > 20.0
    extreme_heat = max_temp_c > 33.5

    # Build description
    parts = []
    if heavy_rain:
        parts.append(f"Heavy rain ({precip_mm:.0f}mm expected)")
    if extreme_heat:
        parts.append(f"Extreme heat ({max_temp_c:.1f}°C max)")
    description = "; ".join(parts) if parts else "Conditions normal"

    return {
        "heavy_rain": heavy_rain,
        "extreme_heat": extreme_heat,
        "precipitation_mm": precip_mm or 0.0,
        "max_temp_c": max_temp_c or 0.0,
        "description": description,
    }
# ---------------------------------------------------------------------------
# Telegram Notification
# ---------------------------------------------------------------------------


async def send_telegram_alert(chat_id: str, text: str) -> bool:
    """Send an alert message via Telegram Bot API."""
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    if not bot_token:
        logger.debug("[Garden Monitor] TELEGRAM_BOT_TOKEN not configured; skipping Telegram alert.")
        return False

    # Fallback to default chat ID if the plant doesn't have one
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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
    """Evaluate all active tracked plants and generate alerts.

    For each active plant:
    1. Calculate days since planting.
    2. Check milestone schedule (±2 day window) for upcoming/overdue actions.
    3. Fetch weather risk for the plant's location.
    4. Persist PlantAlert records if they don't already exist for today.
    5. Send Telegram notification if the plant has a telegram_chat_id.

    Returns:
        Number of new alerts generated.
    """
    today = date.today()
    plants = db.query(TrackedPlant).filter(TrackedPlant.active == True).all()  # noqa: E712

    if not plants:
        logger.info("[Garden Monitor] No active tracked plants found.")
        return 0

    new_alert_count = 0

    for plant in plants:
        days_active = (today - plant.planted_date).days
        if days_active < 0:
            continue  # Not yet planted

        # --- Milestone Check ---
        schedule = MILESTONE_SCHEDULE.get(plant.crop_name, _DEFAULT_MILESTONES)
        for milestone in schedule:
            target_day = milestone["day"]
            # ±2 day window
            if abs(days_active - target_day) <= 2:
                # Check if alert already exists for this milestone today
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

                    # Telegram notification
                        
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
                        await send_telegram_alert(target_chat, tg_text)
                break  # Only trigger one milestone per evaluation

        # --- Weather Risk Check ---
        weather_risk = await fetch_weather_risk(plant.latitude, plant.longitude)

        if weather_risk["heavy_rain"] or weather_risk["extreme_heat"]:
            # Build weather alert
            severity = "critical" if (weather_risk["heavy_rain"] and weather_risk["extreme_heat"]) else "warning"

            if weather_risk["heavy_rain"]:
                action = (
                    "Move pots under shelter or cover with breathable cloth. "
                    "Ensure drainage holes are clear. Check for waterlogged soil."
                )
            else:
                action = (
                    "Move to partial shade during 11AM-3PM. Water deeply in early morning. "
                    "Apply mulch to reduce soil temperature."
                )

            message = f"⚠️ Weather alert for {plant.crop_name}: {weather_risk['description']}"

            # Check if this weather alert already exists for today
            existing = (
                db.query(PlantAlert)
                .filter(
                    PlantAlert.plant_id == plant.id,
                    PlantAlert.alert_type == "weather",
                    PlantAlert.triggered_on == today,
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

                # Telegram notification
                target_chat = plant.telegram_chat_id or getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None)
                if target_chat:
                    loc_name = plant.location_name or "Home Garden"
                    map_url = f"https://www.google.com/maps?q={plant.latitude},{plant.longitude}"
                    
                    tg_text = (
                        f"⚠️ <b>Weather Alert</b> — {plant.crop_name}\n"
                        f"<a href='{map_url}'>📍 {loc_name}</a>\n\n"
                        f"{weather_risk['description']}\n"
                        f"👉 {action}"
                    )
                    await send_telegram_alert(target_chat, tg_text)

    db.commit()
    logger.info("[Garden Monitor] Evaluation complete. %d new alert(s) generated.", new_alert_count)
    return new_alert_count
