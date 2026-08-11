import sqlite3

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .closeai import get_provider_settings, validate_base_url
from .db import get_db
from .resend import get_resend_settings
from .security import USERNAME_RE, admin_required, require_csrf
from .user_storage import delete_user_storage, initialize_user_storage, rename_user_storage, usage_bytes


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        require_csrf()
        identity = request.form.get("identity", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE (username = ? OR email = ?) AND is_admin = 1", (identity, identity)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            import secrets
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(url_for("admin.dashboard"))
        flash("Admin credentials not recognized.", "error")
    return render_template("admin/login.html")


@admin_bp.get("")
@admin_bp.get("/")
@admin_required
def dashboard():
    db = get_db()
    users = [dict(row) for row in db.execute("SELECT id, username, email, is_admin, created_at FROM users ORDER BY id DESC").fetchall()]
    for user in users:
        user["storage_used"] = usage_bytes(user["username"])
    skills = db.execute("SELECT skills.*, users.username FROM skills LEFT JOIN users ON users.id = skills.user_id ORDER BY skills.id DESC").fetchall()
    prompt = db.execute("SELECT value FROM settings WHERE key = 'agent_system_prompt'").fetchone()["value"]
    provider_settings = get_provider_settings()
    provider = {
        "base_url": provider_settings["base_url"],
        "model": provider_settings["model"],
        "key_source": provider_settings["key_source"],
        "key_configured": bool(provider_settings["api_key"]),
        "agent_max_steps": int((db.execute("SELECT value FROM settings WHERE key = 'agent_max_steps'").fetchone()["value"] or current_app.config["AGENT_MAX_STEPS"])),
    }
    resend_settings = get_resend_settings()
    resend = {
        "from_email": resend_settings["from_email"],
        "from_name": resend_settings["from_name"],
        "key_source": resend_settings["key_source"],
        "key_configured": bool(resend_settings["api_key"]),
        "ready": bool(resend_settings["api_key"] and resend_settings["from_email"]),
    }
    stats = {
        "users": len(users),
        "storage": sum(user["storage_used"] for user in users),
        "runs": db.execute("SELECT COUNT(*) AS count FROM agent_runs").fetchone()["count"],
        "failures": db.execute("SELECT COUNT(*) AS count FROM agent_runs WHERE status = 'failed'").fetchone()["count"],
    }
    return render_template("admin/dashboard.html", users=users, skills=skills, system_prompt=prompt, stats=stats, provider=provider, resend=resend)


@admin_bp.post("/users/<int:user_id>/edit")
@admin_required
def edit_user(user_id):
    require_csrf()
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)

    username = request.form.get("username", "").strip().lower()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    is_admin = request.form.get("is_admin") == "1"
    if not USERNAME_RE.fullmatch(username):
        flash("Use 3–32 lowercase letters, numbers, underscores, or hyphens.", "error")
        return redirect(url_for("admin.dashboard", user=user_id))
    if "@" not in email or len(email) > 254:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("admin.dashboard", user=user_id))
    if password and len(password) < 10:
        flash("A replacement password must contain at least 10 characters.", "error")
        return redirect(url_for("admin.dashboard", user=user_id))
    if user["is_admin"] and not is_admin:
        admin_count = db.execute("SELECT COUNT(*) AS count FROM users WHERE is_admin = 1").fetchone()["count"]
        if admin_count <= 1:
            flash("Resona must retain at least one administrator.", "error")
            return redirect(url_for("admin.dashboard", user=user_id))

    old_username = user["username"]
    try:
        assignments = ["username = ?", "email = ?", "is_admin = ?"]
        values = [username, email, int(is_admin)]
        if password:
            assignments.append("password_hash = ?")
            values.append(generate_password_hash(password, method="pbkdf2:sha256:600000"))
        values.append(user_id)
        db.execute(f"UPDATE users SET {', '.join(assignments)} WHERE id = ?", values)
        if username != old_username:
            rename_user_storage(old_username, username)
        db.commit()
    except (sqlite3.IntegrityError, FileExistsError):
        db.rollback()
        flash("That username or email is already in use.", "error")
        return redirect(url_for("admin.dashboard", user=user_id))
    except OSError:
        db.rollback()
        flash("The private workspace could not be renamed, so no account changes were saved.", "error")
        return redirect(url_for("admin.dashboard", user=user_id))

    flash(f"{username}'s account was updated.", "success")
    if user_id == g.user["id"] and not is_admin:
        session.clear()
        return redirect(url_for("auth.login"))
    return redirect(url_for("admin.dashboard", user=user_id))


