"""Helpers for reading/writing .env values and validating API key."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Tuple

import requests
from requests.exceptions import SSLError
from dotenv import dotenv_values, set_key

from app.config import ENV_PATH, load_env


def resolve_ssl_verify(
    ssl_verify_raw: str | bool | None = None,
    ca_bundle: str | None = None,
) -> bool | str:
    """
    Resolve TLS verification mode at request time using runtime env/config.
    """
    verify_source = ssl_verify_raw
    if verify_source is None:
        verify_source = os.environ.get("QGENIE_SSL_VERIFY", "true")
    bundle_source = ca_bundle
    if bundle_source is None:
        bundle_source = os.environ.get("QGENIE_CA_BUNDLE", "")

    if str(verify_source).lower() == "false":
        return False

    bundle = (bundle_source or "").strip()
    if bundle and os.path.exists(bundle):
        return bundle

    return True


def _resolve_verify(
    ssl_verify: str | bool | None = None,
    ca_bundle: str | None = None,
) -> bool | str:
    return resolve_ssl_verify(ssl_verify_raw=ssl_verify, ca_bundle=ca_bundle)


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

    if str(ssl_verify).lower() != "false":
        explicit_bundle = (ca_bundle or "").strip()
        if explicit_bundle and not Path(explicit_bundle).exists():
            return False, f"CA bundle path not found: {explicit_bundle}"

    verify = _resolve_verify(ssl_verify=ssl_verify, ca_bundle=ca_bundle)

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
