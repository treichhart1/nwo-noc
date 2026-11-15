from app.core.database import SessionLocal
from app.core.config import settings
from .service import generate, reminders


def tick():
    if not settings.maintenance_scheduler_enabled:
        return {"enabled": False}
    with SessionLocal() as db:
        result = generate(db)
        result["reminders"] = reminders(db)
        db.commit()
        return result
