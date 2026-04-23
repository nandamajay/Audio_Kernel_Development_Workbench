"""Socket.IO event handlers for AKDW."""
# REUSED FROM: Q-Build-Manager/services/socket_handlers.py

from flask import current_app, request
from flask_socketio import emit, join_room

from app.services.agent_service import AgentService, create_session_id


def register_socket_handlers(socketio):
    fallback_service = AgentService(socketio)

    def _service() -> AgentService:
        app_service = None
        try:
            app_service = current_app.extensions.get("agent_service")
        except Exception:
            app_service = None
        return app_service or fallback_service

    @socketio.on("connect")
    def handle_connect():
        emit("connected", {"status": "ok", "sid": request.sid})

    @socketio.on("join_agent_session")
    def handle_join_agent_session(data):
        payload = data or {}
        session_id = payload.get("session_id") or create_session_id()
        join_room(session_id)
        emit("session_joined", {"session_id": session_id})

    @socketio.on("terminal_input")
    def handle_terminal_input(data):
        payload = data or {}
        session_id = payload.get("session_id")
        text = payload.get("data", "")
        if session_id:
            _service().emit_terminal_line(session_id, text)
            return
        emit("terminal_output", {"data": text})

    @socketio.on("editor_query")
    def handle_editor_query(data):
        payload = data or {}
        session_id = payload.get("session_id") or create_session_id()
        join_room(session_id)
        _service().stream_chat(
            session_id=session_id,
            message=payload.get("message", ""),
            model=payload.get("model"),
            attachments=payload.get("attachments", []),
            selected_code=payload.get("selected_code", ""),
            filename=payload.get("filename", ""),
            page="editor",
        )

    @socketio.on("agent_chat")
    def handle_agent_chat(data):
        payload = data or {}
        session_id = payload.get("session_id") or create_session_id()
        join_room(session_id)
        _service().stream_chat(
            session_id=session_id,
            message=payload.get("message", ""),
            model=payload.get("model"),
            attachments=payload.get("attachments", []),
            selected_code=payload.get("selected_code", ""),
            filename=payload.get("filename", ""),
            page="agent",
        )
