"""Patch workshop routes."""

from flask import Blueprint, render_template

from app.config import MODEL_METADATA, get_available_models, get_default_model


patchwise_bp = Blueprint("patchwise", __name__, url_prefix="/patchwise")


@patchwise_bp.get("/")
def patchwise_home():
    return render_template(
        "patchwise.html",
        models=get_available_models(),
        model_metadata=MODEL_METADATA,
        default_model=get_default_model(),
    )
