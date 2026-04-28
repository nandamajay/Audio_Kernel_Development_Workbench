"""Editor module routes and filesystem APIs."""
# REUSED FROM (PATTERN): Q-Build-Manager/editor_manager.py endpoints

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request

from app.config import MODEL_METADATA, get_available_models, get_default_model
from app.services.fs_service import list_directory, safe_path
from app.services.session_service import create_session_id

editor_bp = Blueprint("editor", __name__, url_prefix="/editor")


ALLOWED_UPLOAD_SUFFIXES = {".c", ".h", ".dts", ".dtsi", ".patch", ".log", ".txt", ".diff"}


@editor_bp.get("/")
def editor_home():
    kernel_path = current_app.config.get("KERNEL_SRC_PATH", "/app/kernel")
    kernel_exists = os.path.isdir(kernel_path)
    return render_template(
        "editor.html",
        kernel_path=kernel_path,
        kernel_exists=kernel_exists,
        models=get_available_models(),
        model_metadata=MODEL_METADATA,
        default_model=get_default_model(),
    )


@editor_bp.get("/api/fs/tree")
def fs_tree():
    path = request.args.get("path", current_app.config.get("KERNEL_SRC_PATH", "/app/kernel"))
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed", "entries": []}), 403
    return jsonify({"ok": True, "path": target, "entries": list_directory(target)})


@editor_bp.get("/api/fs/browse")
def fs_browse():
    path = request.args.get("path", current_app.config.get("KERNEL_SRC_PATH", "/app/kernel"))
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed", "entries": []}), 403
    return jsonify({"ok": True, "path": target, "entries": list_directory(target)})


@editor_bp.get("/api/fs/read")
def fs_read():
    path = request.args.get("path", "")
    target = safe_path(path)
    if not target:
        return jsonify({"ok": False, "error": "Path not allowed"}), 403
    if not os.path.exists(target) or os.path.isdir(target):
        return jsonify({"ok": False, "error": "File not found"}), 404
    with open(target, "r", encoding="utf-8", errors="replace") as handle:
        return jsonify({"ok": True, "path": target, "content": handle.read()})


@editor_bp.post("/api/fs/write")
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


@editor_bp.post("/api/fs/upload")
def fs_upload():
    incoming = request.files.get("file")
    target_dir = request.form.get("target_dir", current_app.config.get("WORKSPACE_PATH", "/app/workspace"))
    if not incoming:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    suffix = Path(incoming.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        return jsonify({"ok": False, "error": "Unsupported file type"}), 400

    safe_dir = safe_path(target_dir)
    if not safe_dir:
        return jsonify({"ok": False, "error": "Target path not allowed"}), 403

    os.makedirs(safe_dir, exist_ok=True)
    target = os.path.join(safe_dir, incoming.filename)
    incoming.save(target)

    with open(target, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read()

    return jsonify({"ok": True, "filename": incoming.filename, "path": target, "content": content})


@editor_bp.post("/api/query")
def editor_query():
    payload = request.get_json() or {}
    session_id = payload.get("session_id") or create_session_id()
    service = current_app.extensions["agent_service"]
    result = service.stream_chat(
        session_id=session_id,
        message=payload.get("message", ""),
        model=payload.get("model", get_default_model()),
        attachments=payload.get("attachments", []),
        selected_code=payload.get("selected_code", ""),
        filename=payload.get("filename", ""),
        page="editor",
        emit_terminal=False,
    )
    return jsonify({"ok": True, "session_id": session_id, "response": result.get("response", "")})
