import re

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .captcha import validate_captcha
from .db import get_db
from .email_verification import issue_email_verification, verification_resend_wait
from .security import login_required, require_csrf
from .user_controls import user_control_enabled


account_bp = Blueprint("account", __name__, url_prefix="/account")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _account_context():
    from .exabyte_oidc import external_identity, exabyte_is_configured

    pending = get_db().execute(
        "SELECT email, expires_at FROM email_verifications WHERE user_id = ? AND purpose = 'email_change' AND used_at IS NULL ORDER BY id DESC LIMIT 1",
        (g.user["id"],),
    ).fetchone()
    return {
        "pending_email": pending["email"] if pending else None,
        "external_identity": external_identity(g.user["id"]),
        "exabyte_configured": exabyte_is_configured(),
    }


def _response(message, ok=True, status=200):
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": ok, "message": message, **_account_context()}), status
    flash(message, "success" if ok else "error")
    return redirect(url_for("account.settings"))


@account_bp.get("/")
@login_required
def settings():
    if g.user["is_demo"]:
        return redirect(url_for("player.index"))
    if not user_control_enabled("profile_editing"):
        return render_template(
            "auth/disabled.html",
            feature_title="Profile editing is unavailable",
            feature_message="Profile and password changes have been temporarily disabled by the administrator.",
        ), 403
    return render_template("account/settings.html", **_account_context())


@account_bp.get("/exabyte-avatar")
@login_required
def exabyte_avatar():
    from .exabyte_oidc import external_identity

    identity = external_identity(g.user["id"])
    if identity is None or not identity["avatar_filename"]:
        abort(404)
    filename = identity["avatar_filename"]
    if not filename.startswith(f"{g.user['id']}.") or "/" in filename or "\\" in filename:
        abort(404)
    return send_from_directory(
        current_app.config["EXABYTE_AVATAR_ROOT"], filename,
        mimetype=identity["avatar_mime_type"], conditional=True, max_age=300,
    )


@account_bp.post("/")
@login_required
def update_settings():
    require_csrf()
    if g.user["is_demo"]:
        return jsonify({"ok": False, "message": "The Demo account is managed by an administrator and cannot be changed."}), 403
    if not user_control_enabled("profile_editing"):
        return jsonify({"ok": False, "message": "Profile editing has been disabled by the administrator."}), 403
    if not validate_captcha():
        return _response("Complete a fresh CAPTCHA verification and try again.", False, 400)
    db = get_db()
    action = request.form.get("action", "update")
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()

    identity = db.execute(
        "SELECT * FROM external_identities WHERE provider = 'exabyte' AND user_id = ? AND connection_status = 'active'", (user["id"],)
    ).fetchone()
    if identity:
        if not user["password_login_enabled"]:
            return _response("Your name, email, and sign-in credentials are managed by Exabyte Accounts.", True)
        if not new_password:
            return _response("Your Exabyte profile is synchronized automatically. Enter a new password only when changing your Resona password.", True)
        if not check_password_hash(user["password_hash"], current_password):
            return _response("Enter your current Resona password to set a new password.", False, 400)
        if len(new_password) < 10:
            return _response("Use at least 10 characters for your new password.", False, 400)
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_password, method="pbkdf2:sha256:600000"), user["id"]),
        )
        db.commit()
        return _response("Your Resona password was updated. Exabyte sign-in remains linked.")

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


@account_bp.post("/exabyte/link")
@login_required
def link_exabyte():
    from .exabyte_oidc import begin_exabyte_authorization, exabyte_is_configured, external_identity

    require_csrf()
    if not user_control_enabled("profile_editing"):
        return _response("Profile editing has been disabled by the administrator.", False, 403)
    if not exabyte_is_configured():
        return _response("Sign in with Exabyte is not configured yet.", False, 503)
    if external_identity(g.user["id"]):
        return _response("This account is already linked to Exabyte.", False, 409)
    if not validate_captcha():
        return _response("Complete a fresh CAPTCHA verification and try again.", False, 400)
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    if not user["password_login_enabled"] or not check_password_hash(user["password_hash"], request.form.get("current_password", "")):
        return _response("Enter your current Resona password before linking Exabyte.", False, 400)
    return begin_exabyte_authorization("link", g.user["id"], url_for("account.settings"))


@account_bp.post("/exabyte/unlink")
@login_required
def unlink_exabyte():
    require_csrf()
    if not user_control_enabled("profile_editing"):
        return _response("Profile editing has been disabled by the administrator.", False, 403)
    if not validate_captcha():
        return _response("Complete a fresh CAPTCHA verification and try again.", False, 400)
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (g.user["id"],)).fetchone()
    identity = db.execute("SELECT id FROM external_identities WHERE provider = 'exabyte' AND user_id = ?", (user["id"],)).fetchone()
    if not identity:
        return _response("This account is not linked to Exabyte.", False, 404)
    if not user["password_login_enabled"] or not check_password_hash(user["password_hash"], request.form.get("current_password", "")):
        return _response("Enter your current Resona password before unlinking Exabyte.", False, 400)
    db.execute("DELETE FROM external_identities WHERE id = ?", (identity["id"],))
    db.commit()
    return _response("Exabyte sign-in was unlinked. Your Resona password remains active.")
