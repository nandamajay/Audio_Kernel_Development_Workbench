"""AKDW Flask application factory."""
# REUSED FROM (PATTERN): Q-Build-Manager/web_manager.py create_app and SocketIO bootstrap

from __future__ import annotations

import os

from flask import Flask, redirect, request, url_for
from flask_migrate import Migrate
from flask_socketio import SocketIO

from app.config import Config, is_first_run
from app.models import db
from app.routes import ALL_BLUEPRINTS
from app.services.agent_service import AgentService
from app.services.fs_service import ensure_workspace_structure
from app.socket_handlers import register_socket_handlers


socketio = SocketIO(async_mode="eventlet", cors_allowed_origins="*")
migrate = Migrate()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    _apply_qgenie_tls_env(app)

    db.init_app(app)
    migrate.init_app(app, db)
    socketio.init_app(app)

    _ensure_directories(app)
    ensure_workspace_structure()

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    app.extensions["agent_service"] = AgentService(socketio)
    register_socket_handlers(socketio)

    @app.before_request
    def _first_run_gate():
        if request.path.startswith("/static/") or request.path.startswith("/socket.io/"):
            return None

        if request.endpoint in {
            "dashboard.health",
            "dashboard.setup_page",
            "dashboard.validate_setup_key",
            "dashboard.save_setup",
        }:
            return None

        if is_first_run():
            return redirect(url_for("dashboard.setup_page"))

        return None

    with app.app_context():
        db.create_all()

    return app


def _ensure_directories(app):
    roots = [
        app.config.get("WORKSPACE_PATH"),
        app.config.get("KERNEL_SRC_PATH"),
        app.config.get("PATCHES_PATH"),
        app.config.get("LOGS_PATH"),
        os.path.dirname(app.config.get("SESSIONS_DB_PATH", "")),
    ]
    for root in roots:
        if root:
            os.makedirs(root, exist_ok=True)


def _apply_qgenie_tls_env(app):
    verify_ssl = str(app.config.get("QGENIE_SSL_VERIFY", True)).lower() == "true"
    ca_bundle = (app.config.get("QGENIE_CA_BUNDLE") or "").strip()
    if verify_ssl and ca_bundle:
        os.environ["REQUESTS_CA_BUNDLE"] = ca_bundle
        os.environ["SSL_CERT_FILE"] = ca_bundle
    else:
        os.environ.pop("REQUESTS_CA_BUNDLE", None)
        os.environ.pop("SSL_CERT_FILE", None)
