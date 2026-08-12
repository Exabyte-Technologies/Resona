import sqlite3
from pathlib import Path

import click
from flask import current_app, g
from werkzeug.security import generate_password_hash


AGENT_PROMPT_VERSION = "resona-ai-mobile-2026-08-12"
AGENT_MODEL_VERSION = "gpt-5.6-sol-2026-08-12"
DEFAULT_AGENT_MODEL = "gpt-5.6-sol"


def default_agent_prompt():
    return (Path(__file__).parent / "default_agent_prompt.txt").read_text(encoding="utf-8").strip()


def get_db():
    if "db" not in g:
        db_path = Path(current_app.config["DATABASE"])
        if not db_path.is_absolute():
            db_path = Path(current_app.root_path).parent / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(db_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    existing_user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    schema = Path(current_app.root_path) / "schema.sql"
    db.executescript(schema.read_text(encoding="utf-8"))
    if existing_user_columns and "display_name" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if existing_user_columns and "email_verified_at" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
        db.execute("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP")
    db.execute("UPDATE users SET display_name = username WHERE display_name IS NULL OR trim(display_name) = ''")
    prompt_version = db.execute("SELECT value FROM settings WHERE key = 'agent_prompt_version'").fetchone()["value"]
    if prompt_version != AGENT_PROMPT_VERSION:
        db.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'agent_system_prompt'", (default_agent_prompt(),))
        db.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'agent_prompt_version'", (AGENT_PROMPT_VERSION,))
    model_version = db.execute("SELECT value FROM settings WHERE key = 'agent_model_version'").fetchone()["value"]
    if model_version != AGENT_MODEL_VERSION:
        db.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'closeai_model'", (DEFAULT_AGENT_MODEL,))
        db.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = 'agent_model_version'", (AGENT_MODEL_VERSION,))
    db.commit()


def sync_environment_admin():
    username = current_app.config.get("ADMIN_USERNAME", "").strip().lower()
    password = current_app.config.get("ADMIN_PASSWORD", "")
    if not username or not password:
        return
    email = current_app.config.get("ADMIN_EMAIL", "").strip().lower() or f"{username}@resona.local"
    db = get_db()
    db.execute(
        "INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, 1) "
        "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, is_admin=1",
        (username, email, generate_password_hash(password, method="pbkdf2:sha256:600000")),
    )
    db.commit()
    from .user_storage import initialize_user_storage, user_root
    if not user_root(username).exists():
        initialize_user_storage(username)


@click.command("init-db")
def init_db_command():
    init_db()
    click.echo("Initialized Resona database.")


@click.command("create-admin")
@click.option("--username", prompt=True)
@click.option("--email", prompt=True)
@click.password_option()
def create_admin_command(username, email, password):
    db = get_db()
    db.execute(
        "INSERT INTO users (username, email, password_hash, is_admin) VALUES (?, ?, ?, 1) "
        "ON CONFLICT(username) DO UPDATE SET email=excluded.email, password_hash=excluded.password_hash, is_admin=1",
        (username.strip().lower(), email.strip().lower(), generate_password_hash(password, method="pbkdf2:sha256:600000")),
    )
    db.commit()
    from .user_storage import initialize_user_storage, user_root
    if not user_root(username.strip().lower()).exists():
        initialize_user_storage(username.strip().lower())
    click.echo(f"Admin {username} is ready.")


def init_app(app):
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
    with app.app_context():
        init_db()
        sync_environment_admin()
