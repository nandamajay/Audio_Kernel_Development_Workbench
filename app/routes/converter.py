"""Driver converter routes."""

from flask import Blueprint, render_template


converter_bp = Blueprint("converter", __name__, url_prefix="/converter")


@converter_bp.get("/")
def converter_home():
    return render_template("converter.html")
