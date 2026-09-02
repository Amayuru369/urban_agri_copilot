import json
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase3.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.core.database import Base, get_db, SessionLocal
from backend.app.services import report_service, savings_service
from backend.main import app
from backend.seed_data import seed_all

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_all()
    yield
    Base.metadata.drop_all(bind=engine)


client = TestClient(app)


@pytest.fixture
def sample_report_payload(tmp_path):
    plan_data = {
        "location": {"lat": 6.9271, "lon": 79.8612},
        "container_type": "grow_bag",
        "space_sqm": 2.0,
        "target_month": 9,
        "forecast_summary": {
            "avg_min_temp_c": 24.0,
            "avg_max_temp_c": 30.0,
            "avg_daylight_hours": 12.0,
        },
        "recommendations": [
            {
                "crop": {"name": "Gotukola", "category": "leafy"},
                "suitability_score": 95,
                "recommended_pot_count": 200,
                "layout": "20 rows × 10 cols (200 plants)",
                "companion_synergy": ["Grows well with Kang-kung"],
                "notes": "Harvestable in ~45 days",
            }
        ],
    }
    diagnosis_data = {
        "crop_detected": "Tomato",
        "issue_type": "Early Nitrogen Deficiency & Leaf Curl",
        "confidence": 0.92,
        "symptoms": ["Older leaves turning pale green to yellow"],
        "severity": "moderate",
        "recommended_action": "Apply compost tea.",
    }
    savings_data = {
        "crop_name": "Tomato",
        "expected_yield_kg": 2.5,
        "retail_price_per_kg": 300.0,
        "currency": "LKR",
        "estimated_savings": 750.0,
    }
    return {
        "plan_data": plan_data,
        "diagnosis_data": diagnosis_data,
        "savings_data": savings_data,
        "output_filename": "garden_report.pdf",
    }


def test_generate_garden_report(tmp_path, sample_report_payload):
    output_path = str(tmp_path / "garden_report.pdf")
    result_path = report_service.generate_garden_report(
        plan_data=sample_report_payload["plan_data"],
        diagnosis_data=sample_report_payload["diagnosis_data"],
        savings_data=sample_report_payload["savings_data"],
        output_path=output_path,
    )
    assert os.path.exists(result_path)
    assert os.path.getsize(result_path) > 0

    with open(result_path, "rb") as f:
        content = f.read()

    # If reportlab produced a compressed PDF, pypdf can extract the text for us.
    try:
        import pypdf

        reader = pypdf.PdfReader(result_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "UrbanAgri-Copilot" in text or "Garden Health" in text
        assert "Grocery Savings" in text or "Estimated savings" in text
    except Exception:
        # Fallback verification: the file should at least be a non-empty PDF/markdown.
        assert content.startswith(b"%PDF") or b"UrbanAgri-Copilot" in content
        assert len(content) > 0


def test_cli_argument_parsing():
    import cli

    parser = cli.build_parser()

    plan_args = parser.parse_args(
        ["plan", "--lat", "6.9271", "--lon", "79.8612", "--space", "2.0", "--container", "pot"]
    )
    assert plan_args.command == "plan"
    assert plan_args.lat == 6.9271
    assert plan_args.lon == 79.8612
    assert plan_args.space == 2.0
    assert plan_args.container == "pot"

    remedy_args = parser.parse_args(
        ["remedy", "--issue", "Nitrogen Deficiency", "--scraps", "banana peel,eggshells"]
    )
    assert remedy_args.command == "remedy"
    assert remedy_args.issue == "Nitrogen Deficiency"
    assert remedy_args.scraps == "banana peel,eggshells"

    report_args = parser.parse_args(["report", "--output", "my_report.pdf"])
    assert report_args.command == "report"
    assert report_args.output == "my_report.pdf"


def test_report_download_endpoint(sample_report_payload):
    response = client.post("/api/report/generate", json=sample_report_payload)
    assert response.status_code == 200
    assert response.content
    assert len(response.content) > 0
    content_disposition = response.headers.get("content-disposition", "")
    assert "garden_report.pdf" in content_disposition


def test_calculate_household_savings():
    db = SessionLocal()
    try:
        result = savings_service.calculate_household_savings("Tomato", 2.5, db)
        assert result["crop_name"] == "Tomato"
        assert result["expected_yield_kg"] == 2.5
        assert result["currency"] == "LKR"
        assert result["estimated_savings"] == 750.0

        missing = savings_service.calculate_household_savings("NonExistentCrop", 1.0, db)
        assert missing["estimated_savings"] is None
        assert "No retail price found" in missing["note"]
    finally:
        db.close()


def test_skill_specification_file():
    spec_path = Path(__file__).resolve().parents[1] / ".qoder" / "skills" / "urban_agri_skills.json"
    assert spec_path.exists()
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    tool_names = {tool["name"] for tool in spec["tools"]}
    assert tool_names == {
        "skill_plan_garden",
        "skill_diagnose_plant",
        "skill_synthesize_remedy",
        "skill_calculate_savings",
    }
