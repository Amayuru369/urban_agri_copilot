import asyncio
from datetime import datetime, timezone, timedelta
from backend.app.database import SessionLocal
from backend.app.models.garden import SystemAuditLog
from backend.app.services import garden_monitor

db = SessionLocal()

print("=== CURRENT LATEST 3 AUDIT LOGS ===")
for row in db.query(SystemAuditLog).order_by(SystemAuditLog.timestamp.desc()).limit(3).all():
    print(f"ID={row.id} | Time={row.timestamp} | Trigger={row.trigger_type} | Action={row.action}")

print("\n=== RUNNING EVALUATE GARDEN STATE WITH TRIGGER MANUAL ===")
try:
    # Try calling evaluate_garden_state
    try:
        count = asyncio.run(garden_monitor.evaluate_garden_state(db, trigger_type="MANUAL"))
    except TypeError:
        count = asyncio.run(garden_monitor.evaluate_garden_state(db))
    print(f"Evaluation finished, alerts returned: {count}")
except Exception as ex:
    print(f"Evaluation threw error: {ex}")

db.expire_all()
latest = db.query(SystemAuditLog).order_by(SystemAuditLog.timestamp.desc()).first()
print(f"\n=== LATEST AUDIT LOG AFTER RUN ===")
if latest:
    print(f"ID={latest.id} | Time={latest.timestamp} | Trigger={latest.trigger_type} | Status={latest.status}")
else:
    print("No logs in database.")

db.close()
