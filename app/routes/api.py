"""Shared API endpoints used across pages."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from app.config import get_default_model
from app.services.env_service import load_env_values, save_env_values
from app.services.fs_service import list_browse_roots, list_directory, safe_path
from app.services.git_service import list_recent_commits
from app.services.activity_service import log_activity
from app.services.agent_stream_service import agent_stream_manager
from app.services.session_service import (
    active_sessions_count,
    create_session,
    create_session_id,
    ensure_session,
    get_session,
    list_sessions,
    ping_session,
)
from app.models import ConversationSession, Message, Session, db

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


@api_bp.get("/api/fs/roots")
def fs_roots():
    return jsonify({"ok": True, "roots": list_browse_roots()})


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


@api_bp.get("/api/editor/file")
def editor_file_get():
    path = request.args.get("path", "")
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed"}), 403
    if not os.path.exists(target):
        return jsonify({"ok": False, "error": "File not found"}), 404
    if os.path.isdir(target):
        return jsonify({"ok": True, "path": target, "is_dir": True, "entries": list_directory(target)})
    with open(target, "r", encoding="utf-8", errors="replace") as handle:
        return jsonify({"ok": True, "path": target, "is_dir": False, "content": handle.read()})


@api_bp.post("/api/editor/file/save")
def editor_file_save():
    payload = request.get_json() or {}
    path = payload.get("path", "")
    content = payload.get("content", "")
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed"}), 403
    if os.path.isdir(target):
        return jsonify({"ok": False, "error": "Cannot save to directory path"}), 400
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(content)
    return jsonify({"success": True, "ok": True, "path": target})


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
    files_payload = payload.get("attachments") or payload.get("files") or []
    if not message and not files_payload:
        return jsonify({"ok": False, "error": "Message or attachments required"}), 400

    session_id = (payload.get("session_id") or create_session_id()).strip()
    model = (payload.get("model") or get_default_model()).strip()
    page = (payload.get("page") or "agent").strip()

    service = current_app.extensions["agent_service"]
    result = service.stream_chat(
        session_id=session_id,
        message=message,
        model=model,
        attachments=files_payload,
        selected_code=payload.get("selected_code", ""),
        filename=payload.get("filename", ""),
        page=page,
    )
    log_activity("Agent session: " + ((message or "(attachments)")[:50]), "agent")

    return jsonify(
        {
            "ok": True,
            "session_id": session_id,
            "model": model,
            "response": result.get("response", ""),
            "content": result.get("response", ""),
            "message": result.get("response", ""),
            "notices": result.get("notices", []),
            "token_usage_estimate": result.get("token_usage_estimate", 0),
            "token_usage_max": result.get("token_usage_max", 131072),
            "prompt_token_estimate": result.get("prompt_token_estimate", 0),
        }
    )


@api_bp.post("/api/agent/stream")
def agent_chat_stream_api():
    payload = request.get_json(silent=True) or {}
    stream_id = str(payload.get("stream_id") or "").strip()
    cursor_raw = payload.get("cursor", 0)
    try:
        cursor = max(0, int(cursor_raw))
    except (TypeError, ValueError):
        cursor = 0

    if not stream_id:
        message = (payload.get("message") or payload.get("query") or "").strip()
        files_payload = payload.get("attachments") or payload.get("files") or []
        if not message and not files_payload:
            return jsonify({"ok": False, "error": "Message or attachments required"}), 400

        session_id = (payload.get("session_id") or create_session_id()).strip()
        model = (payload.get("model") or get_default_model()).strip()
        page = (payload.get("page") or "agent").strip()
        selected_code = payload.get("selected_code", "")
        filename = payload.get("filename", "")
        service = current_app.extensions["agent_service"]

        normalized_attachments = []
        for item in files_payload:
            if isinstance(item, dict):
                normalized_attachments.append(
                    {
                        "filename": item.get("filename") or item.get("name") or "attachment.txt",
                        "content": item.get("content", ""),
                    }
                )
            elif isinstance(item, str):
                normalized_attachments.append({"filename": item, "content": ""})

        ensure_session(session_id=session_id, page=page, model=model)
        app_obj = current_app._get_current_object()
        state = agent_stream_manager.start_stream(
            app=app_obj,
            agent_service=service,
            session_id=session_id,
            message=message,
            model=model,
            page=page,
            attachments=normalized_attachments,
            selected_code=selected_code,
            filename=filename,
        )
        stream_id = state.stream_id
        cursor = 0

    return Response(
        stream_with_context(agent_stream_manager.sse_iter(stream_id=stream_id, cursor=cursor)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@api_bp.post("/api/agent/stream/start")
def agent_chat_stream_start_api():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or payload.get("query") or "").strip()
    files_payload = payload.get("attachments") or payload.get("files") or []
    if not message and not files_payload:
        return jsonify({"ok": False, "error": "Message or attachments required"}), 400

    session_id = (payload.get("session_id") or create_session_id()).strip()
    model = (payload.get("model") or get_default_model()).strip()
    page = (payload.get("page") or "agent").strip()
    selected_code = payload.get("selected_code", "")
    filename = payload.get("filename", "")
    service = current_app.extensions["agent_service"]

    normalized_attachments = []
    for item in files_payload:
        if isinstance(item, dict):
            normalized_attachments.append(
                {
                    "filename": item.get("filename") or item.get("name") or "attachment.txt",
                    "content": item.get("content", ""),
                }
            )
        elif isinstance(item, str):
            normalized_attachments.append({"filename": item, "content": ""})

    ensure_session(session_id=session_id, page=page, model=model)
    app_obj = current_app._get_current_object()
    state = agent_stream_manager.start_stream(
        app=app_obj,
        agent_service=service,
        session_id=session_id,
        message=message,
        model=model,
        page=page,
        attachments=normalized_attachments,
        selected_code=selected_code,
        filename=filename,
    )
    return jsonify({"ok": True, "stream_id": state.stream_id, "cursor": 0, "session_id": session_id})


@api_bp.post("/api/agent/new_session")
def agent_new_session_api():
    payload = request.get_json(silent=True) or {}
    session_id = (payload.get("session_id") or create_session_id()).strip()
    page = (payload.get("page") or "agent").strip()

    service = current_app.extensions["agent_service"]
    service.new_session(session_id)
    return jsonify({"ok": True, "session_id": session_id, "page": page})


@api_bp.post("/api/session/create")
def create_session_api():
    payload = request.get_json(silent=True) or {}
    page = str(payload.get("page") or "agent").strip()
    name = str(payload.get("name") or "").strip() or None
    model = str(payload.get("model") or get_default_model()).strip()
    sid = create_session(page=page, name=name, model=model)
    return jsonify({"ok": True, "session_id": sid})


@api_bp.post("/api/session/ping")
def session_ping_api():
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"ok": False, "error": "session_id required"}), 400
    ok = ping_session(session_id)
    return jsonify({"ok": ok})


@api_bp.get("/api/session/<session_id>")
def session_get_api(session_id: str):
    row = get_session(session_id)
    if not row:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    return jsonify(row)


@api_bp.get("/api/session/list")
def session_list_api():
    page = request.args.get("page", "").strip() or None
    return jsonify({"ok": True, "sessions": list_sessions(page=page)})


@api_bp.get("/api/session/active_count")
def session_active_count_api():
    return jsonify({"ok": True, "active": active_sessions_count()})


@api_bp.post("/api/session/<session_id>/rename")
def session_rename_api(session_id: str):
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    row = Session.query.filter_by(id=session_id).first()
    if not row:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    row.name = name
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.delete("/api/session/<session_id>")
def session_delete_api(session_id: str):
    row = Session.query.filter_by(id=session_id).first()
    if not row:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    Message.query.filter_by(session_id=session_id).delete()
    ConversationSession.query.filter_by(id=session_id).delete()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.post("/api/editor/shell")
def editor_shell():
    payload = request.get_json() or {}
    raw_cmd = (payload.get("cmd") or "").strip()
    cwd = (payload.get("cwd") or "/app/kernel").strip()
    if not raw_cmd:
        return jsonify({"ok": False, "error": "cmd is required"}), 400

    try:
        parts = shlex.split(raw_cmd)
    except ValueError as exc:
        return jsonify({"ok": False, "error": f"Invalid command: {exc}"}), 400

    if not parts:
        return jsonify({"ok": False, "error": "cmd is required"}), 400

    allowed = {"git", "ls", "cat", "grep", "find", "checkpatch.pl", "make", "diff", "patch"}
    cmd_name = os.path.basename(parts[0])
    if cmd_name not in allowed:
        return jsonify({"ok": False, "error": "Command not permitted"}), 403

    target_cwd = safe_path(cwd)
    if not target_cwd:
        return jsonify({"ok": False, "error": "Path not allowed"}), 403
    if not os.path.isdir(target_cwd):
        return jsonify({"ok": False, "error": "Working directory not found"}), 404

    try:
        proc = subprocess.run(
            parts,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return jsonify(
            {
                "ok": True,
                "cmd": raw_cmd,
                "cwd": target_cwd,
                "output": (proc.stdout or "") + (proc.stderr or ""),
                "returncode": proc.returncode,
            }
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "") + "\n[timeout after 120s]"
        return jsonify({"ok": False, "cmd": raw_cmd, "cwd": target_cwd, "output": output, "returncode": 124})


@api_bp.post("/api/editor/mount_path")
def editor_mount_path():
    payload = request.get_json() or {}
    host_path = os.path.abspath((payload.get("host_path") or "").strip())
    if not host_path:
        return jsonify({"ok": False, "error": "host_path is required"}), 400
    if not os.path.isdir(host_path):
        return jsonify({"ok": False, "error": "Path does not exist"}), 400

    allowed_bases_raw = os.getenv("ALLOWED_EXTRA_PATHS", "/local/mnt/workspace")
    allowed_bases = [os.path.abspath(item.strip()) for item in allowed_bases_raw.split(",") if item.strip()]

    allowed = False
    for base in allowed_bases:
        try:
            if os.path.commonpath([host_path, base]) == base:
                allowed = True
                break
        except ValueError:
            continue
    if not allowed:
        return jsonify({"ok": False, "error": "Path outside allowed workspace"}), 403

    mounts_root = "/app/workspace_mounts"
    os.makedirs(mounts_root, exist_ok=True)
    base_name = os.path.basename(host_path.rstrip("/")) or "mount"
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name)[:64]
    alias_path = os.path.join(mounts_root, safe_name)
    if os.path.lexists(alias_path):
        if os.path.islink(alias_path) and os.path.realpath(alias_path) == host_path:
            pass
        else:
            suffix = abs(hash(host_path)) % 100000
            alias_path = os.path.join(mounts_root, f"{safe_name}_{suffix}")
    if not os.path.lexists(alias_path):
        os.symlink(host_path, alias_path)

    env_values = load_env_values()
    raw_existing = env_values.get("EXTRA_WORKSPACE_PATHS", "")
    existing = [item.strip() for item in raw_existing.split(",") if item.strip()]
    if alias_path not in existing:
        existing.append(alias_path)
        save_env_values({"EXTRA_WORKSPACE_PATHS": ",".join(existing)})
        current_app.config["EXTRA_WORKSPACE_PATHS"] = ",".join(existing)

    return jsonify({"ok": True, "mount_alias": alias_path, "host_path": host_path})
