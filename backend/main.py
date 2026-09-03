
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import settings
from backend.app.core.database import Base, engine, SessionLocal
from backend.app.models import garden as _garden_models  # noqa: F401 — ensure models are registered
from backend.app.routers import crops, diagnose, garden, market, planner, remedy, report, weather
from backend.app.services.garden_monitor import evaluate_garden_state
from backend.seed_data import seed_all

scheduler = AsyncIOScheduler()


async def _run_garden_monitor():
    """Wrapper to open a DB session and run the garden evaluation."""
    print("[Scheduler] Triggering garden monitor job now...", flush=True)
    db = SessionLocal()
    try:
        count = await evaluate_garden_state(db)
        print(f"[Scheduler] Evaluation finished. Generated {count} alerts.", flush=True)
    except Exception as e:
        print(f"[Scheduler] Run failed with error: {e}", flush=True)
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    Base.metadata.create_all(bind=engine)
    seed_all()

    # Run once immediately on startup
    await _run_garden_monitor()

    # Run daily at 06:00 AM server time
    scheduler.add_job(
        _run_garden_monitor,
        trigger=CronTrigger(hour=6, minute=0),
        id="garden_monitor_daily",
        replace_existing=True,
    )
    scheduler.start()

    yield

    # --- Shutdown ---
    scheduler.shutdown(wait=False)


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(crops.router, prefix="/api")
app.include_router(weather.router, prefix="/api")
app.include_router(market.router, prefix="/api")
app.include_router(planner.router, prefix="/api")
app.include_router(diagnose.router, prefix="/api")
app.include_router(remedy.router, prefix="/api")
app.include_router(report.router, prefix="/api")
app.include_router(garden.router, prefix="/api")


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
