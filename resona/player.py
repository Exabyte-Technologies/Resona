import base64
import binascii
import json
import mimetypes
import shutil
from pathlib import Path, PurePosixPath

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request

from .db import get_db
from .security import login_required, require_csrf
from .user_storage import ensure_chord_model_assets, migrate_legacy_default_workspace, safe_path, usage_bytes, user_root, validate_nav


player_bp = Blueprint("player", __name__, url_prefix="/player")

FRONTEND_FILE_MAX_BYTES = 5 * 1024 * 1024
FRONTEND_FILE_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp3", ".wav", ".ogg", ".pdf", ".zip", ".bin",
}


def _frontend_file_path(username, value, allow_root=False):
    text = str(value or "").replace("\\", "/").strip("/")
    if not text and allow_root:
        return user_root(username) / "data", ""
    clean = PurePosixPath(text)
    if not text or clean.is_absolute() or ".." in clean.parts or len(text) > 240:
        raise ValueError("Invalid persistent file path")
    root = (user_root(username) / "data").resolve()
    target = (root / Path(*clean.parts)).resolve()
    if root not in target.parents:
        raise ValueError("Persistent file path leaves the data area")
    return target, clean.as_posix()


def _validate_frontend_file_extension(path):
    if path.suffix.lower() not in FRONTEND_FILE_EXTENSIONS:
        raise ValueError("That persistent file type is not supported")


def _write_frontend_bytes(username, target, content):
    if len(content) > FRONTEND_FILE_MAX_BYTES:
        raise ValueError("Persistent files are limited to 5 MB each")
    old_size = target.stat().st_size if target.is_file() and not target.is_symlink() else 0
    projected = usage_bytes(username) - old_size + len(content)
    if projected > int(current_app.config["USER_QUOTA_BYTES"]):
        raise ValueError("This file would exceed the user's storage quota")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@player_bp.post("/api/files")
@login_required
def persistent_files():
    require_csrf()
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", ""))
    data_root = user_root(g.user["username"]) / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    try:
        if action == "list":
            target, relative = _frontend_file_path(g.user["username"], data.get("path", ""), allow_root=True)
            if not target.is_dir() or target.is_symlink():
                raise FileNotFoundError(relative)
            items = []
            for item in sorted(target.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower())):
                if item.is_symlink():
                    continue
                item_relative = item.relative_to(data_root).as_posix()
                items.append({
                    "path": item_relative,
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                    "mime": mimetypes.guess_type(item.name)[0] if item.is_file() else None,
                })
                if len(items) >= 300:
                    break
            return jsonify({"ok": True, "path": relative, "items": items, "truncated": len(items) >= 300})

        if action == "read":
            target, relative = _frontend_file_path(g.user["username"], data.get("path"))
            if not target.is_file() or target.is_symlink():
                raise FileNotFoundError(relative)
            if target.stat().st_size > FRONTEND_FILE_MAX_BYTES:
                raise ValueError("Persistent files are limited to 5 MB each")
            content = target.read_bytes()
            encoding = str(data.get("encoding", "text"))
            if encoding == "base64":
                value = base64.b64encode(content).decode("ascii")
            elif encoding == "text":
                value = content.decode("utf-8")
            else:
                raise ValueError("Read encoding must be text or base64")
            return jsonify({"ok": True, "path": relative, "size": len(content), "mime": mimetypes.guess_type(target.name)[0] or "application/octet-stream", "encoding": encoding, "content": value})

        if action in {"write", "upload"}:
            target, relative = _frontend_file_path(g.user["username"], data.get("path"))
            _validate_frontend_file_extension(target)
            if target.exists() and (target.is_dir() or target.is_symlink()):
                raise ValueError("The destination is not a writable file")
            if action == "write":
                if not isinstance(data.get("content"), str):
                    raise ValueError("Text content must be a string")
                content = data["content"].encode("utf-8")
            else:
                try:
                    content = base64.b64decode(str(data.get("content", "")), validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("Upload content must be valid base64") from exc
            _write_frontend_bytes(g.user["username"], target, content)
            return jsonify({"ok": True, "path": relative, "size": len(content), "mime": mimetypes.guess_type(target.name)[0] or "application/octet-stream"})

        if action == "mkdir":
            target, relative = _frontend_file_path(g.user["username"], data.get("path"))
            if target.exists() and not target.is_dir():
                raise ValueError("A file already exists at that path")
            target.mkdir(parents=True, exist_ok=True)
            return jsonify({"ok": True, "path": relative})

        if action == "move":
            source, source_relative = _frontend_file_path(g.user["username"], data.get("source"))
            destination, destination_relative = _frontend_file_path(g.user["username"], data.get("destination"))
            if not source.exists() or source.is_symlink():
                raise FileNotFoundError(source_relative)
            if destination.exists():
                raise FileExistsError(destination_relative)
            if source.is_file():
                _validate_frontend_file_extension(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            return jsonify({"ok": True, "source": source_relative, "destination": destination_relative})

        if action == "delete":
            target, relative = _frontend_file_path(g.user["username"], data.get("path"))
            if not target.exists() or target.is_symlink():
                raise FileNotFoundError(relative)
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return jsonify({"ok": True, "deleted": relative})

        raise ValueError("Unknown persistent file action")
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": f"Persistent path not found: {exc}"}), 404
    except FileExistsError as exc:
        return jsonify({"ok": False, "error": f"Persistent path already exists: {exc}"}), 409
    except (UnicodeDecodeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


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
