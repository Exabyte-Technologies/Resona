import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .captcha import require_captcha
from .email_verification import issue_email_verification, verification_record, verification_resend_wait
from .resend import resend_is_configured, send_password_reset_email, send_welcome_email
from .security import USERNAME_RE, login_required, require_csrf
from .user_controls import user_control_enabled
from .user_storage import initialize_user_storage


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _disabled(title, message):
    return render_template("auth/disabled.html", feature_title=title, feature_message=message), 403


@auth_bp.route("/register", methods=("GET", "POST"))
def register():
    if not user_control_enabled("registration"):
        return _disabled("Registration is unavailable", "New account registration has been temporarily disabled by the administrator.")
    if request.method == "POST":
        require_csrf()
        require_captcha()
        username = request.form.get("username", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        error = None
        if not USERNAME_RE.fullmatch(username):
            error = "Use 3–32 lowercase letters, numbers, underscores, or hyphens."
        elif username == "demo":
            error = "The Demo username is reserved."
        elif not display_name or len(display_name) > 80:
            error = "Enter a display name between 1 and 80 characters."
        elif "@" not in email:
            error = "Enter a valid email address."
        elif len(password) < 10:
            error = "Use at least 10 characters for your password."
        if error is None:
            db = get_db()
            try:
                cursor = db.execute(
                    "INSERT INTO users(username, email, display_name, password_hash) VALUES (?, ?, ?, ?)",
                    (username, email, display_name, generate_password_hash(password, method="pbkdf2:sha256:600000")),
                )
                verification_token = issue_email_verification(cursor.lastrowid, display_name, email, "registration")
                db.commit()
                initialize_user_storage(username)
                session.clear()
                session["csrf_token"] = secrets.token_urlsafe(32)
                if current_app.testing and not resend_is_configured():
                    session["testing_verification_token"] = verification_token
                flash("Account created. Verify your email before signing in.", "success")
                return redirect(url_for("auth.login"))
            except Exception as exc:
                db.rollback()
                if "UNIQUE constraint" in str(exc):
                    error = "That username or email is already registered."
                elif "Resend" in str(exc):
                    error = "Email verification is temporarily unavailable. Please try again later."
                else:
                    error = "We couldn't create the account. Please try again."
        flash(error, "error")
    return render_template("auth/register.html")


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    if not user_control_enabled("login"):
        return render_template("auth/login.html", sign_in_disabled=True), 403
    if request.method == "POST":
        require_csrf()
        require_captcha()
        identity = request.form.get("identity", "").strip().lower()
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ? OR email = ?", (identity, identity)
        ).fetchone()
        if user and user["password_login_enabled"] and (not user["is_demo"] or user["demo_enabled"]) and check_password_hash(user["password_hash"], request.form.get("password", "")):
            if not user["is_demo"] and not user["email_verified_at"]:
                session["pending_verification_user_id"] = user["id"]
                return render_template(
                    "auth/login.html",
                    verification_pending=True,
                    resend_wait=verification_resend_wait(user["id"]),
                ), 403
            session.clear()
            session["user_id"] = user["id"]
            session["session_version"] = user["session_version"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            destination = request.args.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"):
                destination = url_for("player.index")
            return redirect(destination)
        flash("The username or password doesn't match.", "error")
    return render_template("auth/login.html")


@auth_bp.post("/resend-verification")
def resend_verification():
    require_csrf()
    user_id = session.get("pending_verification_user_id")
    user = get_db().execute(
        "SELECT id, username, display_name, email, email_verified_at FROM users WHERE id = ?",
        (user_id,),
    ).fetchone() if user_id else None
    if not user or user["email_verified_at"]:
        session.pop("pending_verification_user_id", None)
        flash("Sign in again to request a verification email.", "error")
        return redirect(url_for("auth.login"))

    wait = verification_resend_wait(user["id"])
    if wait:
        return render_template(
            "auth/login.html",
            verification_pending=True,
            resend_wait=wait,
            verification_feedback=f"Please wait {wait} seconds before requesting another verification email.",
        ), 429

    db = get_db()
    try:
        token = issue_email_verification(
            user["id"], user["display_name"] or user["username"], user["email"], "registration"
        )
        db.commit()
        if current_app.testing and not resend_is_configured():
            session["testing_verification_token"] = token
        return render_template(
            "auth/login.html",
            verification_pending=True,
            resend_wait=60,
            verification_feedback="A new verification link has been sent. Check your inbox.",
        )
    except Exception:
        db.rollback()
        current_app.logger.exception("Could not resend the verification email for user %s", user["username"])
        return render_template(
            "auth/login.html",
            verification_pending=True,
            resend_wait=0,
            verification_feedback="The verification email could not be sent. Please try again later.",
        ), 503


@auth_bp.get("/verify-email/<token>")
def verify_email(token):
    row = verification_record(token)
    if not row:
        flash("That verification link is invalid or expired.", "error")
        return redirect(url_for("auth.login"))
    db = get_db()
    try:
        if row["purpose"] == "email_change":
            db.execute("UPDATE users SET email = ?, email_verified_at = CURRENT_TIMESTAMP WHERE id = ?", (row["email"], row["user_id"]))
        else:
            db.execute("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP WHERE id = ? AND email = ?", (row["user_id"], row["email"]))
        db.execute("UPDATE email_verifications SET used_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
        db.commit()
        if session.get("pending_verification_user_id") == row["user_id"]:
            session.pop("pending_verification_user_id", None)
        if row["purpose"] == "registration" and resend_is_configured():
            user = db.execute("SELECT username, display_name FROM users WHERE id = ?", (row["user_id"],)).fetchone()
            try:
                send_welcome_email(row["email"], user["display_name"] or user["username"])
            except Exception:
                current_app.logger.exception("Resend could not deliver the welcome email for user %s", user["username"])
        flash("Your email has been verified.", "success")
    except Exception as exc:
        db.rollback()
        if "UNIQUE constraint" in str(exc):
            flash("That email address is already registered.", "error")
        else:
            raise
    return redirect(url_for("account.settings") if session.get("user_id") == row["user_id"] else url_for("auth.login"))


@auth_bp.post("/logout")
@login_required
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot", methods=("GET", "POST"))
def forgot():
    if not user_control_enabled("password_recovery"):
        return _disabled("Password recovery is unavailable", "Password recovery has been temporarily disabled by the administrator.")
    reset_token = None
    if request.method == "POST":
        require_csrf()
        user = get_db().execute("SELECT id, username, email FROM users WHERE email = ? AND is_demo = 0 AND password_login_enabled = 1", (request.form.get("email", "").strip().lower(),)).fetchone()
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
    if not user_control_enabled("password_recovery"):
        return _disabled("Password recovery is unavailable", "Password recovery has been temporarily disabled by the administrator.")
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
        elif get_db().execute("SELECT is_demo FROM users WHERE id = ?", (row["user_id"],)).fetchone()["is_demo"]:
            flash("The Demo password can only be changed by an administrator.", "error")
            return redirect(url_for("auth.login"))
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password, method="pbkdf2:sha256:600000"), row["user_id"]))
            db.execute("UPDATE password_resets SET used_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            db.commit()
            flash("Password updated. You can sign in now.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset.html")
