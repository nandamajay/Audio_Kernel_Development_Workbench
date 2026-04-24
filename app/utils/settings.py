"""Simple settings helper for agent modules."""

from __future__ import annotations

import os


def get_settings() -> dict:
    return {
        "api_key": os.getenv("QGENIE_API_KEY", "").strip(),
        "provider_url": os.getenv("QGENIE_PROVIDER_URL", "https://qgenie-chat.qualcomm.com/v1").strip(),
        "default_model": os.getenv("QGENIE_DEFAULT_MODEL", "claude-sonnet-4").strip() or "claude-sonnet-4",
    }
