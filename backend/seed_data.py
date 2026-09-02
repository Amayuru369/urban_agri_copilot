from sqlalchemy.orm import Session

from backend.app.core.database import SessionLocal, engine, Base
from backend.app.models.schemas import Crop, MarketPrice

CROPS = [
    {
        "name": "Green Chilli",
        "scientific_name": "Capsicum annuum",
        "category": "fruiting",
        "min_temp_c": 18.0,
        "max_temp_c": 35.0,
        "sunlight_hours_min": 6.0,
        "days_to_harvest": 90,
        "typical_yield_per_pot_g": 300.0,
        "companion_crops": "Tomato, Brinjal",
        "spacing_cm": 40,
        "watering_frequency_days": 2,
    },
    {
        "name": "Tomato",
        "scientific_name": "Solanum lycopersicum",
        "category": "fruiting",
        "min_temp_c": 18.0,
        "max_temp_c": 35.0,
        "sunlight_hours_min": 6.0,
        "days_to_harvest": 75,
        "typical_yield_per_pot_g": 800.0,
        "companion_crops": "Green Chilli, Basil",
        "spacing_cm": 45,
        "watering_frequency_days": 2,
    },
    {
        "name": "Gotukola",
        "scientific_name": "Centella asiatica",
        "category": "leafy",
        "min_temp_c": 15.0,
        "max_temp_c": 32.0,
        "sunlight_hours_min": 3.0,
        "days_to_harvest": 45,
        "typical_yield_per_pot_g": 150.0,
        "companion_crops": "Kang-kung",
        "spacing_cm": 10,
        "watering_frequency_days": 1,
    },
    {
        "name": "Kang-kung",
        "scientific_name": "Ipomoea aquatica",
        "category": "leafy",
        "min_temp_c": 20.0,
        "max_temp_c": 35.0,
        "sunlight_hours_min": 4.0,
        "days_to_harvest": 30,
        "typical_yield_per_pot_g": 200.0,
        "companion_crops": "Gotukola",
        "spacing_cm": 15,
        "watering_frequency_days": 1,
    },
    {
        "name": "Brinjal",
        "scientific_name": "Solanum melongena",
        "category": "fruiting",
        "min_temp_c": 20.0,
        "max_temp_c": 35.0,
        "sunlight_hours_min": 6.0,
        "days_to_harvest": 80,
        "typical_yield_per_pot_g": 700.0,
        "companion_crops": "Green Chilli, Onion",
        "spacing_cm": 45,
        "watering_frequency_days": 2,
    },
    {
        "name": "Cowpea",
        "scientific_name": "Vigna unguiculata",
        "category": "leafy",
        "min_temp_c": 20.0,
        "max_temp_c": 38.0,
        "sunlight_hours_min": 5.0,
        "days_to_harvest": 50,
        "typical_yield_per_pot_g": 250.0,
        "companion_crops": "Okra, Maize",
        "spacing_cm": 20,
        "watering_frequency_days": 2,
    },
]

MARKET_PRICES = [
    {"crop_name": "Green Chilli", "retail_price_per_kg": 1200.0, "wholesale_price_per_kg": 700.0, "currency": "LKR"},
    {"crop_name": "Tomato", "retail_price_per_kg": 300.0, "wholesale_price_per_kg": 180.0, "currency": "LKR"},
    {"crop_name": "Gotukola", "retail_price_per_kg": 600.0, "wholesale_price_per_kg": 350.0, "currency": "LKR"},
    {"crop_name": "Kang-kung", "retail_price_per_kg": 250.0, "wholesale_price_per_kg": 140.0, "currency": "LKR"},
    {"crop_name": "Brinjal", "retail_price_per_kg": 280.0, "wholesale_price_per_kg": 160.0, "currency": "LKR"},
    {"crop_name": "Cowpea", "retail_price_per_kg": 350.0, "wholesale_price_per_kg": 200.0, "currency": "LKR"},
]


def seed_crops(db: Session):
    if db.query(Crop).first():
        return
    for crop_data in CROPS:
        db.add(Crop(**crop_data))
    db.commit()


def seed_market_prices(db: Session):
    if db.query(MarketPrice).first():
        return
    for price_data in MARKET_PRICES:
        db.add(MarketPrice(**price_data))
    db.commit()


def seed_all():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_crops(db)
        seed_market_prices(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed_all()
