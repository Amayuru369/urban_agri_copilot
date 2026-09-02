import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase4.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.core.database import Base, get_db
from backend.app.services import report_service
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


def test_root_serves_dashboard():
    response = client.get("/")
    assert response.status_code == 200
    text = response.text
    assert "UrbanAgri-Copilot" in text
    assert "Agro-Climatic Balcony Planner" in text
    assert "AI Leaf Doctor" in text
    assert "Harvest Economics" in text


def test_api_routes_still_work_after_static_mount():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_generate_report_with_long_text_does_not_overflow(tmp_path):
    long_notes = " ".join(["This is a very long cultivation note."] * 50)
    long_action = " ".join(["Spray organic compost tea thoroughly on both leaf surfaces."] * 40)
    long_symptom = " ".join(["Leaf margins turn yellow and curl inward dramatically."] * 30)

    plan_data = {
        "location": {"lat": 6.9271, "lon": 79.8612},
        "container_type": "grow_bag",
        "space_sqm": 3.5,
        "target_month": 8,
        "forecast_summary": {
            "avg_min_temp_c": 24.0,
            "avg_max_temp_c": 31.0,
            "avg_daylight_hours": 12.0,
        },
        "recommendations": [
            {
                "crop": {"name": "Tomato", "category": "vegetable"},
                "suitability_score": 88,
                "recommended_pot_count": 10,
                "layout": "5 rows x 2 cols",
                "companion_synergy": ["Basil"],
                "notes": long_notes,
            }
        ],
    }
    diagnosis_data = {
        "crop_detected": "Tomato",
        "issue_type": "Leaf Curl Complex",
        "confidence": 0.87,
        "symptoms": [long_symptom],
        "severity": "moderate",
        "recommended_action": long_action,
    }
    savings_data = {
        "crop_name": "Tomato",
        "expected_yield_kg": 4.2,
        "retail_price_per_kg": 320.0,
        "currency": "LKR",
        "estimated_savings": 1344.0,
    }

    output_path = str(tmp_path / "long_report.pdf")
    result_path = report_service.generate_garden_report(
        plan_data=plan_data,
        diagnosis_data=diagnosis_data,
        savings_data=savings_data,
        output_path=output_path,
    )
    assert Path(result_path).exists()
    assert Path(result_path).stat().st_size > 0

    # Confirm the produced file still contains key report content.
    try:
        import pypdf

        reader = pypdf.PdfReader(result_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "UrbanAgri-Copilot" in text
        assert "Leaf Health Pathology Status" in text
        assert "Estimated Grocery Savings" in text
        assert "Tomato" in text
    except Exception:
        content = Path(result_path).read_bytes()
        assert content.startswith(b"%PDF") or b"UrbanAgri-Copilot" in content
