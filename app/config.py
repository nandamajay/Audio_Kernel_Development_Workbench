"""AKDW configuration module."""
# REUSED FROM (PATTERN): Q-Build-Manager/web_manager.py env/bootstrap conventions

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

DEFAULT_MODELS = "claude-sonnet-4,qwen3,qgenie-pro,claude-haiku-4"
MODEL_METADATA: Dict[str, Dict[str, str]] = {
    "claude-sonnet-4": {"badge": "⭐ Recommended"},
    "qwen3": {"badge": "🔬 Advanced"},
    "qgenie-pro": {"badge": "🔬 Advanced"},
    "claude-haiku-4": {"badge": "⚡ Fast"},
}


def load_env(override: bool = False) -> None:
    """Load environment variables from .env if present."""
    load_dotenv(ENV_PATH, override=override)


load_env()


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "change_me_in_production")

    FLASK_ENV = os.getenv("FLASK_ENV", "development")
    HOST = os.getenv("FLASK_HOST", "0.0.0.0")
    PORT = int(os.getenv("FLASK_PORT", "5000"))
    DEBUG = os.getenv("DEBUG", "true").lower() == "true"

    QGENIE_API_KEY = os.getenv("QGENIE_API_KEY", "")
    QGENIE_PROVIDER_URL = os.getenv("QGENIE_PROVIDER_URL", "https://qgenie-chat.qualcomm.com/v1")
    QGENIE_DEFAULT_MODEL = os.getenv("QGENIE_DEFAULT_MODEL", "auto")
    QGENIE_AVAILABLE_MODELS = os.getenv("QGENIE_AVAILABLE_MODELS", DEFAULT_MODELS)
    QGENIE_SSL_VERIFY = os.getenv("QGENIE_SSL_VERIFY", "true").lower() == "true"
    QGENIE_CA_BUNDLE = os.getenv("QGENIE_CA_BUNDLE", "")

    USER_DISPLAY_NAME = os.getenv("USER_DISPLAY_NAME", "")

    HOST_WORKSPACE_PATH = os.getenv(
        "HOST_WORKSPACE_PATH",
        "/local/mnt/workspace/AUDIO_KERNEL_DEVELOPMENT_WORKBENCH(AKDW)",
    )
    WORKSPACE_PATH = os.getenv("WORKSPACE_PATH", "/app/workspace")
    KERNEL_SRC_PATH = os.getenv("KERNEL_SRC_PATH", "/app/kernel")
    EXTRA_WORKSPACE_PATHS = os.getenv("EXTRA_WORKSPACE_PATHS", "")
    ALLOWED_EXTRA_PATHS = os.getenv("ALLOWED_EXTRA_PATHS", "/local/mnt/workspace")
    PATCHES_PATH = os.getenv("PATCHES_PATH", "/app/patches")
    LOGS_PATH = os.getenv("LOGS_PATH", "/app/workspace/logs")
    SESSIONS_DB_PATH = os.getenv("SESSIONS_DB_PATH", "/app/sessions/akdw_sessions.db")

    PATCHWISE_DEFAULT_COMMITS = int(os.getenv("PATCHWISE_DEFAULT_COMMITS", "1"))
    PATCHWISE_SUBSYSTEM = os.getenv("PATCHWISE_SUBSYSTEM", "sound/soc/qcom")

    TRIAGE_AUTO_ANALYSE = os.getenv("TRIAGE_AUTO_ANALYSE", "true").lower() == "true"

    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{SESSIONS_DB_PATH}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False


def get_available_models() -> List[str]:
    raw = os.getenv("QGENIE_AVAILABLE_MODELS", DEFAULT_MODELS)
    models = [item.strip() for item in raw.split(",") if item.strip()]
    return models if models else ["claude-sonnet-4"]


def get_default_model() -> str:
    explicit = os.getenv("QGENIE_DEFAULT_MODEL", "auto").strip()
    models = get_available_models()
    if explicit == "auto":
        return models[0]
    return explicit or models[0]


def get_user_display_name() -> str:
    name = os.getenv("USER_DISPLAY_NAME", "").strip()
    if name:
        return name
    try:
        return os.getlogin()
    except OSError:
        return os.getenv("USER", "User")


def is_first_run() -> bool:
    # Containerized and CI deployments may inject env vars via env_file or runtime
    # without mounting an on-disk /app/.env file.
    return os.getenv("QGENIE_API_KEY", "").strip() == ""


def get_qgenie_verify() -> bool | str:
    verify_ssl = os.getenv("QGENIE_SSL_VERIFY", "true").lower() == "true"
    if not verify_ssl:
        return False

    ca_bundle = os.getenv("QGENIE_CA_BUNDLE", "").strip()
    if ca_bundle:
        return ca_bundle
    return True
