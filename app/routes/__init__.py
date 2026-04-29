"""Blueprint registry for AKDW routes."""

from app.routes.agent import agent_bp
from app.routes.api import api_bp
from app.routes.converter import converter_api_bp, converter_bp
from app.routes.dashboard import dashboard_bp
from app.routes.editor import editor_bp
from app.routes.evidence import evidence_bp
from app.routes.patchwise import patchwise_bp
from app.routes.triage import triage_bp
from app.routes.upstream import upstream_bp
from app.routes.terminal_routes import terminal_bp
from app.routes.dual_agent import bp as dual_agent_bp
from app.routes.target_manager import target_manager_bp


ALL_BLUEPRINTS = [
    dashboard_bp,
    api_bp,
    evidence_bp,
    editor_bp,
    patchwise_bp,
    upstream_bp,
    converter_bp,
    converter_api_bp,
    triage_bp,
    agent_bp,
    terminal_bp,
    dual_agent_bp,
    target_manager_bp,
]
