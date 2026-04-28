"""Terminal-IDE API routes for terminal sessions and agent mode."""

from __future__ import annotations

import os
import time
import uuid

from flask import Blueprint, current_app, jsonify, request
from flask_socketio import emit, join_room, leave_room
from app.config import get_default_model
from app.models import (
    TerminalCommandAudit,
    delete_host_from_db,
    get_saved_hosts,
    save_host_to_db,
)
from app.ssh_manager import (
    close_session as close_ssh_session,
    create_session as create_ssh_session,
    get_session as get_ssh_session,
    list_sessions as list_ssh_sessions,
)
from app.services.terminal_service import terminal_service


terminal_bp = Blueprint("terminal", __name__)
_socket_handlers_registered = False


@terminal_bp.get("/api/terminal/sessions")
def get_terminal_sessions():
    return jsonify({"sessions": list_ssh_sessions()})


@terminal_bp.get("/api/terminal/hosts")
def get_terminal_hosts():
    return jsonify({"hosts": get_saved_hosts()})


@terminal_bp.post("/api/terminal/hosts")
def save_terminal_host():
    payload = request.get_json(silent=True) or {}
    hostname = (payload.get("hostname") or "").strip()
    if not hostname:
        return jsonify({"success": False, "error": "hostname is required"}), 400
    try:
        host_id = save_host_to_db(
            label=(payload.get("label") or hostname),
            hostname=hostname,
            port=int(payload.get("port") or 22),
            username=(payload.get("username") or "").strip(),
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({"success": True, "id": host_id})


@terminal_bp.delete("/api/terminal/hosts/<int:host_id>")
def delete_terminal_host(host_id: int):
    delete_host_from_db(host_id)
    return jsonify({"success": True})


def register_terminal_socketio_handlers(sio):
    global _socket_handlers_registered
    if _socket_handlers_registered:
        return
    _socket_handlers_registered = True

    @sio.on("terminal_join")
    def handle_terminal_join(data):
        session_id = (data or {}).get("session_id")
        if not session_id:
            return
        join_room(session_id)
        emit("joined", {"session_id": session_id, "status": "ok"})

    @sio.on("terminal_connect")
    def handle_terminal_connect(data):
        payload = data or {}
        session_id = (payload.get("session_id") or "").strip() or str(uuid.uuid4())[:8]
        hostname = (payload.get("hostname") or "").strip()
        username = (payload.get("username") or "").strip()
        current_app.logger.info(
            "terminal_connect start session=%s host=%s user=%s",
            session_id,
            hostname,
            username,
        )
        if not hostname or not username:
            emit(
                "terminal_error",
                {"session_id": session_id, "message": "hostname and username are required"},
                room=session_id,
            )
            return

        join_room(session_id)
        close_ssh_session(session_id)
        sess = create_ssh_session(session_id=session_id, socketio=sio)
        result = sess.connect(
            hostname=hostname,
            port=int(payload.get("port") or 22),
            username=username,
            password=payload.get("password"),
            key_path=payload.get("key_path"),
        )
        if result.get("success"):
            sess.start_pty_reader()
            current_app.logger.info(
                "terminal_connect success session=%s host=%s user=%s",
                session_id,
                hostname,
                username,
            )
            sio.emit(
                "terminal_connected",
                {
                    "session_id": session_id,
                    "hostname": hostname,
                    "username": username,
                    "message": result.get("message") or f"Connected to {username}@{hostname}",
                },
                room=session_id,
            )
            return

        close_ssh_session(session_id)
        current_app.logger.warning(
            "terminal_connect failure session=%s host=%s user=%s msg=%s",
            session_id,
            hostname,
            username,
            result.get("message") or "Connection failed",
        )
        sio.emit(
            "terminal_error",
            {"session_id": session_id, "message": result.get("message") or "Connection failed"},
            room=session_id,
        )

    @sio.on("terminal_input")
    def handle_terminal_input(data):
        payload = data or {}
        session_id = payload.get("session_id")
        ssh_sess = get_ssh_session(session_id) if session_id else None
        if ssh_sess:
            ssh_sess.send(payload.get("data") or "")
            return
        emit(
            "terminal_error",
            {"session_id": session_id, "message": "Session not found or expired"},
            room=session_id,
        )

    @sio.on("terminal_resize")
    def handle_terminal_resize(data):
        payload = data or {}
        session_id = payload.get("session_id")
        ssh_sess = get_ssh_session(session_id) if session_id else None
        if not ssh_sess:
            return
        ssh_sess.resize(int(payload.get("cols") or 120), int(payload.get("rows") or 40))

    @sio.on("terminal_disconnect_session")
    def handle_terminal_disconnect_session(data):
        payload = data or {}
        session_id = payload.get("session_id")
        if not session_id:
            return
        close_ssh_session(session_id)
        leave_room(session_id)
        emit("terminal_closed", {"session_id": session_id, "message": "Disconnected"})


@terminal_bp.post("/api/terminal/session")
def create_terminal_session():
    payload = request.get_json(silent=True) or {}
    cwd = (payload.get("cwd") or current_app.config.get("KERNEL_SRC_PATH", "/app/kernel")).strip()
    session_id = terminal_service.create_session(cwd=cwd)
    return jsonify({"ok": True, "session_id": session_id, "cwd": cwd})


@terminal_bp.post("/api/terminal/agent")
def terminal_agent_mode():
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or "").strip() or terminal_service.create_session()
    prompt = (payload.get("prompt") or "").strip()
    cwd = (payload.get("cwd") or current_app.config.get("KERNEL_SRC_PATH", "/app/kernel")).strip()
    file_context = (payload.get("file_context") or "").strip()
    if not prompt:
        return jsonify({"ok": False, "error": "prompt is required"}), 400

    if session_id not in terminal_service.sessions:
        terminal_service.create_session(cwd=cwd, session_id=session_id)
    recent_output = terminal_service.get_recent_output(session_id, lines=20)
    filename = (payload.get("filename") or "(none)").strip()

    sys_prompt = (
        "You are an expert Linux kernel developer assistant running inside a development container. "
        f"The user's current directory is {cwd}. "
        f"The user's currently open file is {filename}. "
        "You can run shell commands to help the user. When you need to run a command, output it in a ```bash fenced block. "
        "Available tools: git clone, git format-patch, git am, make, checkpatch.pl, grep, find, vim edits via patch. "
        "Always explain what you're doing before running commands. "
        "For gerrit: use `git push origin HEAD:refs/for/main`. "
        "Token budget: keep responses under 2000 tokens.\n\n"
        "Last terminal output:\n"
        f"{recent_output}\n\n"
        "Open file context:\n"
        f"{file_context}"
    )

    agent_service = current_app.extensions["agent_service"]
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": prompt},
    ]

    socketio = agent_service.socketio
    socketio.emit("agent:thinking", {"session_id": session_id, "message": "🤔 Thinking..."}, namespace="/terminal", to=session_id)
    start = time.time()
    response = agent_service._try_qgenie_chat((payload.get("model") or get_default_model()), messages)

    commands = terminal_service.extract_bash_blocks(response)
    outputs = []
    actor = (payload.get("actor") or "terminal-agent").strip()
    for cmd in commands:
        socketio.emit("agent:tool_call", {"session_id": session_id, "message": f"⚡ Running: {cmd}"}, namespace="/terminal", to=session_id)
        out = terminal_service.execute_safe_command(session_id, cmd, cwd=cwd, actor=actor)
        outputs.append({"cmd": cmd, "output": out})
        socketio.emit("agent:output", {"session_id": session_id, "output": out}, namespace="/terminal", to=session_id)

    elapsed = round(time.time() - start, 2)
    socketio.emit(
        "agent:complete",
        {"session_id": session_id, "message": f"✅ Done - Worked for {elapsed}s"},
        namespace="/terminal",
        to=session_id,
    )
    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "response": (response or "").strip() or "Completed.",
            "commands": commands,
            "outputs": outputs,
            "cwd": cwd if os.path.isdir(cwd) else "/app/kernel",
            "elapsed_s": elapsed,
        }
    )


@terminal_bp.get("/api/terminal/audit")
def terminal_audit():
    limit_raw = request.args.get("limit", "100")
    session_id = (request.args.get("session_id") or "").strip()
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = 100
    limit = min(500, max(1, limit))

    query = TerminalCommandAudit.query.order_by(TerminalCommandAudit.created_at.desc())
    if session_id:
        query = query.filter_by(session_id=session_id)
    rows = query.limit(limit).all()

    return jsonify(
        {
            "ok": True,
            "count": len(rows),
            "rows": [
                {
                    "id": row.id,
                    "session_id": row.session_id,
                    "actor": row.actor,
                    "command": row.command,
                    "cwd": row.cwd,
                    "exit_code": row.exit_code,
                    "allowed": bool(row.allowed),
                    "blocked_reason": row.blocked_reason or "",
                    "output_preview": row.output_preview or "",
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    )
