import os
import secrets
from pathlib import Path

from flask import Flask, g, redirect, session, url_for
from dotenv import load_dotenv

from .db import close_db, init_app as init_db_app


load_dotenv()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
        DATABASE=str(Path(app.instance_path) / "resona.sqlite3"),
        STORAGE_ROOT=str(Path(app.instance_path) / "storage"),
        USER_QUOTA_BYTES=1_073_741_824,
        CLOSEAI_BASE_URL=os.getenv("CLOSEAI_BASE_URL", "https://api.openai-proxy.org"),
        CLOSEAI_API_KEY=os.getenv("CLOSEAI_API_KEY", ""),
        CLOSEAI_MODEL=os.getenv("CLOSEAI_MODEL", "gpt-5.6-sol"),
        CLOSEAI_READ_TIMEOUT_SECONDS=int(os.getenv("CLOSEAI_READ_TIMEOUT_SECONDS", "300")),
        CLOSEAI_PREFER_ENV=os.getenv("CLOSEAI_PREFER_ENV", "0") == "1",
        RESEND_API_KEY=os.getenv("RESEND_API_KEY", ""),
        RESEND_FROM_EMAIL=os.getenv("RESEND_FROM_EMAIL", "").strip().lower(),
        RESEND_FROM_NAME=os.getenv("RESEND_FROM_NAME", "Resona").strip(),
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
        AGENT_MAX_STEPS=int(os.getenv("AGENT_MAX_STEPS", "80")),
        CAPTCHA_CHALLENGE_COUNT=int(os.getenv("CAPTCHA_CHALLENGE_COUNT", "50")),
        CAPTCHA_CHALLENGE_DIFFICULTY=int(os.getenv("CAPTCHA_CHALLENGE_DIFFICULTY", "4")),
        AGENT_TRACE=False,
        ADMIN_USERNAME=os.getenv("ADMIN_USERNAME", "admin").strip().lower(),
        ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD", ""),
        ADMIN_EMAIL=os.getenv("ADMIN_EMAIL", "").strip().lower(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    )
    if os.getenv("DATABASE_PATH"):
        app.config["DATABASE"] = os.getenv("DATABASE_PATH")
    if os.getenv("RESONA_STORAGE_ROOT"):
        app.config["STORAGE_ROOT"] = os.getenv("RESONA_STORAGE_ROOT")
    if os.getenv("RESONA_USER_QUOTA_BYTES"):
        app.config["USER_QUOTA_BYTES"] = int(os.getenv("RESONA_USER_QUOTA_BYTES"))
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["STORAGE_ROOT"]).mkdir(parents=True, exist_ok=True)
    init_db_app(app)

    from .auth import auth_bp
    from .admin import admin_bp
    from .player import player_bp
    from .agent import agent_bp
    from .storage import storage_bp
    from .account import account_bp
    from .captcha import captcha_bp, init_captcha

    init_captcha(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(storage_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(captcha_bp)

    @app.get("/")
    def home():
        return redirect(url_for("player.index" if g.get("user") else "auth.login"))

    @app.before_request
    def load_user():
        from .db import get_db

        user_id = session.get("user_id")
        g.user = get_db().execute(
            "SELECT id, username, email, display_name, email_verified_at, is_admin, is_demo, demo_enabled, session_version, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone() if user_id else None
        if g.user and g.user["is_demo"] and (
            not g.user["demo_enabled"] or session.get("session_version", 0) != g.user["session_version"]
        ):
            session.clear()
            g.user = None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-src 'self'; "
            "worker-src 'self' blob:; font-src 'self'",
        )
        return response

    @app.context_processor
    def inject_csrf():
        from .user_controls import get_user_controls

        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return {"csrf_token": token, "user_controls": get_user_controls()}

    app.teardown_appcontext(close_db)
    return app
