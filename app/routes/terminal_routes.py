"""Terminal-IDE API routes for terminal sessions and agent mode."""

from __future__ import annotations

import os
import time

from flask import Blueprint, current_app, jsonify, request

from app.config import get_default_model
from app.services.terminal_service import terminal_service


terminal_bp = Blueprint("terminal", __name__)


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
    for cmd in commands:
        socketio.emit("agent:tool_call", {"session_id": session_id, "message": f"⚡ Running: {cmd}"}, namespace="/terminal", to=session_id)
        out = terminal_service.execute_safe_command(session_id, cmd, cwd=cwd)
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
