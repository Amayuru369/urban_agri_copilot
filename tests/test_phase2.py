import io
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase2.db")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.core.database import Base, get_db
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


def test_planner_recommend(monkeypatch):
    _fake_forecast = [
        {
            "date": "2026-08-28",
            "min_temp_c": 24.0,
            "max_temp_c": 30.0,
            "daylight_duration_seconds": 43200.0,
            "avg_rh_percent": 78.5,
            "precipitation_mm": 3.2,
        }
        for _ in range(7)
    ]

    async def fake_fetch_microclimate(lat: float, lon: float):
        return {
            "latitude": lat,
            "longitude": lon,
            "current": {"temperature_c": 28.0, "relative_humidity_percent": 75.0},
            "forecast": _fake_forecast,
        }

    async def fake_fetch_climate_normals(lat: float, lon: float, target_month: int):
        return {
            "latitude": lat,
            "longitude": lon,
            "current": {},
            "forecast": _fake_forecast,
        }

    monkeypatch.setattr(
        "backend.app.services.planner_service.weather_service.fetch_microclimate",
        fake_fetch_microclimate,
    )
    monkeypatch.setattr(
        "backend.app.services.planner_service.weather_service.fetch_climate_normals",
        fake_fetch_climate_normals,
    )

    payload = {
        "lat": 6.9271,
        "lon": 79.8612,
        "container_type": "grow_bag",
        "space_sqm": 2.0,
        "target_month": 9,
    }
    response = client.post("/api/planner/recommend", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["container_type"] == "grow_bag"
    assert data["space_sqm"] == 2.0
    assert "forecast_summary" in data
    assert "is_current_month" in data
    # Weekly forecast should carry precipitation per day
    wf = data["weekly_forecast"]
    assert len(wf) == 7
    assert all("precipitation_mm" in d for d in wf)
    recommendations = data["recommendations"]
    assert len(recommendations) >= 6
    top = recommendations[0]
    assert 0 <= top["suitability_score"] <= 100
    assert top["recommended_pot_count"] >= 1
    assert "layout" in top
    assert "crop" in top


def test_planner_timeline_known_crop():
    """Timeline endpoint should resolve harvest_days from the DB for a seeded crop."""
    payload = {"crop_name": "Tomato", "target_month": 9, "lat": 6.9271}
    response = client.post("/api/planner/timeline", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["crop_name"] == "Tomato"
    assert data["harvest_days"] > 0
    assert data["total_weeks"] >= 2
    assert len(data["weeks"]) == data["total_weeks"]
    first = data["weeks"][0]
    assert "phase" in first
    assert "phase_emoji" in first
    assert "day_range" in first
    # RH should be present and within valid range
    assert "relative_humidity_pct" in first
    assert 30 <= first["relative_humidity_pct"] <= 98
    # Colombo (lat ~6.9°N, tropical) → fallback RH ~80 ± 1
    for w in data["weeks"]:
        assert "relative_humidity_pct" in w
        assert isinstance(w["relative_humidity_pct"], int)
        assert abs(w["relative_humidity_pct"] - 80) <= 1
        # Every week should carry an actionable care tip
        assert "action_tip" in w
        assert len(w["action_tip"]) > 10
    # No week should fall back to the generic "Monitor plant health" message
    generic = "Monitor plant health daily"
    tips = [w["action_tip"] for w in data["weeks"]]
    assert not any(generic in t for t in tips), "All tips should be phase-specific, not generic fallback"
    # Multiple phases should produce distinct tips
    assert len(set(tips)) >= 2, "Different growth phases should yield different care tips"
    # Each week should carry precipitation_mm and a dynamic rainfall label
    for w in data["weeks"]:
        assert "precipitation_mm" in w
        assert isinstance(w["precipitation_mm"], (int, float))
        # Without API precip data, all weeks fallback to 0.0 → "Sunny / Dry Spells"
        assert w["rainfall_pattern"] in ("Sunny / Dry Spells", "Scattered Showers",
                                          "Moderate Showers", "NE Monsoon — Moderate Rain",
                                          "SW Monsoon — Moderate Rain", "SW Monsoon — Heavy Rain",
                                          "NE Monsoon — Light Rain", "SW Monsoon — Light to Moderate",
                                          "NE Inter-monsoonal Showers", "Dry / High Sunshine",
                                          "Moderate Inter-monsoonal Showers", "Intermittent Showers")


def test_planner_timeline_custom_crop():
    """Timeline endpoint should accept explicit harvest_days for an unknown crop."""
    payload = {"crop_name": "Dragon Fruit", "harvest_days": 180, "target_month": 3, "lat": 6.9271}
    response = client.post("/api/planner/timeline", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["crop_name"] == "Dragon Fruit"
    assert data["harvest_days"] == 180
    assert data["total_weeks"] >= 25  # ceil(180/7) = 26
    assert len(data["weeks"]) == data["total_weeks"]
    # Colombo tropical fallback ~80 ± 1
    rh_values = [w["relative_humidity_pct"] for w in data["weeks"]]
    assert all(abs(v - 80) <= 1 for v in rh_values)


def test_planner_timeline_no_lat_uses_global_default():
    """Without lat, the fallback should use the global default (~75%)."""
    payload = {"crop_name": "Tomato", "target_month": 6}
    response = client.post("/api/planner/timeline", json=payload)
    assert response.status_code == 200
    data = response.json()
    # Global default RH is 75 ± 1
    rh_values = [w["relative_humidity_pct"] for w in data["weeks"]]
    assert all(abs(v - 75) <= 1 for v in rh_values)


def test_planner_timeline_unknown_crop_no_days():
    """Timeline endpoint should 400 if crop is unknown and harvest_days is missing."""
    payload = {"crop_name": "Unobtainium Berry", "target_month": 6}
    response = client.post("/api/planner/timeline", json=payload)
    assert response.status_code == 400


def _make_image_bytes(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (100, 100), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_diagnose_upload_mock():
    image_bytes = b"\x89PNG\r\n\x1a\nfake-image-data"
    response = client.post(
        "/api/diagnose/upload",
        files={"file": ("leaf.jpg", image_bytes, "image/jpeg")},
        params={"crop_name": "Tomato", "mock": "true"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "diagnosis" in data
    assert "linked_remedy" in data
    diagnosis = data["diagnosis"]
    assert diagnosis["crop_detected"] == "Tomato"
    assert "issue_type" in diagnosis
    assert 0.0 <= diagnosis["confidence"] <= 1.0
    assert isinstance(diagnosis["symptoms"], list)
    assert diagnosis["severity"]


def test_pixel_analyzer_detects_healthy_foliage():
    from backend.app.services import diagnose_service

    image_bytes = _make_image_bytes((50, 150, 50))
    diagnosis = diagnose_service.analyze_leaf_pixels(image_bytes, crop_name="Test Crop")
    assert diagnosis["crop_detected"] == "Test Crop"
    assert diagnosis["issue_type"] == "Healthy Foliage"
    assert diagnosis["severity"] == "low"
    assert 0.0 <= diagnosis["confidence"] <= 1.0


def test_pixel_analyzer_detects_chlorosis():
    from backend.app.services import diagnose_service

    image_bytes = _make_image_bytes((200, 180, 60))
    diagnosis = diagnose_service.analyze_leaf_pixels(image_bytes, crop_name="Test Crop")
    assert diagnosis["crop_detected"] == "Test Crop"
    assert "Chlorosis" in diagnosis["issue_type"]
    assert diagnosis["severity"] in ("low", "moderate")


def test_remedy_generate():
    payload = {
        "issue_type": "Nitrogen Deficiency",
        "available_scraps": ["banana peels", "coffee grounds"],
    }
    response = client.post("/api/remedy/generate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["issue_type"] == "Nitrogen Deficiency"
    assert "remedy_name" in data
    assert len(data["ingredients"]) > 0
    assert len(data["preparation_steps"]) > 0
    assert "application_schedule" in data
    assert "matched_scraps" in data
    assert any("banana" in scrap.lower() for scrap in data["matched_scraps"])


def test_remedy_generate_unknown_issue():
    payload = {"issue_type": "Mystery Blight"}
    response = client.post("/api/remedy/generate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["issue_type"] == "Mystery Blight"
    assert "remedy_name" in data
    assert len(data["ingredients"]) > 0
