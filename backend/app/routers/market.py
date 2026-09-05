from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models.schemas import MarketPrice, MarketPriceOut
from backend.app.services.market_service import get_prices_with_margins
from backend.app.services.price_lookup_service import lookup_market_price

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/prices", response_model=list[MarketPriceOut])
def list_market_prices(db: Session = Depends(get_db)):
    """List crop prices and calculate retail-to-wholesale margins."""
    return get_prices_with_margins(db)


@router.get("/prices/lookup")
def lookup_price(crop_name: str = Query(..., min_length=1)):
    """Best-effort live web search for a crop's current retail price.

    Read-only — never touches the database. Returns candidate prices with
    their source so the caller can review before applying via
    PATCH /market/prices/{crop_name}.
    """
    return lookup_market_price(crop_name)


class MarketPriceApply(BaseModel):
    retail_price_per_kg: float = Field(..., gt=0)
    wholesale_price_per_kg: float | None = Field(
        default=None,
        gt=0,
        description="Optional. If omitted on a brand-new crop, defaults to the retail price as a rough placeholder.",
    )


@router.patch("/prices/{crop_name}")
def apply_market_price(crop_name: str, payload: MarketPriceApply, db: Session = Depends(get_db)):
    """Apply a reviewed price (e.g. from /prices/lookup) to the stored MarketPrice row.

    Updates the existing row for this crop if one exists. If none exists yet,
    creates one — wholesale_price_per_kg is required by the schema and a live
    search rarely surfaces it, so it defaults to the retail price when not
    explicitly supplied (correct it manually afterwards if you have a real
    wholesale figure).
    """
    price = (
        db.query(MarketPrice)
        .filter(MarketPrice.crop_name.ilike(crop_name.strip()))
        .first()
    )
    if price:
        price.retail_price_per_kg = payload.retail_price_per_kg
        if payload.wholesale_price_per_kg is not None:
            price.wholesale_price_per_kg = payload.wholesale_price_per_kg
    else:
        price = MarketPrice(
            crop_name=crop_name.strip(),
            retail_price_per_kg=payload.retail_price_per_kg,
            wholesale_price_per_kg=payload.wholesale_price_per_kg or payload.retail_price_per_kg,
            currency="LKR",
        )
        db.add(price)

    db.commit()
    db.refresh(price)

    return {
        "id": price.id,
        "crop_name": price.crop_name,
        "retail_price_per_kg": price.retail_price_per_kg,
        "wholesale_price_per_kg": price.wholesale_price_per_kg,
        "currency": price.currency,
        "message": "Price updated.",
    }