@admin_bp.post("/users/<int:user_id>/delete")
@admin_required
def delete_user(user_id):
    require_csrf()
    db = get_db()
    user = db.execute("SELECT id, username, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        abort(404)
    if request.form.get("confirm_username", "").strip().lower() != user["username"]:
        flash(f"Type {user['username']} exactly to confirm deletion.", "error")
        return redirect(url_for("admin.dashboard", user=user_id))
    if user["is_admin"]:
        admin_count = db.execute("SELECT COUNT(*) AS count FROM users WHERE is_admin = 1").fetchone()["count"]
        if admin_count <= 1:
            flash("The final administrator cannot be deleted.", "error")
            return redirect(url_for("admin.dashboard", user=user_id))

    username = user["username"]
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    try:
        delete_user_storage(username)
    except OSError:
        current_app.logger.exception("Could not remove deleted user workspace for %s", username)
        flash(f"{username}'s account was deleted, but its workspace requires manual cleanup.", "error")
    else:
        flash(f"{username}'s account and private workspace were deleted.", "success")

    if user_id == g.user["id"]:
        session.clear()
        return redirect(url_for("admin.login"))
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/provider")
@admin_required
def update_provider():
    require_csrf()
    base_url = request.form.get("base_url", "").strip()
    model = request.form.get("model", "").strip()
    api_key = request.form.get("api_key", "").strip()
    clear_key = request.form.get("clear_api_key") == "1"
    max_steps = request.form.get("agent_max_steps", "").strip()
    try:
        validate_base_url(base_url)
        if not model or len(model) > 120:
            raise ValueError("Enter a valid provider model")
        if not max_steps.isdigit() or not 1 <= int(max_steps) <= 200:
            raise ValueError("Agent step limit must be between 1 and 200")
        db = get_db()
        db.execute("INSERT INTO settings(key, value) VALUES ('closeai_base_url', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (base_url.rstrip("/"),))
        db.execute("INSERT INTO settings(key, value) VALUES ('closeai_model', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (model,))
        db.execute("INSERT INTO settings(key, value) VALUES ('agent_max_steps', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (max_steps,))
        if api_key or clear_key:
            db.execute("INSERT INTO settings(key, value) VALUES ('closeai_api_key', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", ("" if clear_key else api_key,))
        db.commit()
        flash("Server provider settings updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/resend")
@admin_required
def update_resend():
    require_csrf()
    from_email = request.form.get("from_email", "").strip().lower()
    from_name = request.form.get("from_name", "").strip()
    api_key = request.form.get("api_key", "").strip()
    clear_key = request.form.get("clear_api_key") == "1"
    if "@" not in from_email or len(from_email) > 254:
        flash("Enter a valid Resend sender email address.", "error")
        return redirect(url_for("admin.dashboard"))
    if not from_name or len(from_name) > 100:
        flash("Enter a sender name of no more than 100 characters.", "error")
        return redirect(url_for("admin.dashboard"))
    if api_key and (not api_key.startswith("re_") or len(api_key) > 250):
        flash("Enter a valid Resend API key beginning with re_.", "error")
        return redirect(url_for("admin.dashboard"))

    db = get_db()
    db.execute("INSERT INTO settings(key, value) VALUES ('resend_from_email', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (from_email,))
    db.execute("INSERT INTO settings(key, value) VALUES ('resend_from_name', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", (from_name,))
    if api_key or clear_key:
        db.execute("INSERT INTO settings(key, value) VALUES ('resend_api_key', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP", ("" if clear_key else api_key,))
    db.commit()
    flash("Resend email settings updated.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/prompt")
@admin_required
def update_prompt():
    require_csrf()
    value = request.form.get("system_prompt", "").strip()
    if not value:
        flash("The system prompt cannot be empty.", "error")
    else:
        db = get_db()
        db.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'agent_system_prompt'", (value,))
        db.commit()
        flash("Agent prompt updated.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/skills")
@admin_required
def add_skill():
    require_csrf()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    scope = request.form.get("scope", "global")
    user_id = request.form.get("user_id") or None
    if not name or not description or scope not in {"global", "user"}:
        flash("Skill name, description, and a valid scope are required.", "error")
    else:
        db = get_db()
        db.execute("INSERT INTO skills(name, description, endpoint, scope, user_id) VALUES (?, ?, ?, ?, ?)", (name, description, request.form.get("endpoint", "").strip() or None, scope, user_id if scope == "user" else None))
        db.commit()
        flash("Skill registered.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.post("/admins")
@admin_required
def add_admin():
    require_csrf()
    username = request.form.get("username", "").strip().lower()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not USERNAME_RE.fullmatch(username) or "@" not in email or len(password) < 10:
        flash("Enter a valid username, email, and password of at least 10 characters.", "error")
    else:
        db = get_db()
        cursor = db.execute("INSERT INTO users(username, email, password_hash, is_admin) VALUES (?, ?, ?, 1)", (username, email, generate_password_hash(password, method="pbkdf2:sha256:600000")))
        db.commit()
        initialize_user_storage(username)
        flash("Admin created.", "success")
    return redirect(url_for("admin.dashboard"))
