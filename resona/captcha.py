import hashlib
import sqlite3

from capjs_server import CapServer
from flask import Blueprint, abort, current_app, jsonify, request

from .db import get_db


captcha_bp = Blueprint("captcha", __name__, url_prefix="/captcha")


def init_captcha(app):
    app.extensions["resona_cap"] = CapServer(
        secret_key=str(app.config["SECRET_KEY"]),
        challenge_count=int(app.config["CAPTCHA_CHALLENGE_COUNT"]),
        challenge_difficulty=int(app.config["CAPTCHA_CHALLENGE_DIFFICULTY"]),
    )


def _cap():
    return current_app.extensions["resona_cap"]


def submitted_captcha_token(data=None):
    if isinstance(data, dict):
        token = data.get("cap_token") or data.get("cap-token")
    else:
        token = None
    return str(token or request.form.get("cap-token", "") or request.headers.get("X-Cap-Token", "")).strip()


def validate_captcha(data=None):
    token = submitted_captcha_token(data)
    if not _cap().validate(token):
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    db = get_db()
    try:
        db.execute("INSERT INTO captcha_redemptions(token_hash) VALUES (?)", (digest,))
        db.execute("DELETE FROM captcha_redemptions WHERE used_at < datetime('now', '-1 day')")
        db.commit()
        return True
    except sqlite3.IntegrityError:
        db.rollback()
        return False


def require_captcha(data=None):
    if not validate_captcha(data):
        abort(400, "Complete the CAPTCHA verification and try again")


@captcha_bp.post("/challenge")
def challenge():
    return jsonify(_cap().create_challenge())


@captcha_bp.post("/redeem")
def redeem():
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    solutions = data.get("solutions")
    if not isinstance(token, str) or not isinstance(solutions, list) or not all(isinstance(value, int) for value in solutions):
        return jsonify({"success": False, "error": "Invalid CAPTCHA solution"}), 400
    result = _cap().redeem(token, solutions)
    return jsonify(result), (200 if result.get("success") else 400)
