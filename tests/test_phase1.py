import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_phase1.db")

import pytest
from fastapi.testclient import TestClient
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


def test_health_check():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "UrbanAgri-Copilot"


def test_list_crops():
    response = client.get("/api/crops")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 6
    assert all("name" in crop and "category" in crop for crop in data)


def test_list_crops_with_category_filter():
    response = client.get("/api/crops?category=fruiting")
    assert response.status_code == 200
    data = response.json()
    assert len(data) > 0
    assert all(crop["category"] == "fruiting" for crop in data)


def test_get_crop_detail():
    response = client.get("/api/crops/1")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == 1
    assert "name" in data
    assert "days_to_harvest" in data


def test_get_crop_not_found():
    response = client.get("/api/crops/9999")
    assert response.status_code == 404


def test_market_prices():
    response = client.get("/api/market/prices")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 6
    for price in data:
        assert price["currency"] == "LKR"
        assert "margin_per_kg" in price
        assert "margin_percent" in price
        assert price["retail_price_per_kg"] >= price["wholesale_price_per_kg"]


def test_weather_current(monkeypatch):
    async def fake_fetch_microclimate(lat: float, lon: float):
        return {
            "latitude": lat,
            "longitude": lon,
            "current": {
                "temperature_c": 28.5,
                "relative_humidity_percent": 70.0,
                "precipitation_probability_percent": 20.0,
                "time": "2026-08-28T12:00",
            },
            "forecast": [
                {
                    "date": "2026-08-28",
                    "min_temp_c": 24.0,
                    "max_temp_c": 30.0,
                    "daylight_duration_seconds": 43200.0,
                }
            ],
        }

    monkeypatch.setattr(
        "backend.app.services.weather_service.fetch_microclimate",
        fake_fetch_microclimate,
    )

    response = client.get("/api/weather/current?lat=6.9271&lon=79.8612")
    assert response.status_code == 200
    data = response.json()
    assert data["latitude"] == 6.9271
    assert data["longitude"] == 79.8612
    assert "current" in data
    assert "forecast" in data
    assert data["current"]["temperature_c"] == 28.5
    assert len(data["forecast"]) > 0
