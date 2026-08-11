import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .resend import resend_is_configured, send_password_reset_email, send_welcome_email
from .security import USERNAME_RE, login_required, require_csrf
from .user_storage import initialize_user_storage


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/register", methods=("GET", "POST"))
def register():
    if request.method == "POST":
        require_csrf()
        username = request.form.get("username", "").strip().lower()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        error = None
        if not USERNAME_RE.fullmatch(username):
            error = "Use 3–32 lowercase letters, numbers, underscores, or hyphens."
        elif "@" not in email:
            error = "Enter a valid email address."
        elif len(password) < 10:
            error = "Use at least 10 characters for your password."
        if error is None:
            db = get_db()
            try:
                cursor = db.execute(
                    "INSERT INTO users(username, email, password_hash) VALUES (?, ?, ?)",
                    (username, email, generate_password_hash(password, method="pbkdf2:sha256:600000")),
                )
                db.commit()
                initialize_user_storage(username)
                if resend_is_configured():
                    try:
                        send_welcome_email(email, username)
                    except Exception:
                        current_app.logger.exception("Resend could not deliver the registration email for user %s", username)
                session.clear()
                session["user_id"] = cursor.lastrowid
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("player.index"))
            except Exception as exc:
                if "UNIQUE constraint" in str(exc):
                    error = "That username or email is already registered."
                else:
                    error = "We couldn't create the account. Please try again."
        flash(error, "error")
    return render_template("auth/register.html")


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        require_csrf()
        identity = request.form.get("identity", "").strip().lower()
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ? OR email = ?", (identity, identity)
        ).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            destination = request.args.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"):
                destination = url_for("player.index")
            return redirect(destination)
        flash("The username or password doesn't match.", "error")
    return render_template("auth/login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot", methods=("GET", "POST"))
def forgot():
    reset_token = None
    if request.method == "POST":
        require_csrf()
        user = get_db().execute("SELECT id, username, email FROM users WHERE email = ?", (request.form.get("email", "").strip().lower(),)).fetchone()
        if user:
            token = secrets.token_urlsafe(36)
            digest = hashlib.sha256(token.encode()).hexdigest()
            expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
            db = get_db()
            db.execute("INSERT INTO password_resets(user_id, token_hash, expires_at) VALUES (?, ?, ?)", (user["id"], digest, expires))
            db.commit()
            delivered = False
            if resend_is_configured():
                public_base_url = current_app.config.get("PUBLIC_BASE_URL", "").rstrip("/")
                reset_path = url_for("auth.reset", token=token)
                reset_url = public_base_url + reset_path if public_base_url else url_for("auth.reset", token=token, _external=True)
                try:
                    send_password_reset_email(user["email"], user["username"], reset_url)
                    delivered = True
                except Exception:
                    current_app.logger.exception("Resend could not deliver a password reset email for user %s", user["username"])
            if not delivered and (current_app.debug or current_app.testing):
                reset_token = token
        flash("If the address exists, password reset instructions have been sent.", "success")
    return render_template("auth/forgot.html", reset_token=reset_token)


@auth_bp.route("/reset/<token>", methods=("GET", "POST"))
def reset(token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = get_db().execute("SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL", (digest,)).fetchone()
    valid = row and datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
    if not valid:
        flash("That reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot"))
    if request.method == "POST":
        require_csrf()
        password = request.form.get("password", "")
        if len(password) < 10:
            flash("Use at least 10 characters.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password, method="pbkdf2:sha256:600000"), row["user_id"]))
            db.execute("UPDATE password_resets SET used_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            db.commit()
            flash("Password updated. You can sign in now.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html")
