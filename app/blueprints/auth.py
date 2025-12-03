from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from werkzeug.security import check_password_hash

from database import SessionLocal
from extensions import login_manager
from helpers import safe_redirect_target
from models import User

auth_bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id: str | int | None):
    if not user_id:
        return None
    session = SessionLocal()
    try:
        return session.get(User, int(user_id))
    finally:
        session.close()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    session = g.db
    error = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if not username or not password:
            error = "Моля, попълнете потребител и парола."
        else:
            normalized = username.lower()
            user = session.query(User).filter(func.lower(User.username) == normalized).first()
            if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
                error = "Невалиден потребител или парола."
            else:
                login_user(user)
                flash("Успешен вход.", "success")
                return redirect(safe_redirect_target(request.args.get("next")))
        if error:
            flash(error, "danger")
    return render_template("login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Излязохте от системата.", "info")
    return redirect(url_for("auth.login"))
