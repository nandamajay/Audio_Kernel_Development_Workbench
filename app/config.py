"""AKDW configuration module."""
# REUSED FROM (PATTERN): Q-Build-Manager/web_manager.py and runtime env usage

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "akdw-dev-secret")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'akdw.db'}")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    HOST = os.getenv("FLASK_HOST", "0.0.0.0")
    PORT = int(os.getenv("FLASK_PORT", "5001"))
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    QGENIE_API_KEY = os.getenv("QGENIE_API_KEY", "")
    QGENIE_PROVIDER_URL = os.getenv("QGENIE_PROVIDER_URL", "https://qgenie-chat.qualcomm.com/v1")
    QGENIE_MODEL = os.getenv("QGENIE_MODEL", "")

    WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", str(BASE_DIR / "workspace"))
    UPLOADS_DIR = os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))
