import json

from flask import Blueprint, abort, g, jsonify, render_template, request

from .db import get_db
from .security import login_required, require_csrf
from .user_storage import ensure_chord_model_assets, migrate_legacy_default_workspace, safe_path, usage_bytes, user_root, validate_nav


player_bp = Blueprint("player", __name__, url_prefix="/player")


@player_bp.get("")
@player_bp.get("/")
@login_required
def index():
    ensure_chord_model_assets(g.user["username"])
    migrate_legacy_default_workspace(g.user["username"])
    nav_path = safe_path(g.user["username"], "nav.json")
    try:
        nav = validate_nav(json.loads(nav_path.read_text(encoding="utf-8")))
        for item in nav["nav_items"]:
            if str(item.get("icon_path", "")).startswith("/storage/"):
                item["icon_path"] = "/".join(item["icon_path"].split("/")[3:])
    except (ValueError, json.JSONDecodeError, OSError):
        abort(500, "Navigation is invalid. Ask an admin to restore your last snapshot.")
    return render_template("player/index.html", nav=nav)


@player_bp.get("/api/history")
@login_required
def history():
    rows = get_db().execute(
        "SELECT id, title, config_json, duration_seconds, created_at FROM playback_history WHERE user_id = ? ORDER BY id DESC LIMIT 50",
        (g.user["id"],),
    ).fetchall()
    return jsonify([{**dict(row), "config": json.loads(row["config_json"])} for row in rows])


@player_bp.post("/api/history")
@login_required
def save_history():
    require_csrf()
    data = request.get_json(silent=True) or {}
    config = data.get("config", {})
    if not isinstance(config, dict):
        abort(400, "Invalid playback configuration")
    db = get_db()
    db.execute(
        "INSERT INTO playback_history(user_id, title, config_json, duration_seconds) VALUES (?, ?, ?, ?)",
        (g.user["id"], str(data.get("title", "Healing session"))[:80], json.dumps(config), max(0, int(data.get("duration_seconds", 0)))),
    )
    db.commit()
    return jsonify({"ok": True}), 201


@player_bp.get("/api/profile")
@login_required
def profile():
    from .closeai import get_provider_settings
    used = usage_bytes(g.user["username"])
    return jsonify({
        "username": g.user["username"],
        "email": g.user["email"],
        "storage_used": used,
        "storage_limit": int(__import__("flask").current_app.config["USER_QUOTA_BYTES"]),
        "api_ready": bool(get_provider_settings()["api_key"]),
    })


@player_bp.get("/api/snapshots")
@login_required
def snapshots():
    root = user_root(g.user["username"]) / "snapshots"
    items = sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True)
    return jsonify(items[:25])
