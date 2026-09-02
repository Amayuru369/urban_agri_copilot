from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app.core.database import get_db
from backend.app.models.schemas import Crop, CropOut

router = APIRouter(prefix="/crops", tags=["crops"])


@router.get("", response_model=list[CropOut])
def list_crops(category: str | None = Query(None), db: Session = Depends(get_db)):
    """List all supported crops. Optionally filter by category."""
    query = db.query(Crop)
    if category:
        query = query.filter(Crop.category == category)
    return query.all()


@router.get("/{crop_id}", response_model=CropOut)
def get_crop(crop_id: int, db: Session = Depends(get_db)):
    """Return detailed growth rules for a single crop."""
    crop = db.query(Crop).filter(Crop.id == crop_id).first()
    if not crop:
        raise HTTPException(status_code=404, detail="Crop not found")
    return crop
