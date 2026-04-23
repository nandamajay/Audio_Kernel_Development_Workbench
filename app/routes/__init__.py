"""Blueprint registry for AKDW routes."""

from app.routes.agent import agent_bp
from app.routes.api import api_bp
from app.routes.converter import converter_bp
from app.routes.dashboard import dashboard_bp
from app.routes.editor import editor_bp
from app.routes.patchwise import patchwise_bp
from app.routes.triage import triage_bp


ALL_BLUEPRINTS = [
    dashboard_bp,
    api_bp,
    editor_bp,
    patchwise_bp,
    converter_bp,
    triage_bp,
    agent_bp,
]
