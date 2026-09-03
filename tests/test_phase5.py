"""Tests for the Plant Monitoring & Alert System (Phase 5)."""

import os
from datetime import date, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase5.db")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import settings
from backend.app.core.database import Base, get_db
from backend.main import app
from backend.app.models.garden import PlantAlert, TrackedPlant
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


def test_create_plant():
    """POST /api/garden/plants should register a new tracked plant."""
    payload = {
        "crop_name": "Tomato",
        "planted_date": (date.today() - timedelta(days=10)).isoformat(),
        "pot_size_liters": 15.0,
        "latitude": 6.9271,
        "longitude": 79.8612,
        "telegram_chat_id": None,
    }
    response = client.post("/api/garden/plants", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["crop_name"] == "Tomato"
    assert data["id"] >= 1
    assert "message" in data


def test_create_plant_chilli():
    """Register a second plant (Chilli) to verify multi-plant support."""
    payload = {
        "crop_name": "Chilli",
        "planted_date": (date.today() - timedelta(days=28)).isoformat(),
        "pot_size_liters": 10.0,
        "latitude": 6.9271,
        "longitude": 79.8612,
        "telegram_chat_id": None,
    }
    response = client.post("/api/garden/plants", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["crop_name"] == "Chilli"


def test_dashboard_returns_plants_with_days_active(monkeypatch):
    """GET /api/garden/dashboard should return active plants with computed days_active."""

    # Monkeypatch weather fetch to avoid real API calls
    async def fake_fetch_weather_risk(lat, lon):
        return {
            "heavy_rain": False,
            "extreme_heat": False,
            "precipitation_mm": 2.0,
            "max_temp_c": 31.0,
            "description": "Conditions normal",
        }

    monkeypatch.setattr(
        "backend.app.services.garden_monitor.fetch_weather_risk",
        fake_fetch_weather_risk,
    )

    response = client.get("/api/garden/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert "plants" in data
    assert data["active_plants"] >= 2
    assert "evaluated_on" in data

    tomato = next((p for p in data["plants"] if p["crop_name"] == "Tomato"), None)
    assert tomato is not None
    assert tomato["days_active"] == 10
    assert 0 < tomato["progress_pct"] <= 100
    assert isinstance(tomato["unresolved_alerts"], list)

    chilli = next((p for p in data["plants"] if p["crop_name"] == "Chilli"), None)
    assert chilli is not None
    assert chilli["days_active"] == 28


def test_milestone_alert_generated(monkeypatch):
    """Dashboard evaluation should generate milestone alerts for plants near a target day."""

    async def fake_fetch_weather_risk(lat, lon):
        return {
            "heavy_rain": False,
            "extreme_heat": False,
            "precipitation_mm": 0.0,
            "max_temp_c": 30.0,
            "description": "Conditions normal",
        }

    monkeypatch.setattr(
        "backend.app.services.garden_monitor.fetch_weather_risk",
        fake_fetch_weather_risk,
    )

    response = client.get("/api/garden/dashboard")
    assert response.status_code == 200
    data = response.json()

    # Tomato at day 10 should match the day-7 milestone (within ±2 window? No, |10-7|=3 > 2)
    # But it matches day 21? No, |10-21|=11 > 2. So no milestone for Tomato at day 10.
    # Chilli at day 28 should NOT match day 30 (|28-30|=2, within window!)
    chilli = next((p for p in data["plants"] if p["crop_name"] == "Chilli"), None)
    assert chilli is not None
    milestone_alerts = [a for a in chilli["unresolved_alerts"] if a["alert_type"] == "milestone"]
    assert len(milestone_alerts) >= 1
    assert "Vegetative establishment" in milestone_alerts[0]["message"]


def test_weather_alert_generated(monkeypatch):
    """Dashboard should generate weather alerts when conditions are extreme."""

    async def fake_fetch_weather_risk_rainy(lat, lon):
        return {
            "heavy_rain": True,
            "extreme_heat": False,
            "precipitation_mm": 35.0,
            "max_temp_c": 28.0,
            "description": "Heavy rain (35mm expected)",
        }

    monkeypatch.setattr(
        "backend.app.services.garden_monitor.fetch_weather_risk",
        fake_fetch_weather_risk_rainy,
    )

    response = client.get("/api/garden/dashboard")
    assert response.status_code == 200
    data = response.json()

    # At least one plant should have a weather alert
    all_weather_alerts = []
    for plant in data["plants"]:
        all_weather_alerts.extend(
            [a for a in plant["unresolved_alerts"] if a["alert_type"] == "weather"]
        )
    assert len(all_weather_alerts) >= 1
    assert any("Heavy rain" in a["message"] for a in all_weather_alerts)


def test_resolve_alert(monkeypatch):
    """PATCH /api/garden/alerts/{id}/resolve should mark an alert as resolved."""

    async def fake_fetch_weather_risk(lat, lon):
        return {
            "heavy_rain": False,
            "extreme_heat": False,
            "precipitation_mm": 1.0,
            "max_temp_c": 30.0,
            "description": "Conditions normal",
        }

    monkeypatch.setattr(
        "backend.app.services.garden_monitor.fetch_weather_risk",
        fake_fetch_weather_risk,
    )

    # Get an unresolved alert ID
    response = client.get("/api/garden/dashboard")
    data = response.json()
    alert_id = None
    for plant in data["plants"]:
        if plant["unresolved_alerts"]:
            alert_id = plant["unresolved_alerts"][0]["id"]
            break

    assert alert_id is not None, "Expected at least one unresolved alert to resolve"

    # Resolve it
    response = client.patch(f"/api/garden/alerts/{alert_id}/resolve")
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == alert_id
    assert result["resolved"] is True


def test_resolve_nonexistent_alert():
    """PATCH on a non-existent alert should return 404."""
    response = client.patch("/api/garden/alerts/99999/resolve")
    assert response.status_code == 404


def test_no_duplicate_alerts_same_day(monkeypatch):
    """Running dashboard evaluation twice on the same day should not duplicate alerts."""

    async def fake_fetch_weather_risk(lat, lon):
        return {
            "heavy_rain": True,
            "extreme_heat": False,
            "precipitation_mm": 25.0,
            "max_temp_c": 29.0,
            "description": "Heavy rain (25mm expected)",
        }

    monkeypatch.setattr(
        "backend.app.services.garden_monitor.fetch_weather_risk",
        fake_fetch_weather_risk,
    )

    # First call
    r1 = client.get("/api/garden/dashboard")
    assert r1.status_code == 200
    data1 = r1.json()

    # Second call (same day)
    r2 = client.get("/api/garden/dashboard")
    assert r2.status_code == 200
    data2 = r2.json()

    # Alert counts should be the same (no duplicates)
    count1 = sum(len(p["unresolved_alerts"]) for p in data1["plants"])
    count2 = sum(len(p["unresolved_alerts"]) for p in data2["plants"])
    assert count1 == count2, f"Duplicate alerts created: {count1} vs {count2}"
