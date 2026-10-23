from __future__ import annotations

import hmac
import sqlite3
import secrets
from functools import wraps
from typing import Any, Callable

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db


auth_bp = Blueprint("auth", __name__)


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def validate_csrf() -> None:
    submitted = request.form.get("_csrf_token") or request.headers.get("X-CSRFToken")
    expected = session.get("_csrf_token")
    if not expected or not submitted or not hmac.compare_digest(submitted, expected):
        abort(400, description="Invalid or missing CSRF token.")


@auth_bp.before_app_request
def csrf_protect() -> None:
    if request.method == "POST":
        validate_csrf()


def load_logged_in_user() -> None:
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        return
    g.user = get_db().execute(
        "SELECT id, email, full_name FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if g.user is None:
        session.clear()


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped_view(**kwargs: Any) -> Any:
        if g.user is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)

    return wrapped_view


def normalize_email(value: str) -> str:
    return value.strip().lower()


def safe_next_url(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return url_for("main.dashboard")


@auth_bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user is not None:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        confirmation = request.form.get("confirmation", "")
        error = None
        if len(full_name) < 2 or len(full_name) > 100:
            error = "Enter a name between 2 and 100 characters."
        elif "@" not in email or len(email) > 254:
            error = "Enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirmation:
            error = "Passwords do not match."
        if error is None:
            try:
                db = get_db()
                db.execute(
                    "INSERT INTO users (email, password_hash, full_name) VALUES (?, ?, ?)",
                    (email, generate_password_hash(password), full_name),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed: users.email" in str(exc):
                    error = "An account with that email already exists."
                else:
                    raise
        if error:
            flash(error, "error")
        else:
            flash("Account created. Please log in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html")


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user is not None:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = normalize_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["_csrf_token"] = secrets.token_urlsafe(32)
            return redirect(safe_next_url(request.args.get("next")))
    return render_template("auth/login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
