"""Garden Assistant Chat Service.

Context-aware chatbot powered by Google Gemini that understands
the user's live garden state (active plants, unresolved alerts).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date

import httpx
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.models.garden import PlantAlert, TrackedPlant

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "gemini-3.6-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_gemini_keys() -> list[str]:
    """Return all configured Gemini API keys, supporting comma-separated GEMINI_API_KEYS."""
    raw = (
        os.getenv("GEMINI_API_KEYS")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or getattr(settings, "GEMINI_API_KEY", None)
    )
    if not raw or raw.strip().lower() in {"none", "null", "your_token_here", ""}:
        return []
    return [k.strip().strip("'\"") for k in raw.split(",") if k.strip()]


def _build_garden_context(db: Session, scope_user_id: int | None = None) -> str:
    """Build a compact text summary of the current garden state.

    Role-based scoping:
    - ``scope_user_id is None`` (admin / global overview) → all active plants
      and all unresolved alerts across the system.
    - ``scope_user_id`` set (secondary grower) → ONLY that user's active plants,
      and ONLY unresolved alerts belonging to plants they own (via a join on
      TrackedPlant). No global data ever leaks into a grower's context.
    """
    today = date.today()

    plant_query = db.query(TrackedPlant).filter(TrackedPlant.active == True)  # noqa: E712
    if scope_user_id is not None:
        plant_query = plant_query.filter(TrackedPlant.user_id == scope_user_id)
    plants = plant_query.all()

    alert_query = db.query(PlantAlert).filter(PlantAlert.resolved == False)  # noqa: E712
    if scope_user_id is not None:
        # Join to TrackedPlant so alerts are restricted to this grower's plants.
        alert_query = alert_query.join(
            TrackedPlant, PlantAlert.plant_id == TrackedPlant.id
        ).filter(TrackedPlant.user_id == scope_user_id)
    alerts = alert_query.all()

    lines: list[str] = []

    if plants:
        lines.append(f"Active Plants ({len(plants)}):")
        for p in plants:
            days = (today - p.planted_date).days
            loc = p.location_name or "Home Garden"
            lines.append(f"  - {p.crop_name} | Day {days} | Location: {loc}")
    else:
        lines.append("Active Plants: None registered yet.")

    if alerts:
        lines.append(f"\nUnresolved Alerts ({len(alerts)}):")
        for a in alerts:
            plant = db.query(TrackedPlant).filter(TrackedPlant.id == a.plant_id).first()
            crop = plant.crop_name if plant else "Unknown"
            lines.append(
                f"  - [{a.alert_type} | {a.severity}] {crop}: {a.message}"
            )
            if a.action_required:
                lines.append(f"    Action: {a.action_required}")
    else:
        lines.append("\nUnresolved Alerts: None. All crops healthy.")

    return "\n".join(lines)


async def _call_openrouter_fallback(
    system_instruction: str, user_message: str, history: list[dict]
) -> str | None:
    """Fallback call to OpenRouter's active free models if Gemini fails."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    fallback_instruction = (
        f"{system_instruction}\n\n"
        "IMPORTANT: You are online and operational. Do not apologize or state that the AI service is offline. "
        "Answer the user's question directly and concisely using the provided live garden context."
    )
    messages = [{"role": "system", "content": fallback_instruction}]
    for turn in history[-10:]:
        role = "user" if turn.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": turn.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "UrbanAgri-Copilot",
        "Content-Type": "application/json",
    }

    
   # OpenRouter dynamic free router and active free community slugs
    candidate_models = [
        "openrouter/auto",
        "deepseek/deepseek-chat:free",
        "google/gemma-2-9b-it:free",
        "mistralai/mistral-small-24b-instruct-2501:free",
        "qwen/qwen-2.5-72b-instruct:free",
    ]

    for model_name in candidate_models:
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": 512,
            "temperature": 0.7,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices", [])
                    if choices:
                        msg_obj = choices[0].get("message") or {}
                        raw_content = msg_obj.get("content")
                        if isinstance(raw_content, str) and raw_content.strip():
                            logger.info("[Chat Service] Fallback answered by OpenRouter model: %s", model_name)
                            return raw_content.strip()

                logger.warning(
                    "[Chat Service] OpenRouter %s returned HTTP %s: %s",
                    model_name,
                    res.status_code,
                    res.text[:120],
                )
        except Exception as exc:
            logger.warning("[Chat Service] OpenRouter %s failed: %s", model_name, repr(exc))

    return None


