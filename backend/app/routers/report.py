import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import FileResponse

from backend.app.models.schemas import ReportGenerateRequest
from backend.app.services import report_service

router = APIRouter(prefix="/report", tags=["report"])


@router.post("/generate")
async def generate_report(
    payload: ReportGenerateRequest,
    background_tasks: BackgroundTasks,
):
    """Generate and download a Garden Health & Action Card report."""
    filename = payload.output_filename or "garden_report.pdf"
    suffix = Path(filename).suffix or ".pdf"

    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name

    output_path = report_service.generate_garden_report(
        plan_data=payload.plan_data,
        diagnosis_data=payload.diagnosis_data,
        savings_data=payload.savings_data,
        output_path=tmp_path,
    )

    # Schedule cleanup after the response is sent.
    background_tasks.add_task(os.remove, output_path)

    media_type = "application/pdf" if suffix.lower() == ".pdf" else "text/markdown"
    return FileResponse(
        output_path,
        media_type=media_type,
        filename=filename,
    )
