"""Simple settings facade backed by .env values."""

from __future__ import annotations

import json
from typing import Any

from app.services.env_service import load_env_values, save_env_values

SETTING_ENV_MAP = {
    "user_email": "USER_EMAIL",
    "upstream_tracked_emails": "UPSTREAM_TRACKED_EMAILS",
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_from": "AKDW_EMAIL_FROM",
    "session_retention_days": "SESSION_RETENTION_DAYS",
    "agent_show_thinking": "AGENT_SHOW_THINKING",
    "token_warn_threshold": "TOKEN_WARN_THRESHOLD",
}

SETTING_DEFAULTS = {
    "user_email": "",
    "upstream_tracked_emails": "[]",
    "smtp_host": "localhost",
    "smtp_port": "25",
    "smtp_from": "nandam@qti.qualcomm.com",
    "session_retention_days": "30",
    "agent_show_thinking": "true",
    "token_warn_threshold": "75",
}


def _env_key(key: str) -> str:
    return SETTING_ENV_MAP.get(key, key.upper())


def get_setting(key: str, default: str | None = None) -> str:
    values = load_env_values()
    env_key = _env_key(key)
    if env_key in values and values[env_key] is not None:
        return str(values[env_key])
    if default is not None:
        return str(default)
    return str(SETTING_DEFAULTS.get(key, ""))


def save_setting(key: str, value: Any) -> None:
    env_key = _env_key(key)
    save_env_values({env_key: str(value)})


def get_json_setting(key: str, default: Any) -> Any:
    raw = get_setting(key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default
