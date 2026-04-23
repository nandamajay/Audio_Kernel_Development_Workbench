import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app as app_package
from app import create_app
from app.models import db


@pytest.fixture
def app(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    kernel = tmp_path / "kernel"
    patches = tmp_path / "patches"
    sessions = tmp_path / "sessions"
    logs = workspace / "logs"

    workspace.mkdir(parents=True, exist_ok=True)
    kernel.mkdir(parents=True, exist_ok=True)
    patches.mkdir(parents=True, exist_ok=True)
    sessions.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)

    db_path = sessions / "test_sessions.db"

    monkeypatch.setenv("QGENIE_API_KEY", "test-key")
    monkeypatch.setenv("QGENIE_PROVIDER_URL", "https://example.invalid/v1")
    monkeypatch.setenv("QGENIE_DEFAULT_MODEL", "auto")
    monkeypatch.setenv("QGENIE_AVAILABLE_MODELS", "claude-sonnet-4,qwen3")
    monkeypatch.setenv("USER_DISPLAY_NAME", "Ajay")
    monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
    monkeypatch.setenv("KERNEL_SRC_PATH", str(kernel))
    monkeypatch.setenv("PATCHES_PATH", str(patches))
    monkeypatch.setenv("LOGS_PATH", str(logs))
    monkeypatch.setenv("SESSIONS_DB_PATH", str(db_path))

    # Keep route tests focused on route behavior, not first-run redirection.
    monkeypatch.setattr(app_package, "is_first_run", lambda: False)

    class TestConfig:
        TESTING = True
        SECRET_KEY = "test-secret"
        FLASK_ENV = "testing"
        HOST = "127.0.0.1"
        PORT = 5000
        DEBUG = True
        WORKSPACE_PATH = str(workspace)
        KERNEL_SRC_PATH = str(kernel)
        PATCHES_PATH = str(patches)
        LOGS_PATH = str(logs)
        SESSIONS_DB_PATH = str(db_path)
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    flask_app = create_app(TestConfig)

    with flask_app.app_context():
        db.drop_all()
        db.create_all()

    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()
