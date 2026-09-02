from sqlalchemy.orm import Session

from backend.app.models.schemas import MarketPrice, MarketPriceOut


def get_prices_with_margins(db: Session) -> list[MarketPriceOut]:
    """Return all market prices enriched with retail-to-wholesale margins."""
    prices = db.query(MarketPrice).all()
    results = []
    for price in prices:
        margin = price.retail_price_per_kg - price.wholesale_price_per_kg
        margin_percent = (
            (margin / price.wholesale_price_per_kg) * 100
            if price.wholesale_price_per_kg
            else 0.0
        )
        results.append(
            MarketPriceOut(
                id=price.id,
                crop_name=price.crop_name,
                retail_price_per_kg=price.retail_price_per_kg,
                wholesale_price_per_kg=price.wholesale_price_per_kg,
                currency=price.currency,
                last_updated=price.last_updated,
                margin_per_kg=margin,
                margin_percent=round(margin_percent, 2),
            )
        )
    return results
