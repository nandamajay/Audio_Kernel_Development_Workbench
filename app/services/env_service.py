"""Helpers for reading/writing .env values and validating API key."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Tuple

import requests
from requests.exceptions import SSLError
from dotenv import dotenv_values, set_key

from app.config import ENV_PATH, get_qgenie_verify, load_env


def _resolve_verify(
    ssl_verify: str | bool | None = None,
    ca_bundle: str | None = None,
) -> bool | str:
    if ssl_verify is None and ca_bundle is None:
        return get_qgenie_verify()

    verify_ssl = True
    if isinstance(ssl_verify, bool):
        verify_ssl = ssl_verify
    elif ssl_verify is not None:
        verify_ssl = str(ssl_verify).lower() == "true"

    if not verify_ssl:
        return False

    bundle = (ca_bundle or "").strip()
    if bundle:
        return bundle
    return True


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


def validate_qgenie_key(
    api_key: str,
    provider_url: str,
    ssl_verify: str | bool | None = None,
    ca_bundle: str | None = None,
) -> Tuple[bool, str]:
    if not api_key.strip():
        return False, "API key cannot be empty"

    url = provider_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    verify = _resolve_verify(ssl_verify=ssl_verify, ca_bundle=ca_bundle)

    if isinstance(verify, str):
        bundle = Path(verify)
        if not bundle.exists():
            return False, f"CA bundle path not found: {verify}"

    try:
        response = requests.get(url, headers=headers, timeout=8, verify=verify)
    except SSLError as exc:
        return (
            False,
            "SSL verification failed. Configure QGENIE_CA_BUNDLE or set "
            "QGENIE_SSL_VERIFY=false for internal testing. "
            f"Details: {exc}",
        )
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
