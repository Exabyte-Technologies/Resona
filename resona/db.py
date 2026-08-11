import sqlite3
from pathlib import Path

import click
from flask import current_app, g
from werkzeug.security import generate_password_hash


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
    schema = Path(current_app.root_path) / "schema.sql"
    db.executescript(schema.read_text(encoding="utf-8"))
    db.execute(
        "UPDATE settings SET value = ? WHERE key = 'agent_system_prompt' AND value = ?",
        (
            "You are Resona Vibe Agent, an autonomous coding agent for one isolated user workspace. Inspect relevant files, use the available tools repeatedly, implement the request completely, validate the result, and finish with a concise summary.",
            "You are Resona Vibe Agent. Return only JSON describing safe files inside the current user storage. Preserve the outer navigation shell and microphone control. Build accessible healing interfaces and Web Audio configurations.",
        ),
    )
    db.execute(
        "UPDATE settings SET value = ? WHERE key = 'agent_system_prompt' AND value = ?",
        (
            "You are Resona Vibe Agent, an autonomous coding agent for one isolated user workspace. Inspect relevant HTML and CSS, use the available tools repeatedly, implement complete styled interfaces rather than placeholder text, validate the result, and finish with a concise summary.",
            "You are Resona Vibe Agent, an autonomous coding agent for one isolated user workspace. Inspect relevant files, use the available tools repeatedly, implement the request completely, validate the result, and finish with a concise summary.",
        ),
    )
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