FALLBACK_REPLY = (
    "I'm having trouble connecting to the AI service right now. "
    "Please check your Garden Dashboard for active alerts and milestones — "
    "I'll be back online shortly! 🌿"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def ask_garden_copilot(
    user_message: str,
    history: list[dict],
    db: Session,
    scope_user_id: int | None = None,
) -> str:
    """Generate a context-aware reply using Gemini with live garden state.

    ``scope_user_id`` enforces role-based data isolation: when set (a secondary
    grower), only that user's plants/alerts are injected into the model context.
    When ``None`` (admin), the global overview is used.
    """
    garden_context = _build_garden_context(db, scope_user_id=scope_user_id)

    system_instruction = (
        "You are UrbanAgri-Copilot, an expert agronomist and practical urban home-gardening assistant.\n"
        "Use the live garden context below to provide direct, practical advice. "
        "If the user asks about crop issues or household scraps, recommend zero-chemical organic kitchen remedies. "
        "Keep replies actionable, supportive, and under 3-4 sentences.\n\n"
        "STRICT DATA GUARDRAIL: Only answer questions using the plants, alerts, and data "
        "explicitly provided in the user's garden context below. If the user asks about alerts "
        "or plants not present in this context, state that they have none. Never invent, infer, "
        "or reference plants, alerts, or garden data that are not listed here.\n\n"
        f"Live Garden Context:\n{garden_context}"
    )

    contents: list[dict] = []
    for turn in history[-10:]:
        role = "user" if turn.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": turn.get("content", "")}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 512,
        },
    }

    keys = _get_gemini_keys()
    if not keys:
        logger.warning("[Chat Service] No Gemini API keys configured; checking fallback.")
        openrouter_reply = await _call_openrouter_fallback(system_instruction, user_message, history)
        if openrouter_reply:
            return openrouter_reply
        return FALLBACK_REPLY

    last_error: Exception | None = None

    for api_key in keys:
        url = f"{GEMINI_BASE_URL}/{MODEL}:generateContent?key={api_key}"

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(url, json=payload, headers={"Content-Type": "application/json"})

                    if res.status_code == 503:
                        logger.warning(
                            "[Chat Service] 503 high demand spike for key ending ...%s (attempt %s/2). Waiting 1.5s...",
                            api_key[-4:], attempt + 1
                        )
                        last_error = Exception("HTTP 503 Capacity Spike")
                        await asyncio.sleep(1.5)
                        continue

                    if res.status_code == 429:
                        logger.warning("[Chat Service] Rate-limited for key ending ...%s; switching key", api_key[-4:])
                        last_error = Exception("HTTP 429 Rate Limited")
                        break

                    if res.status_code != 200:
                        logger.warning("[Chat Service] HTTP %s: %s", res.status_code, res.text[:300])
                        last_error = Exception(f"HTTP {res.status_code}")
                        break

                    data = res.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            reply_text = parts[0].get("text", "").strip()
                            if reply_text:
                                return reply_text

                    last_error = Exception("Empty response from Gemini")
                    break

            except Exception as exc:
                logger.warning("[Chat Service] Request exception for key ...%s: %s", api_key[-4:], repr(exc))
                last_error = exc
                break

    # If all Gemini keys failed, automatically invoke OpenRouter
    logger.warning("[Chat Service] All Gemini keys exhausted. Attempting OpenRouter fallback...")
    openrouter_reply = await _call_openrouter_fallback(system_instruction, user_message, history)
    if openrouter_reply:
        return openrouter_reply

    logger.error("[Chat Service] All providers exhausted. Last error: %s", last_error)
    return FALLBACK_REPLY