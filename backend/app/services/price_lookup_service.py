"""Best-effort live market price lookup via Google's Custom Search JSON API.

This is a *suggestion* engine, not a source of truth: it searches the web,
tries to extract a plausible LKR/kg price from the top results, and returns
candidates with their source so a human can review before applying. It never
writes to the database itself — see the PATCH /market/prices/{crop_name}
endpoint in market.py for that.

Setup (free tier — 100 queries/day):
  1. Get an API key from https://console.cloud.google.com (enable
     "Custom Search API").
  2. Create a search engine at https://programmablesearchengine.google.com,
     set it to search the entire web, and copy its Search Engine ID.
  3. Set two environment variables before starting the backend:
       GOOGLE_CSE_API_KEY=<your api key>
       GOOGLE_CSE_CX=<your search engine id>

Without those two variables set, lookup_market_price() returns a clear
"not configured" note instead of failing — nothing else in the app depends
on this, so leaving it unconfigured doesn't break anything else.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests

GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

# Matches "Rs. 250/kg", "LKR 250 per kg", "Rs 250.50/Kg", etc.
_PRICE_PATTERN = re.compile(
    r"(?:Rs\.?|LKR)\s?([\d,]+(?:\.\d+)?)\s?(?:/|\s*per\s*)\s?kg",
    re.IGNORECASE,
)


def _extract_price(text: str) -> float | None:
    match = _PRICE_PATTERN.search(text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def lookup_market_price(crop_name: str) -> dict[str, Any]:
    """Search the web for a current retail price (LKR/kg) for a crop.

    Returns:
        {
          "candidates": [{"price": float, "source_title": str, "source_url": str}, ...],
          "best_guess": float | None,   # median of found candidates
          "note": str,                  # human-readable status/explanation
        }
    """
    api_key = os.environ.get("GOOGLE_CSE_API_KEY")
    cx = os.environ.get("GOOGLE_CSE_CX")
    if not api_key or not cx:
        return {
            "candidates": [],
            "best_guess": None,
            "note": "Live price lookup isn't configured yet (missing GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX environment variables).",
        }

    query = f"{crop_name} retail price per kg Sri Lanka today"
    try:
        resp = requests.get(
            GOOGLE_CSE_ENDPOINT,
            params={"key": api_key, "cx": cx, "q": query, "num": 5},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return {
            "candidates": [],
            "best_guess": None,
            "note": f"Price search failed: {exc}",
        }

    candidates: list[dict[str, Any]] = []
    for item in data.get("items", []):
        snippet = f"{item.get('title', '')} {item.get('snippet', '')}"
        price = _extract_price(snippet)
        if price is not None:
            candidates.append({
                "price": price,
                "source_title": item.get("title"),
                "source_url": item.get("link"),
            })

    if not candidates:
        return {
            "candidates": [],
            "best_guess": None,
            "note": f"No clear price found in the top search results for '{crop_name}'. Try checking manually.",
        }

    prices = sorted(c["price"] for c in candidates)
    mid = len(prices) // 2
    best_guess = prices[mid] if len(prices) % 2 == 1 else round((prices[mid - 1] + prices[mid]) / 2, 2)

    return {
        "candidates": candidates,
        "best_guess": best_guess,
        "note": "",
    }
