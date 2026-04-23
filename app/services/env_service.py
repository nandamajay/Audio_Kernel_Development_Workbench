"""Helpers for reading/writing .env values and validating API key."""

from __future__ import annotations

import os
from typing import Dict, Tuple

import requests
from dotenv import dotenv_values, set_key

from app.config import ENV_PATH, load_env


def load_env_values() -> Dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    raw = dotenv_values(str(ENV_PATH))
    return {k: (v or "") for k, v in raw.items()}


def save_env_values(values: Dict[str, str]) -> None:
    ENV_PATH.touch(exist_ok=True)
    for key, value in values.items():
        set_key(str(ENV_PATH), key, value)
        os.environ[key] = value
    load_env(override=True)


def validate_qgenie_key(api_key: str, provider_url: str) -> Tuple[bool, str]:
    if not api_key.strip():
        return False, "API key cannot be empty"

    url = provider_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = requests.get(url, headers=headers, timeout=8)
    except Exception as exc:
        return False, f"Validation request failed: {exc}"

    if response.status_code == 200:
        return True, "API key validated successfully"
    if response.status_code in (401, 403):
        return False, "API key rejected by provider"

    return False, f"Provider returned {response.status_code}"


def current_username() -> str:
    try:
        return os.getlogin()
    except OSError:
        return os.getenv("USER", "user")
