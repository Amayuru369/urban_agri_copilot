
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
from backend.app.models.garden import SystemAuditLog
<<<<<<< Updated upstream
from backend.app.routers import crops, diagnose, garden, market, planner, remedy, report, weather
from backend.app.services.garden_monitor import evaluate_garden_state, send_daily_morning_digest
=======
from backend.app.routers import auth, chat, crops, diagnose, garden, market, planner, remedy, report, users, weather
from backend.app.services.garden_monitor import (
    evaluate_garden_state,
    send_daily_morning_digest,
    send_telegram_alert,
)
from backend.app.services.digest_service import send_morning_garden_digest
>>>>>>> Stashed changes
from backend.seed_data import seed_all

scheduler = AsyncIOScheduler()


def _ensure_user_id_column() -> None:
    """Lightweight additive migration: add nullable user_id column to tracked_plants
    if the SQLite table was created before the profile feature was introduced.

    This never drops or alters existing columns, so it is safe to run on every
    startup and keeps backward compatibility with older databases.
    """
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        if "tracked_plants" not in inspector.get_table_names():
            return
        existing_cols = {c["name"] for c in inspector.get_columns("tracked_plants")}
        if "user_id" in existing_cols:
            return

        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tracked_plants ADD COLUMN user_id INTEGER"))
        print("[Migration] Added nullable user_id column to tracked_plants.", flush=True)
    except Exception as exc:
        # Never crash startup because of an opportunistic migration
        print(f"[Migration] Skipped user_id column check: {exc}", flush=True)


def _ensure_user_auth_columns() -> None:
    """Lightweight additive migration: add nullable hashed_password and
    is_admin columns to the users table when it predates the login feature.
    """
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        if "users" not in inspector.get_table_names():
            return
        existing_cols = {c["name"] for c in inspector.get_columns("users")}

        statements = []
        if "hashed_password" not in existing_cols:
            statements.append("ALTER TABLE users ADD COLUMN hashed_password VARCHAR")
        if "is_admin" not in existing_cols:
            statements.append("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")

        if not statements:
            return

        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
        print(f"[Migration] Added auth column(s) to users: {statements}", flush=True)
    except Exception as exc:
        print(f"[Migration] Skipped users auth column check: {exc}", flush=True)


def _seed_default_admin() -> None:
    """Create the default admin profile on first startup so a login exists.

    Credentials come from env vars ADMIN_USERNAME / ADMIN_PASSWORD with
    safe defaults so the app is always reachable in development.
    """
    import os
    from backend.app.core.auth import hash_password
    from backend.app.models.garden import User

    username = (os.getenv("ADMIN_USERNAME") or "admin").strip()
    password = os.getenv("ADMIN_PASSWORD") or "admin123"

    db = SessionLocal()
    try:
        # Skip if any admin already exists
        existing_admin = db.query(User).filter(User.is_admin == True).first()  # noqa: E712
        if existing_admin:
            return

        # Reuse an existing profile with the same name if present
        user = db.query(User).filter(User.name.ilike(username)).first()
        if user:
            user.hashed_password = hash_password(password)
            user.is_admin = True
            db.commit()
            print(f"[Auth Seed] Promoted existing profile '{user.name}' (id={user.id}) to admin.", flush=True)
        else:
            user = User(
                name=username,
                hashed_password=hash_password(password),
                is_admin=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"[Auth Seed] Created default admin '{username}' (id={user.id}).", flush=True)
    except Exception as exc:
        print(f"[Auth Seed] Failed to seed default admin: {exc}", flush=True)
    finally:
        db.close()


async def _run_garden_monitor(trigger_type: str = "CRON_SCHEDULED"):
    """Wrapper to open a DB session and run the garden evaluation."""
    print("[Scheduler] Triggering garden monitor job now...", flush=True)
    db = SessionLocal()
    try:
        count = await evaluate_garden_state(db)
        print(f"[Scheduler] Evaluation finished. Generated {count} alerts.", flush=True)
        # Record successful audit entry
        audit = SystemAuditLog(
            trigger_type=trigger_type,
            action="Autonomous Crop & Risk Scan",
            details=f"Evaluated active crops. Generated {count or 0} new alert(s).",
            status="SUCCESS",
        )
        db.add(audit)
        db.commit()

        # Send morning digest only for scheduled cron runs (not on STARTUP)
        if trigger_type == "CRON_SCHEDULED":
            try:
                await send_daily_morning_digest(db, count or 0)
            except Exception as digest_err:
                print(f"[Scheduler] Morning digest failed (non-critical): {digest_err}", flush=True)

    except Exception as e:
        print(f"[Scheduler] Run failed with error: {e}", flush=True)
        # Record failure audit entry
        audit = SystemAuditLog(
            trigger_type=trigger_type,
            action="Autonomous Crop & Risk Scan",
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
    _ensure_user_id_column()
    _ensure_user_auth_columns()
    seed_all()
    _seed_default_admin()

    # Run once immediately on startup
    await _run_garden_monitor(trigger_type="STARTUP")

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
<<<<<<< Updated upstream
=======
app.include_router(users.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
>>>>>>> Stashed changes


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
