"""Agent routes."""

from flask import Blueprint, render_template


agent_bp = Blueprint("agent", __name__, url_prefix="/agent")


@agent_bp.get("/")
def agent_home():
    return render_template("agent.html")
