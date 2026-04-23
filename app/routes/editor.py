"""Editor module routes."""

from flask import Blueprint, render_template


editor_bp = Blueprint("editor", __name__, url_prefix="/editor")


@editor_bp.get("/")
def editor_home():
    return render_template("editor.html")
