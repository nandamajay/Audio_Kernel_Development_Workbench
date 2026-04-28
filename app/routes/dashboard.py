"""Dashboard, setup, and settings routes."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request, url_for

from app.config import (
    Config,
    MODEL_METADATA,
    get_available_models,
    get_default_model,
    get_user_display_name,
)
from app.models import ActivityLog, ConversionJob, Message, ReviewSession, Session, TriageSession, UpstreamPatch
from app.services.checkpatch_service import resolve_checkpatch_in_root, resolve_checkpatch_path
from app.services.env_service import (
    current_username,
    load_env_values,
    resolve_ssl_verify,
    save_env_values,
    validate_qgenie_key,
)


dashboard_bp = Blueprint("dashboard", __name__)


def _relative_time(value: datetime | None) -> str:
    if not value:
        return "N/A"
    now = datetime.now(timezone.utc)
    dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    diff = now - dt
    sec = int(max(0, diff.total_seconds()))
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    if sec < 86400 * 30:
        return f"{sec // 86400}d ago"
    return dt.strftime("%Y-%m-%d")


def _latest_git_activity() -> str:
    kernel_root = current_app.config.get("KERNEL_SRC_PATH", "/app/kernel")
    try:
        proc = subprocess.run(
            ["git", "-C", kernel_root, "log", "-1", "--format=%ar"],
            capture_output=True,
            text=True,
            timeout=4,
        )
        output = (proc.stdout or "").strip()
        if output:
            return output
    except Exception:
        pass
    return "No git history"


def _refresh_runtime_config(updates: dict) -> None:
    config_key_map = {
        "QGENIE_API_KEY": "QGENIE_API_KEY",
        "QGENIE_PROVIDER_URL": "QGENIE_PROVIDER_URL",
        "QGENIE_DEFAULT_MODEL": "QGENIE_DEFAULT_MODEL",
        "QGENIE_AVAILABLE_MODELS": "QGENIE_AVAILABLE_MODELS",
        "QGENIE_SSL_VERIFY": "QGENIE_SSL_VERIFY",
        "QGENIE_CA_BUNDLE": "QGENIE_CA_BUNDLE",
        "USER_DISPLAY_NAME": "USER_DISPLAY_NAME",
        "USER_EMAIL": "USER_EMAIL",
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
    return render_template(
        "dashboard.html",
        user_display_name=get_user_display_name(),
        default_model=get_default_model(),
    )


@dashboard_bp.get("/api/dashboard/stats")
def dashboard_stats():
    merged = UpstreamPatch.query.filter(UpstreamPatch.status.in_(["merged", "accepted"])).count()
    in_review = UpstreamPatch.query.filter_by(status="under_review").count()
    needs_revision = UpstreamPatch.query.filter_by(status="changes_requested").count()
    return jsonify(
        {
            "patches_reviewed": ReviewSession.query.count(),
            "drivers_converted": ConversionJob.query.count(),
            "triage_sessions": TriageSession.query.count(),
            "upstream_patches": UpstreamPatch.query.count(),
            "last_git_activity": _latest_git_activity(),
            "patch_health": {
                "merged": merged,
                "in_review": in_review,
                "needs_revision": needs_revision,
            },
        }
    )


@dashboard_bp.get("/api/dashboard/activity")
def dashboard_activity():
    events = []

    # Primary source for resumable recent activity: shared session state bus.
    recent_sessions = (
        Session.query.filter_by(page="agent")
        .order_by(Session.updated_at.desc())
        .limit(12)
        .all()
    )
    for row in recent_sessions:
        latest = (
            Message.query.filter_by(session_id=row.id)
            .order_by(Message.created_at.desc())
            .first()
        )
        preview = ""
        if latest and latest.content:
            preview = latest.content.strip().replace("\n", " ")
        if not preview:
            preview = row.name or row.id
        events.append(
            {
                "type": "agent",
                "module": "QGenie Agent",
                "session_id": row.id,
                "preview_text": preview[:160],
                "desc": f"Agent session: {preview[:72]}",
                "time": _relative_time(row.updated_at),
                "timestamp": row.updated_at.isoformat() if row.updated_at else "",
                "open_url": url_for("agent.agent_home"),
                "sort_ts": row.updated_at.timestamp() if row.updated_at else 0,
            }
        )

    for row in ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(10).all():
        events.append(
            {
                "type": row.event_type or "agent",
                "desc": row.event or "Activity",
                "time": _relative_time(row.created_at),
                "module": "AKDW",
                "session_id": None,
                "preview_text": row.event or "",
                "timestamp": row.created_at.isoformat() if row.created_at else "",
                "open_url": None,
                "sort_ts": row.created_at.timestamp() if row.created_at else 0,
            }
        )

    for row in ReviewSession.query.order_by(ReviewSession.created_at.desc()).limit(3).all():
        label = getattr(row, "patch_filename", None) or row.session_id
        events.append(
            {
                "type": "review",
                "desc": f"Reviewed {label}",
                "time": _relative_time(row.created_at),
                "module": "Patch Workshop",
                "session_id": row.session_id,
                "preview_text": str(label),
                "timestamp": row.created_at.isoformat() if row.created_at else "",
                "open_url": url_for("patchwise.patchwise_home"),
                "sort_ts": row.created_at.timestamp() if row.created_at else 0,
            }
        )

    for row in TriageSession.query.order_by(TriageSession.created_at.desc()).limit(3).all():
        desc = "Triaged crash log"
        if row.input_payload:
            first_line = (row.input_payload.splitlines() or [""])[0].strip()
            if first_line:
                desc = f"Triaged {first_line[:64]}"
        events.append(
            {
                "type": "triage",
                "desc": desc,
                "time": _relative_time(row.created_at),
                "module": "Triage",
                "session_id": None,
                "preview_text": desc,
                "timestamp": row.created_at.isoformat() if row.created_at else "",
                "open_url": url_for("triage.triage_home"),
                "sort_ts": row.created_at.timestamp() if row.created_at else 0,
            }
        )

    for row in ConversionJob.query.order_by(ConversionJob.created_at.desc()).limit(3).all():
        events.append(
            {
                "type": "convert",
                "desc": f"Converted driver ({row.conversion_type or 'generic'})",
                "time": _relative_time(row.created_at),
                "module": "Converter",
                "session_id": None,
                "preview_text": f"Converted driver ({row.conversion_type or 'generic'})",
                "timestamp": row.created_at.isoformat() if row.created_at else "",
                "open_url": url_for("converter.converter_home"),
                "sort_ts": row.created_at.timestamp() if row.created_at else 0,
            }
        )

    if not events:
        events = [
            {
                "type": "agent",
                "desc": "Dashboard loaded",
                "time": "just now",
                "module": "AKDW",
                "session_id": None,
                "preview_text": "Dashboard loaded",
                "timestamp": "",
                "open_url": None,
                "sort_ts": 2,
            },
            {
                "type": "agent",
                "desc": "Workspace initialized",
                "time": "just now",
                "module": "AKDW",
                "session_id": None,
                "preview_text": "Workspace initialized",
                "timestamp": "",
                "open_url": None,
                "sort_ts": 1,
            },
        ]

    events.sort(key=lambda item: item.get("sort_ts", 0), reverse=True)
    return jsonify([{k: v for k, v in event.items() if k != "sort_ts"} for event in events[:10]])


@dashboard_bp.get("/api/dashboard/patch_health")
def dashboard_patch_health():
    rows = ReviewSession.query.order_by(ReviewSession.updated_at.desc()).limit(5).all()
    payload = []
    for row in rows:
        summary = {}
        try:
            summary = json.loads(row.summary or "{}")
        except Exception:
            summary = {}
        critical = int(summary.get("critical", 0) or 0)
        warning = int(summary.get("warning", 0) or 0)
        suggestion = int(summary.get("suggestion", 0) or 0)
        score = max(0, 100 - (critical * 25 + warning * 10 + suggestion * 4))
        if critical > 0:
            status = "fail"
            status_text = f"{critical} critical"
        elif warning > 0:
            status = "warn"
            status_text = f"{warning} warning(s)"
        else:
            status = "pass"
            status_text = "Clean"

        payload.append(
            {
                "patch_name": getattr(row, "patch_filename", None) or row.session_id,
                "score": score,
                "status": status,
                "status_text": status_text,
                "updated_at": _relative_time(row.updated_at),
            }
        )

    return jsonify(payload)


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
    if not env_values.get("USER_EMAIL"):
        display = (env_values.get("USER_DISPLAY_NAME") or "").strip()
        env_values["USER_EMAIL"] = display if "@" in display else ""
    env_values.setdefault("QGENIE_SSL_VERIFY", "true")
    env_values.setdefault("QGENIE_CA_BUNDLE", "")
    env_values.setdefault("UPSTREAM_TRACKED_EMAILS", "[]")
    env_values.setdefault("SMTP_HOST", "localhost")
    env_values.setdefault("SMTP_PORT", "25")
    env_values.setdefault("AKDW_EMAIL_FROM", "nandam@qti.qualcomm.com")
    env_values.setdefault("SESSION_RETENTION_DAYS", "30")
    env_values.setdefault("AGENT_SHOW_THINKING", "true")
    env_values.setdefault("TOKEN_WARN_THRESHOLD", "75")
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
        "USER_EMAIL": (payload.get("user_email") or "").strip(),
        "QGENIE_DEFAULT_MODEL": (payload.get("default_model") or "auto").strip(),
        "KERNEL_SRC_PATH": (kernel_src_raw or Config.KERNEL_SRC_PATH).strip(),
        "QGENIE_SSL_VERIFY": "true"
        if str(ssl_verify_raw).lower() == "true"
        else "false",
        "QGENIE_CA_BUNDLE": (ca_bundle_raw or "").strip(),
        "EXTRA_WORKSPACE_PATHS": ",".join(normalized_extra_paths),
        "UPSTREAM_TRACKED_EMAILS": str(payload.get("upstream_tracked_emails") or "[]"),
        "SMTP_HOST": str(payload.get("smtp_host") or "localhost").strip(),
        "SMTP_PORT": str(payload.get("smtp_port") or "25").strip(),
        "AKDW_EMAIL_FROM": str(payload.get("smtp_from") or "nandam@qti.qualcomm.com").strip(),
        "SESSION_RETENTION_DAYS": str(payload.get("session_retention_days") or "30").strip(),
        "AGENT_SHOW_THINKING": "true"
        if str(payload.get("agent_show_thinking", "true")).lower() == "true"
        else "false",
        "TOKEN_WARN_THRESHOLD": str(payload.get("token_warn_threshold") or "75").strip(),
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


@dashboard_bp.get("/api/validate_checkpatch")
def validate_checkpatch():
    kernel_root = (request.args.get("path") or "").strip()
    if not kernel_root:
        kernel_root = os.environ.get("KERNEL_SRC_PATH", Config.KERNEL_SRC_PATH)
    script = resolve_checkpatch_in_root(kernel_root)
    return jsonify({"found": bool(script), "path": script or ""})


@dashboard_bp.get("/api/models")
def model_list():
    models = get_available_models()
    return jsonify(
        {
            "default": get_default_model(),
            "models": [{"name": item, "badge": MODEL_METADATA.get(item, {}).get("badge", "")} for item in models],
        }
    )
