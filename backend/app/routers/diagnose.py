from fastapi import APIRouter, File, Query, UploadFile

from backend.app.models.schemas import DiagnosisResponse
from backend.app.services import diagnose_service

router = APIRouter(prefix="/diagnose", tags=["diagnose"])


@router.post("/upload", response_model=DiagnosisResponse)
async def diagnose_upload(
    file: UploadFile = File(...),
    crop_name: str | None = Query(None),
    mock: bool = Query(False),
):
    """Upload a plant photo and receive an AI-generated diagnosis and AI-formulated organic remedy."""
    image_bytes = await file.read()
    diagnosis, linked_remedy = await diagnose_service.diagnose_plant_image(
        image_bytes,
        crop_name=crop_name,
        use_mock=mock,
    )
    return {"diagnosis": diagnosis, "linked_remedy": linked_remedy}