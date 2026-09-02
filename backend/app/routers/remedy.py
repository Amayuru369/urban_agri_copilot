from fastapi import APIRouter

from backend.app.models.schemas import RemedyRecipe, RemedyRequest
from backend.app.services import remedy_service

router = APIRouter(prefix="/remedy", tags=["remedy"])


@router.post("/generate", response_model=RemedyRecipe)
def generate_remedy(payload: RemedyRequest):
    """Generate a zero-chemical remedy recipe for a plant issue."""
    return remedy_service.synthesize_organic_remedy(
        payload.issue_type,
        payload.available_scraps,
    )
