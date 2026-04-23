"""Patch workshop routes."""

from flask import Blueprint, render_template


patchwise_bp = Blueprint("patchwise", __name__, url_prefix="/patchwise")


@patchwise_bp.get("/")
def patchwise_home():
    return render_template("patchwise.html")
