"""AKDW Flask application factory."""
# REUSED FROM (PATTERN): Q-Build-Manager/web_manager.py create_app and SocketIO bootstrap

import os

from flask import Flask
from flask_migrate import Migrate
from flask_socketio import SocketIO

from app.config import Config
from app.models import db
from app.routes import ALL_BLUEPRINTS
from app.socket_handlers import register_socket_handlers


socketio = SocketIO(async_mode="eventlet", cors_allowed_origins="*")
migrate = Migrate()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    socketio.init_app(app)

    _ensure_directories(app)

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    register_socket_handlers(socketio)

    with app.app_context():
        db.create_all()

    return app


def _ensure_directories(app):
    upload_root = app.config.get("UPLOADS_DIR")
    workspace_root = app.config.get("WORKSPACE_DIR")
    for directory in [
        upload_root,
        os.path.join(upload_root, "patches") if upload_root else None,
        os.path.join(upload_root, "logs") if upload_root else None,
        os.path.join(upload_root, "drivers") if upload_root else None,
        workspace_root,
    ]:
        if directory:
            os.makedirs(directory, exist_ok=True)
