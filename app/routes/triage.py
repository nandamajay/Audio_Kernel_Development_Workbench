"""Triage routes."""

from flask import Blueprint, render_template


triage_bp = Blueprint("triage", __name__, url_prefix="/triage")


@triage_bp.get("/")
def triage_home():
    return render_template("triage.html")
