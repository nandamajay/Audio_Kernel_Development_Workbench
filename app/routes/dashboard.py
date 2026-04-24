"""Dashboard, setup, and settings routes."""

from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify, render_template, request, url_for

from app.config import (
    Config,
    MODEL_METADATA,
    get_available_models,
    get_default_model,
    get_user_display_name,
)
from app.models import ConversionJob, PatchRecord, TriageSession
from app.services.env_service import (
    current_username,
    load_env_values,
    resolve_ssl_verify,
    save_env_values,
    validate_qgenie_key,
)


dashboard_bp = Blueprint("dashboard", __name__)


def _refresh_runtime_config(updates: dict) -> None:
    config_key_map = {
        "QGENIE_API_KEY": "QGENIE_API_KEY",
        "QGENIE_PROVIDER_URL": "QGENIE_PROVIDER_URL",
        "QGENIE_DEFAULT_MODEL": "QGENIE_DEFAULT_MODEL",
        "QGENIE_AVAILABLE_MODELS": "QGENIE_AVAILABLE_MODELS",
        "QGENIE_SSL_VERIFY": "QGENIE_SSL_VERIFY",
        "QGENIE_CA_BUNDLE": "QGENIE_CA_BUNDLE",
        "USER_DISPLAY_NAME": "USER_DISPLAY_NAME",
        "KERNEL_SRC_PATH": "KERNEL_SRC_PATH",
        "EXTRA_WORKSPACE_PATHS": "EXTRA_WORKSPACE_PATHS",
    }
    for env_key, config_key in config_key_map.items():
        if env_key in updates:
            current_app.config[config_key] = updates[env_key]

    resolved_verify = resolve_ssl_verify(
        ssl_verify_raw=current_app.config.get("QGENIE_SSL_VERIFY", "true"),
        ca_bundle=(current_app.config.get("QGENIE_CA_BUNDLE") or "").strip(),
    )
    if isinstance(resolved_verify, str):
        os.environ["REQUESTS_CA_BUNDLE"] = resolved_verify
        os.environ["SSL_CERT_FILE"] = resolved_verify
        os.environ["CURL_CA_BUNDLE"] = resolved_verify
    else:
        os.environ.pop("REQUESTS_CA_BUNDLE", None)
        os.environ.pop("SSL_CERT_FILE", None)
        os.environ.pop("CURL_CA_BUNDLE", None)


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
        default_model=get_default_model(),
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
        ssl_verify=env_values.get("QGENIE_SSL_VERIFY", "true"),
        ca_bundle=env_values.get("QGENIE_CA_BUNDLE", ""),
    )


@dashboard_bp.post("/api/setup/validate")
def validate_setup_key():
    payload = request.get_json() or {}
    api_key = payload.get("api_key", "")
    provider_url = payload.get("provider_url", Config.QGENIE_PROVIDER_URL)
    ssl_verify = payload.get("ssl_verify", os.environ.get("QGENIE_SSL_VERIFY", "true"))
    ca_bundle = payload.get("ca_bundle", os.environ.get("QGENIE_CA_BUNDLE", ""))
    resolved_verify = resolve_ssl_verify(ssl_verify_raw=ssl_verify, ca_bundle=ca_bundle)
    if isinstance(resolved_verify, str):
        os.environ["REQUESTS_CA_BUNDLE"] = resolved_verify
        os.environ["SSL_CERT_FILE"] = resolved_verify
    elif resolved_verify is False:
        os.environ.pop("REQUESTS_CA_BUNDLE", None)
        os.environ.pop("SSL_CERT_FILE", None)
    ok, message = validate_qgenie_key(api_key, provider_url, ssl_verify=ssl_verify, ca_bundle=ca_bundle)
    return jsonify({"ok": ok, "message": message}), (200 if ok else 400)


