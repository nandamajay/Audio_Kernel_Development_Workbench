"""Socket.IO event handlers for AKDW."""
# REUSED FROM: Q-Build-Manager/services/socket_handlers.py

from flask import request
from flask_socketio import emit, join_room


def register_socket_handlers(socketio):
    @socketio.on("connect")
    def handle_connect():
        emit("connected", {"status": "ok", "sid": request.sid})

    @socketio.on("join_agent_session")
    def handle_join_agent_session(data):
        session_id = (data or {}).get("session_id")
        if session_id:
            join_room(session_id)
            emit("session_joined", {"session_id": session_id})

    @socketio.on("terminal_input")
    def handle_terminal_input(data):
        emit("terminal_output", {"data": (data or {}).get("data", "")})
