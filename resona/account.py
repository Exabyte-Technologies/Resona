import re

from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .captcha import validate_captcha
from .db import get_db
from .email_verification import issue_email_verification, verification_resend_wait
from .security import login_required, require_csrf


account_bp = Blueprint("account", __name__, url_prefix="/account")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _account_context():
    pending = get_db().execute(
        "SELECT email, expires_at FROM email_verifications WHERE user_id = ? AND purpose = 'email_change' AND used_at IS NULL ORDER BY id DESC LIMIT 1",
        (g.user["id"],),
    ).fetchone()
    return {"pending_email": pending["email"] if pending else None}


def _response(message, ok=True, status=200):
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": ok, "message": message, **_account_context()}), status
    flash(message, "success" if ok else "error")
    return redirect(url_for("account.settings"))


@account_bp.get("/")
@login_required
def settings():
    return render_template("account/settings.html", **_account_context())


@account_bp.post("/")
@login_required
def update_settings():
    require_csrf()
    if not validate_captcha():
        return _response("Complete a fresh CAPTCHA verification and try again.", False, 400)
    db = get_db()
    action = request.form.get("action", "update")
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()

    if action == "resend_verification":
        wait = verification_resend_wait(user["id"])
        if wait:
            return _response(f"Please wait {wait} seconds before requesting another verification email.", False, 429)
        try:
            token = issue_email_verification(user["id"], user["display_name"] or user["username"], user["email"], "registration")
            db.commit()
            if current_app.testing:
                session["testing_verification_token"] = token
            return _response("A new verification link has been sent.")
        except Exception:
            db.rollback()
            return _response("The verification email could not be sent. Please try again later.", False, 503)

    if not display_name or len(display_name) > 80:
        return _response("Enter a display name between 1 and 80 characters.", False, 400)
    if not EMAIL_RE.fullmatch(email):
        return _response("Enter a valid email address.", False, 400)
    sensitive_change = email != user["email"] or bool(new_password)
    if sensitive_change and not check_password_hash(user["password_hash"], current_password):
        return _response("Enter your current password to change your email or password.", False, 400)
    if new_password and len(new_password) < 10:
        return _response("Use at least 10 characters for your new password.", False, 400)
    if email != user["email"] and db.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user["id"])).fetchone():
        return _response("That email address is already registered.", False, 409)

    try:
        db.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user["id"]))
        if new_password:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password, method="pbkdf2:sha256:600000"), user["id"]))
        if email != user["email"]:
            token = issue_email_verification(user["id"], display_name, email, "email_change")
            if current_app.testing:
                session["testing_verification_token"] = token
        db.commit()
    except Exception:
        db.rollback()
        return _response("Your account changes could not be saved. Please try again later.", False, 503)
    message = "Account updated. Verify the link sent to your new email before it replaces your current address." if email != user["email"] else "Account updated."
    return _response(message)
