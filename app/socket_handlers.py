"""Socket.IO event handlers for AKDW."""
# REUSED FROM: Q-Build-Manager/services/socket_handlers.py

from flask import current_app, request
from flask_socketio import emit, join_room

from app.services.agent_service import AgentService, create_session_id
from app.services.terminal_service import terminal_service


def register_socket_handlers(socketio):
    fallback_service = AgentService(socketio)
    terminal_service.attach_socketio(socketio)

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

    @socketio.on("connect", namespace="/terminal")
    def terminal_connect():
        emit("connected", {"status": "ok", "sid": request.sid, "namespace": "/terminal"})

    @socketio.on("terminal:join", namespace="/terminal")
    def terminal_join(data):
        payload = data or {}
        session_id = payload.get("session_id") or terminal_service.create_session()
        join_room(session_id)
        emit("terminal:joined", {"session_id": session_id})

    @socketio.on("terminal:input", namespace="/terminal")
    def terminal_input(data):
        payload = data or {}
        session_id = payload.get("session_id") or ""
        text = payload.get("data", "")
        if not session_id:
            return
        ok = terminal_service.write(session_id, text)
        if not ok:
            emit("terminal:output", {"session_id": session_id, "data": "[terminal closed]\n"})

    @socketio.on("terminal:resize", namespace="/terminal")
    def terminal_resize(data):
        payload = data or {}
        session_id = payload.get("session_id") or ""
        cols = int(payload.get("cols") or 80)
        rows = int(payload.get("rows") or 24)
        if session_id:
            terminal_service.resize(session_id, cols, rows)

    @socketio.on("terminal:kill", namespace="/terminal")
    def terminal_kill(data):
        payload = data or {}
        session_id = payload.get("session_id") or ""
        if session_id:
            terminal_service.kill(session_id)

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
