"""Shared API endpoints used across pages."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from flask import Blueprint, jsonify, request

from app.config import get_default_model
from app.services.fs_service import list_directory, safe_path
from app.services.git_service import list_recent_commits
from app.services.session_service import create_session_id

api_bp = Blueprint("api", __name__)


@api_bp.get("/api/fs/browse")
def fs_browse():
    path = request.args.get("path", "/app/kernel")
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed", "entries": []}), 403
    return jsonify({"ok": True, "path": target, "entries": list_directory(target)})


@api_bp.get("/api/fs/tree")
def fs_tree():
    path = request.args.get("path", "/app/kernel")
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed", "entries": []}), 403
    return jsonify({"ok": True, "path": target, "entries": list_directory(target)})


@api_bp.get("/api/fs/read")
def fs_read():
    path = request.args.get("path", "")
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed"}), 403
    if not os.path.exists(target) or os.path.isdir(target):
        return jsonify({"ok": False, "error": "File not found"}), 404
    with open(target, "r", encoding="utf-8", errors="replace") as handle:
        return jsonify({"ok": True, "path": target, "content": handle.read()})


@api_bp.post("/api/fs/write")
def fs_write():
    payload = request.get_json() or {}
    path = payload.get("path", "")
    content = payload.get("content", "")
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed"}), 403
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(content)
    return jsonify({"ok": True, "path": target})


@api_bp.get("/api/git/commits")
def git_commits():
    n = request.args.get("n", "1")
    try:
        count = max(1, int(n))
    except ValueError:
        count = 1

    cwd = request.args.get("cwd", "/app/kernel")
    target = safe_path(cwd) or cwd
    commits = list_recent_commits(cwd=target, n=count)
    return jsonify({"ok": True, "commits": commits})


@api_bp.post("/api/agent/chat")
def agent_chat_api():
    payload = request.get_json() or {}
    message = (payload.get("message") or "").strip()
    if not message and not payload.get("attachments"):
        return jsonify({"ok": False, "error": "Message or attachments required"}), 400

    from flask import current_app

    session_id = (payload.get("session_id") or create_session_id()).strip()
    model = (payload.get("model") or get_default_model()).strip()
    page = (payload.get("page") or "agent").strip()

    service = current_app.extensions["agent_service"]
    result = service.stream_chat(
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
            "content": result.get("response", ""),
            "message": result.get("response", ""),
        }
    )


@api_bp.post("/api/agent/new_session")
def agent_new_session_api():
    payload = request.get_json(silent=True) or {}
    session_id = (payload.get("session_id") or create_session_id()).strip()
    page = (payload.get("page") or "agent").strip()

    from flask import current_app

    service = current_app.extensions["agent_service"]
    service.new_session(session_id)
    return jsonify({"ok": True, "session_id": session_id, "page": page})
