# UrbanAgri-Copilot — Phase 1

Phase 1 scaffolding for UrbanAgri-Copilot: a FastAPI backend with a SQLite local data layer, SQLAlchemy ORM models, Open-Meteo weather integration, and Sri Lankan market price seed data.

## Project Structure

```
urban_agri_copilot/
├── backend/
│   ├── app/
│   │   ├── core/
│   │   │   ├── config.py          # Pydantic settings
│   │   │   └── database.py        # SQLAlchemy engine & session
│   │   ├── models/
│   │   │   └── schemas.py         # DB models + Pydantic schemas
│   │   ├── services/
│   │   │   ├── weather_service.py # Open-Meteo async client
│   │   │   └── market_service.py  # Price + margin calculations
│   │   └── routers/
│   │       ├── crops.py           # Crop listing & detail endpoints
│   │       ├── weather.py         # Current weather endpoint
│   │       └── market.py          # Market price endpoint
│   ├── main.py                    # FastAPI application entry point
│   └── seed_data.py               # Baseline crops & market prices
├── tests/
│   └── test_phase1.py             # Unit tests for Phase 1
├── requirements.txt
└── README.md
```

## Setup

1. Create and activate a virtual environment (recommended).
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Run the Backend

Run from the `urban_agri_copilot` project root so package imports resolve:

```bash
cd urban_agri_copilot
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for interactive OpenAPI documentation.

## Seeded Data

The database is created automatically on startup and pre-seeded with:

- 6 urban home-garden crops: Green Chilli, Tomato, Gotukola, Kang-kung, Brinjal, Cowpea.
- Realistic LKR retail/wholesale prices for each crop.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/crops` | List all crops; filter with `?category=fruiting` |
| GET | `/api/crops/{crop_id}` | Crop growth-rule detail |
| GET | `/api/weather/current?lat={lat}&lon={lon}` | Current micro-climate + 7-day forecast |
| GET | `/api/market/prices` | Crop prices with retail-to-wholesale margins |

## Running Tests

```bash
cd urban_agri_copilot
pytest tests/test_phase1.py -v
```

`pytest.ini` adds the project root to the Python path, so no extra `PYTHONPATH` setup is needed. Tests use a separate SQLite database file (`test_phase1.db`) and verify health, crop endpoints, market margins, and the weather endpoint response shape.
