import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.app.core.config import settings

# Fallback to a writable location in /tmp if DATABASE_URL is missing or invalid on Vercel
db_url = getattr(settings, "DATABASE_URL", None) or os.getenv("DATABASE_URL")
if not db_url or not str(db_url).strip():
    db_url = "sqlite:////tmp/urban_agri.db"

connect_args = {"check_same_thread": False} if "sqlite" in str(db_url) else {}

engine = create_engine(
    str(db_url),
    connect_args=connect_args,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()