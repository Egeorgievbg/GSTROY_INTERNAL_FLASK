from flask import Blueprint, render_template
from flask_login import login_required

main_bp = Blueprint("main", __name__)


@main_bp.route("/", endpoint="index")
@login_required
def index():
    return render_template("index.html")


@main_bp.route("/scanner", endpoint="scanner")
def scanner():
    return render_template("scanner.html")


@main_bp.route("/multiscanner", endpoint="multiscanner")
def multiscanner():
    return render_template("multiscanner.html")
