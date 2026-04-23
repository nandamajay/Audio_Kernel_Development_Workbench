"""Dashboard, setup, and settings routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request, url_for

from app.config import (
    Config,
    MODEL_METADATA,
    get_available_models,
    get_default_model,
    get_user_display_name,
)
from app.models import ConversionJob, PatchRecord, TriageSession
from app.services.env_service import current_username, load_env_values, save_env_values, validate_qgenie_key


dashboard_bp = Blueprint("dashboard", __name__)


def _refresh_runtime_config(updates: dict) -> None:
    config_key_map = {
        "QGENIE_API_KEY": "QGENIE_API_KEY",
        "QGENIE_PROVIDER_URL": "QGENIE_PROVIDER_URL",
        "QGENIE_DEFAULT_MODEL": "QGENIE_DEFAULT_MODEL",
        "QGENIE_AVAILABLE_MODELS": "QGENIE_AVAILABLE_MODELS",
        "USER_DISPLAY_NAME": "USER_DISPLAY_NAME",
        "KERNEL_SRC_PATH": "KERNEL_SRC_PATH",
    }
    for env_key, config_key in config_key_map.items():
        if env_key in updates:
            current_app.config[config_key] = updates[env_key]


@dashboard_bp.get("/")
def dashboard():
    stats = {
        "patches_reviewed": PatchRecord.query.count(),
        "drivers_converted": ConversionJob.query.count(),
        "triage_sessions": TriageSession.query.count(),
        "last_git_activity": "No activity yet",
    }
    recent_activity = [
        {"label": "Workspace initialized", "timestamp": "just now"},
        {"label": "Dashboard loaded", "timestamp": "just now"},
    ]
    return render_template(
        "dashboard.html",
        stats=stats,
        recent_activity=recent_activity,
        user_display_name=get_user_display_name(),
    )


@dashboard_bp.get("/health")
def health():
    return {"status": "ok", "service": "akdw", "port": Config.PORT}, 200


@dashboard_bp.get("/setup")
def setup_page():
    env_values = load_env_values()
    return render_template(
        "setup.html",
        default_username=env_values.get("USER_DISPLAY_NAME") or current_username(),
        provider_url=env_values.get("QGENIE_PROVIDER_URL") or Config.QGENIE_PROVIDER_URL,
    )


@dashboard_bp.post("/api/setup/validate")
def validate_setup_key():
    payload = request.get_json() or {}
    api_key = payload.get("api_key", "")
    provider_url = payload.get("provider_url", Config.QGENIE_PROVIDER_URL)
    ok, message = validate_qgenie_key(api_key, provider_url)
    return jsonify({"ok": ok, "message": message}), (200 if ok else 400)


@dashboard_bp.post("/api/setup/save")
def save_setup():
    payload = request.get_json() or {}
    api_key = payload.get("api_key", "").strip()
    provider_url = payload.get("provider_url", Config.QGENIE_PROVIDER_URL).strip()
    user_display_name = payload.get("user_display_name", "").strip() or current_username()

    ok, message = validate_qgenie_key(api_key, provider_url)
    if not ok:
        return jsonify({"ok": False, "message": message}), 400

    updates = {
        "QGENIE_API_KEY": api_key,
        "QGENIE_PROVIDER_URL": provider_url,
        "USER_DISPLAY_NAME": user_display_name,
    }
    save_env_values(updates)
    _refresh_runtime_config(updates)
    return jsonify({"ok": True, "message": "Saved", "redirect": url_for("dashboard.dashboard")})


@dashboard_bp.get("/settings")
def settings_page():
    env_values = load_env_values()
    models = get_available_models()
    return render_template(
        "settings.html",
        values=env_values,
        models=models,
        model_metadata=MODEL_METADATA,
        selected_model=env_values.get("QGENIE_DEFAULT_MODEL") or get_default_model(),
    )


@dashboard_bp.post("/api/settings/save")
def save_settings():
    payload = request.get_json() or {}
    updates = {
        "USER_DISPLAY_NAME": (payload.get("user_display_name") or "").strip(),
        "QGENIE_DEFAULT_MODEL": (payload.get("default_model") or "auto").strip(),
        "KERNEL_SRC_PATH": (payload.get("kernel_src_path") or Config.KERNEL_SRC_PATH).strip(),
    }

    api_key = (payload.get("api_key") or "").strip()
    provider_url = (payload.get("provider_url") or Config.QGENIE_PROVIDER_URL).strip()
    if api_key:
        ok, message = validate_qgenie_key(api_key, provider_url)
        if not ok:
            return jsonify({"ok": False, "message": message}), 400
        updates["QGENIE_API_KEY"] = api_key
        updates["QGENIE_PROVIDER_URL"] = provider_url

    save_env_values(updates)
    _refresh_runtime_config(updates)
    return jsonify({"ok": True, "message": "Settings saved"})


@dashboard_bp.get("/api/models")
def model_list():
    models = get_available_models()
    return jsonify(
        {
            "default": get_default_model(),
            "models": [{"name": item, "badge": MODEL_METADATA.get(item, {}).get("badge", "")} for item in models],
        }
    )
