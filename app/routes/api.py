"""Shared API endpoints used across pages."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any, Dict, List

from flask import Blueprint, current_app, jsonify, request

from app.config import get_default_model
from app.services.env_service import load_env_values, save_env_values
from app.services.fs_service import list_browse_roots, list_directory, safe_path
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

    service = current_app.extensions["agent_service"]
    service.new_session(session_id)
    return jsonify({"ok": True, "session_id": session_id, "page": page})


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
