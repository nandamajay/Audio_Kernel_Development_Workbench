"""Activity log helper."""

from __future__ import annotations

from app.models import ActivityLog, db


def log_activity(event: str, event_type: str = "agent") -> None:
    try:
        row = ActivityLog(event=(event or "")[:500], event_type=(event_type or "agent")[:50])
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
