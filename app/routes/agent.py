"""Agent routes and session APIs."""
# REUSED FROM (PATTERN): Q-Build-Manager/routes/agent_routes.py

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from app.config import MODEL_METADATA, get_available_models, get_default_model
from app.models import Session, db
from app.services.session_service import create_session_id, get_session_messages, grouped_sessions

agent_bp = Blueprint("agent", __name__, url_prefix="/agent")


def _agent_service():
    return current_app.extensions["agent_service"]


@agent_bp.get("/")
def agent_home():
    return render_template(
        "agent.html",
        models=get_available_models(),
        model_metadata=MODEL_METADATA,
        default_model=get_default_model(),
    )


@agent_bp.get("/history")
@agent_bp.get("/history/")
def agent_history():
    return render_template("agent_history.html")


@agent_bp.post("/chat")
def agent_chat():
    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    if not message and not payload.get("attachments"):
        return jsonify({"ok": False, "error": "Message or attachments required"}), 400

    session_id = (payload.get("session_id") or create_session_id()).strip()
    model = (payload.get("model") or get_default_model()).strip()
    page = (payload.get("page") or "agent").strip()

    result = _agent_service().stream_chat(
        session_id=session_id,
        message=message,
        model=model,
        attachments=payload.get("attachments", []),
        selected_code=payload.get("selected_code", ""),
        filename=payload.get("filename", ""),
        page=page,
    )

    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "model": model,
            "response": result.get("response", ""),
        }
    )


@agent_bp.get("/sessions")
def list_sessions():
    return jsonify({"ok": True, "grouped": grouped_sessions()})


@agent_bp.get("/sessions/<session_id>/messages")
def list_session_messages(session_id: str):
    return jsonify({"ok": True, "messages": get_session_messages(session_id)})


@agent_bp.post("/sessions/<session_id>/continue")
def continue_session(session_id: str):
    session = Session.query.filter_by(id=session_id).first()
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    session.status = "active"
    db.session.commit()
    return jsonify({"ok": True})
