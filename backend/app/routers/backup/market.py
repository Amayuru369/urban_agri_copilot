from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models.schemas import MarketPriceOut
from backend.app.services.market_service import get_prices_with_margins

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/prices", response_model=list[MarketPriceOut])
def list_market_prices(db: Session = Depends(get_db)):
    """List crop prices and calculate retail-to-wholesale margins."""
    return get_prices_with_margins(db)