@dashboard_bp.post("/api/setup/save")
def save_setup():
    payload = request.get_json() or {}
    api_key = payload.get("api_key", "").strip()
    provider_url = payload.get("provider_url", Config.QGENIE_PROVIDER_URL).strip()
    user_display_name = payload.get("user_display_name", "").strip() or current_username()
    ssl_verify_raw = payload.get("ssl_verify", os.environ.get("QGENIE_SSL_VERIFY", "true"))
    ssl_verify = "true" if str(ssl_verify_raw).lower() == "true" else "false"
    ca_bundle = (payload.get("ca_bundle", os.environ.get("QGENIE_CA_BUNDLE", "")) or "").strip()

    ok, message = validate_qgenie_key(api_key, provider_url, ssl_verify=ssl_verify, ca_bundle=ca_bundle)
    if not ok:
        return jsonify({"ok": False, "message": message}), 400

    updates = {
        "QGENIE_API_KEY": api_key,
        "QGENIE_PROVIDER_URL": provider_url,
        "USER_DISPLAY_NAME": user_display_name,
        "QGENIE_SSL_VERIFY": ssl_verify,
        "QGENIE_CA_BUNDLE": ca_bundle,
    }
    save_env_values(updates)
    _refresh_runtime_config(updates)
    return jsonify({"ok": True, "message": "Saved", "redirect": url_for("dashboard.dashboard")})


@dashboard_bp.get("/settings")
@dashboard_bp.get("/settings/")
def settings_page():
    env_values = load_env_values()
    models = get_available_models()
    return render_template(
        "settings.html",
        values=env_values,
        models=models,
        model_metadata=MODEL_METADATA,
        selected_model=env_values.get("QGENIE_DEFAULT_MODEL") or get_default_model(),
        extra_paths=[item.strip() for item in (env_values.get("EXTRA_WORKSPACE_PATHS", "") or "").split(",") if item.strip()],
    )


@dashboard_bp.post("/api/settings/save")
@dashboard_bp.post("/api/settings")
def save_settings():
    payload = request.get_json() or {}
    ssl_verify_raw = payload.get("ssl_verify", os.environ.get("QGENIE_SSL_VERIFY", "true"))
    ca_bundle_raw = payload.get(
        "ca_bundle",
        payload.get("ca_bundle_path", os.environ.get("QGENIE_CA_BUNDLE", "")),
    )
    display_name_raw = payload.get("user_display_name", payload.get("display_name", ""))
    kernel_src_raw = payload.get("kernel_src_path", payload.get("kernel_path", Config.KERNEL_SRC_PATH))
    extra_paths_raw = payload.get("extra_workspace_paths", [])
    if isinstance(extra_paths_raw, list):
        normalized_extra_paths = [str(item).strip() for item in extra_paths_raw if str(item).strip()]
    elif isinstance(extra_paths_raw, str):
        normalized_extra_paths = [item.strip() for item in extra_paths_raw.split(",") if item.strip()]
    else:
        normalized_extra_paths = []
    updates = {
        "USER_DISPLAY_NAME": (display_name_raw or "").strip(),
        "QGENIE_DEFAULT_MODEL": (payload.get("default_model") or "auto").strip(),
        "KERNEL_SRC_PATH": (kernel_src_raw or Config.KERNEL_SRC_PATH).strip(),
        "QGENIE_SSL_VERIFY": "true"
        if str(ssl_verify_raw).lower() == "true"
        else "false",
        "QGENIE_CA_BUNDLE": (ca_bundle_raw or "").strip(),
        "EXTRA_WORKSPACE_PATHS": ",".join(normalized_extra_paths),
    }

    api_key = (payload.get("api_key") or "").strip()
    provider_url = (payload.get("provider_url") or Config.QGENIE_PROVIDER_URL).strip()
    if api_key:
        ok, message = validate_qgenie_key(
            api_key,
            provider_url,
            ssl_verify=updates["QGENIE_SSL_VERIFY"],
            ca_bundle=updates["QGENIE_CA_BUNDLE"],
        )
        if not ok:
            return jsonify({"ok": False, "message": message}), 400
        updates["QGENIE_API_KEY"] = api_key
        updates["QGENIE_PROVIDER_URL"] = provider_url

    save_env_values(updates)
    _refresh_runtime_config(updates)
    return jsonify({"ok": True, "success": True, "message": "Settings saved"})


@dashboard_bp.get("/api/models")
def model_list():
    models = get_available_models()
    return jsonify(
        {
            "default": get_default_model(),
            "models": [{"name": item, "badge": MODEL_METADATA.get(item, {}).get("badge", "")} for item in models],
        }
    )
