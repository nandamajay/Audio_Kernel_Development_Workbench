"""Dashboard routes."""

from flask import Blueprint, render_template

from app.models import ConversionJob, PatchRecord, TriageSession


dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.get("/")
def dashboard():
    stats = {
        "patches_reviewed": PatchRecord.query.count(),
        "drivers_converted": ConversionJob.query.count(),
        "triage_sessions": TriageSession.query.count(),
        "last_git_activity": "No activity yet",
    }
    recent_activity = [
        {"label": "Workspace initialized", "timestamp": "just now"},
        {"label": "Dashboard loaded", "timestamp": "just now"},
    ]
    return render_template("dashboard.html", stats=stats, recent_activity=recent_activity)


@dashboard_bp.get("/health")
def health():
    return {"status": "ok", "service": "akdw", "port": 5001}, 200
