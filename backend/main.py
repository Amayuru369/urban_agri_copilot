import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
from backend.app.models.garden import SystemAuditLog
from backend.app.routers import chat, crops, diagnose, garden, market, planner, remedy, report, weather
from backend.app.services.garden_monitor import (
    evaluate_garden_state,
    send_daily_morning_digest,
    send_telegram_alert,
)
from backend.app.services.digest_service import send_morning_garden_digest
from backend.seed_data import seed_all

scheduler = AsyncIOScheduler()


async def _run_garden_monitor(trigger_type: str = "CRON_SCHEDULED"):
    """Wrapper to open a DB session and run the garden evaluation."""
    print(f"[Scheduler] Triggering garden monitor job ({trigger_type}) now...", flush=True)
    db = SessionLocal()
    try:
        count = await evaluate_garden_state(db)
        print(f"[Scheduler] Evaluation finished. Generated {count} alerts.", flush=True)

        action_name = (
            "Manual Crop & Risk Scan" if trigger_type == "MANUAL" else "Autonomous Crop & Risk Scan"
        )
        audit = SystemAuditLog(
            timestamp=datetime.now(timezone.utc),
            trigger_type=trigger_type,
            action=action_name,
            details=f"Evaluated active crops. Generated {count or 0} new alert(s).",
            status="SUCCESS",
        )
        db.add(audit)
        db.commit()

        # 1. Dispatch 6:00 AM daily morning digest
        if trigger_type == "CRON_SCHEDULED":
            try:
                await send_daily_morning_digest(db, count or 0)
            except Exception as digest_err:
                print(f"[Scheduler] Morning digest failed (non-critical): {digest_err}", flush=True)

        # 2. Dispatch Telegram alert on manual green button refresh
        elif trigger_type == "MANUAL":
            chat_id = getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None)
            if chat_id:
                msg = (
                    "🔄 <b>Manual Garden Scan Completed</b>\n\n"
                    f"• <b>Action:</b> Dashboard refresh requested\n"
                    f"• <b>New Alerts Generated:</b> {count or 0}\n"
                    "• <b>Status:</b> Evaluation up to date"
                )
                asyncio.create_task(send_telegram_alert(chat_id, msg))

    except Exception as e:
        print(f"[Scheduler] Run failed with error: {e}", flush=True)
        audit = SystemAuditLog(
            timestamp=datetime.now(timezone.utc),
            trigger_type=trigger_type,
            action="Crop & Risk Scan",
            details=f"Execution error: {e}",
            status="FAILED",
        )
        db.add(audit)
        db.commit()
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    Base.metadata.create_all(bind=engine)
    seed_all()

    # 1. Announce system is online first (awaited so it arrives before alerts)
    chat_id = getattr(settings, "TELEGRAM_DEFAULT_CHAT_ID", None)
    if chat_id:
        startup_msg = (
            "🚀 <b>UrbanAgri-Copilot Online</b>\n\n"
            "• <b>Status:</b> System initialized & monitoring active\n"
            "• <b>Daily Scan:</b> Scheduled for 06:00 AM\n"
            "• <b>Morning Digest:</b> Scheduled for 07:00 AM"
        )
        await send_telegram_alert(chat_id, startup_msg)

    # 2. Run evaluation on startup (weather risks & alerts deliver second)
    await _run_garden_monitor(trigger_type="STARTUP")

    # 3. Run daily at 06:00 AM server time
    scheduler.add_job(
        _run_garden_monitor,
        trigger=CronTrigger(hour=6, minute=0),
        id="garden_monitor_daily",
        replace_existing=True,
    )

    # 4. Send the Daily Morning Garden Digest at 07:00 AM server time
    scheduler.add_job(
        send_morning_garden_digest,
        trigger=CronTrigger(hour=7, minute=0),
        id="daily_morning_digest",
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
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])

@app.get("/api/scheduler/jobs")
def get_scheduled_jobs():
    return [
        {
            "id": job.id,
            "trigger": str(job.trigger),
            "next_run_time": str(job.next_run_time),
        }
        for job in scheduler.get_jobs()
    ]

@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")