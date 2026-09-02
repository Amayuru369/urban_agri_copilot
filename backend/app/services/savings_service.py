from sqlalchemy.orm import Session

from backend.app.models.schemas import MarketPrice


def calculate_household_savings(
    crop_name: str,
    expected_yield_kg: float,
    db: Session,
) -> dict:
    """Estimate household grocery savings for growing a crop at current retail prices.

    Returns a dict with crop name, expected yield, retail price per kg, currency,
    estimated total savings, and an info note when no market price is available.
    """
    price = (
        db.query(MarketPrice)
        .filter(MarketPrice.crop_name.ilike(crop_name.strip()))
        .first()
    )

    if not price:
        return {
            "crop_name": crop_name,
            "expected_yield_kg": expected_yield_kg,
            "retail_price_per_kg": None,
            "currency": "LKR",
            "estimated_savings": None,
            "note": f"No retail price found for '{crop_name}'.",
        }

    estimated_savings = round(price.retail_price_per_kg * expected_yield_kg, 2)
    return {
        "crop_name": price.crop_name,
        "expected_yield_kg": expected_yield_kg,
        "retail_price_per_kg": price.retail_price_per_kg,
        "currency": price.currency,
        "estimated_savings": estimated_savings,
        "note": "",
    }
