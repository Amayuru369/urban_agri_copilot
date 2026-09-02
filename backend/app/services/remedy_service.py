from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv

from backend.app.core.config import settings

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_json_block(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


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


def _normalize_list(value: Any, default: list[str]) -> list[str]:
    """Ensure output is always a list of non-empty strings."""
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
        return items if items else default
    elif isinstance(value, str) and value.strip():
        parts = [s.strip() for s in value.split(".") if s.strip()]
        return parts if parts else [value.strip()]
    return default


def _clean_step(step: str) -> str:
    """Remove leading numbering like '1. ' from a preparation step."""
    step = step.strip()
    step = re.sub(r"^\d+[\.\)\-]\s*", "", step)
    return step


def _match_scraps(ingredients: list[str], available_scraps: list[str] | None) -> list[str]:
    """Return the subset of available scraps that appear in any ingredient."""
    if not available_scraps:
        return []
    matched = []
    for scrap in available_scraps:
        scrap_lower = scrap.lower()
        for ingredient in ingredients:
            if scrap_lower in ingredient.lower():
                matched.append(scrap)
                break
    return matched


# ---------------------------------------------------------------------------
# Fallback recipes
# ---------------------------------------------------------------------------

def _fallback_remedy(issue_type: str) -> dict[str, Any]:
    issue = issue_type or "General Plant Stress"
    return {
        "issue_type": issue,
        "remedy_name": f"Organic Botanical Treatment for {issue.title()}",
        "ingredients": [
            "1 litre lukewarm water",
            "1 tsp mild liquid soap",
            "1 tsp cold-pressed neem oil",
        ],
        "preparation_steps": [
            "Mix soap into water until emulsified",
            "Add neem oil and shake thoroughly",
            "Spray foliage during early morning or late evening",
        ],
        "application_schedule": "Apply every 5 to 7 days.",
        "safety_notes": [
            "Avoid spraying in direct sunlight.",
            "Test on a single leaf first.",
        ],
        "matched_scraps": [],
    }


def _nitrus_boost_remedy(issue_type: str, matched_scraps: list[str]) -> dict[str, Any]:
    """Deterministic nitrogen-deficiency recipe using common kitchen scraps."""
    return {
        "issue_type": issue_type,
        "remedy_name": "Organic Nitro-Boost Coffee & Banana Soil Drench",
        "ingredients": [
            "1 cup spent coffee grounds (dried)",
            "3 chopped banana peels",
            "1 gallon unchlorinated water (rainwater or aerated tap water)",
        ],
        "preparation_steps": [
            "Chop the banana peels into small 1/2-inch pieces and place them into a clean bucket along with the coffee grounds",
            "Pour 1 gallon of unchlorinated water over the ingredients, stir well, and cover the container loosely with a breathable cloth",
            "Steep the mixture at room temperature for 48 to 72 hours, stirring once per day to oxygenate the tea",
            "Strain the liquid through cheesecloth or a fine mesh sieve to remove all solid residues before use",
        ],
        "application_schedule": "Apply 1 to 2 cups of the strained liquid to the soil at the root zone of affected plants every 14 days, preferably early in the morning.",
        "safety_notes": [
            "Avoid applying un-strained grounds directly in thick layers on the soil surface, as they can compact and restrict water infiltration",
            "Do not allow the liquid mixture to ferment past 4 days to prevent the growth of anaerobic, bad bacteria",
            "Monitor acid-sensitive plants, as coffee grounds can slightly lower soil pH over long-term, repeated applications",
        ],
        "matched_scraps": matched_scraps,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def synthesize_organic_remedy(
    issue_type: str,
    available_scraps: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a zero-chemical organic kitchen remedy recipe for a plant issue.

    Returns a dict matching the RemedyRecipe schema:
      issue_type, remedy_name, ingredients, preparation_steps,
      application_schedule, safety_notes, matched_scraps.
    """
    issue = (issue_type or "General Plant Stress").strip()
    scraps = available_scraps or []

    # Use a deterministic, scrap-aware recipe for nitrogen deficiency.
    if "nitrogen" in issue.lower():
        matched = _match_scraps(
            ["coffee grounds", "banana peels", "eggshells", "compost tea"],
            scraps,
        )
        return _nitrus_boost_remedy(issue, matched)

    keys = _get_gemini_keys()
    if keys and issue:
        prompt_text = (
            "You are an organic agriculture scientist and master gardener. "
            f"Formulate a zero-chemical, natural organic kitchen remedy for: '{issue}'. "
            f"Context details: Urban home garden application.\n\n"
            "Return ONLY a valid JSON object matching this schema:\n"
            "{\n"
            '  "title": "string (creative, descriptive recipe name)",\n'
            '  "ingredients": ["string (exact quantity and item)", "string"],\n'
            '  "preparation": ["string (step 1)", "string (step 2)", "string (step 3)"],\n'
            '  "application_schedule": "string (clear interval and best time of day)",\n'
            '  "safety_notes": ["string (safety note 1)", "string (safety note 2)"]\n'
            "}"
        )

        headers = {
            "Content-Type": "application/json",
        }
        payload = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
            },
        }

        last_error: httpx.Response | None = None
        for api_key in keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={api_key}"
            try:
                with httpx.Client(timeout=15.0) as client:
                    res = client.post(url, headers=headers, json=payload)
                    if res.status_code == 429:
                        logger.warning("Gemini remedy rate-limited for key ending ...%s", api_key[-4:])
                        last_error = res
                        continue
                    if res.status_code != 200:
                        logger.warning("Gemini remedy HTTP error [%s]: %s", res.status_code, res.text)
                        return _fallback_remedy(issue)

                    data = res.json()
                    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(_clean_json_block(raw_text))

                    ingredients = _normalize_list(
                        parsed.get("ingredients"),
                        ["1 litre clean water", "1 tsp mild soap"],
                    )
                    preparation = _normalize_list(
                        parsed.get("preparation") or parsed.get("preparation_steps"),
                        ["Mix thoroughly", "Spray foliage"],
                    )

                    recipe = {
                        "issue_type": issue,
                        "remedy_name": parsed.get("title") or parsed.get("remedy_name") or f"Organic Recipe for {issue}",
                        "ingredients": ingredients,
                        "preparation_steps": [_clean_step(step) for step in preparation],
                        "application_schedule": str(parsed.get("application_schedule", "Apply every 5-7 days in the early morning.")),
                        "safety_notes": _normalize_list(
                            parsed.get("safety_notes"),
                            ["Avoid applying in direct sunlight.", "Test on a single leaf first."],
                        ),
                        "matched_scraps": _match_scraps(ingredients, scraps),
                    }
                    return recipe
            except Exception as exc:
                logger.warning("Gemini remedy generation caught exception: %s", repr(exc))
                return _fallback_remedy(issue)

        if last_error:
            logger.warning("All Gemini keys exhausted. Last error [%s]: %s", last_error.status_code, last_error.text)

    return _fallback_remedy(issue)
