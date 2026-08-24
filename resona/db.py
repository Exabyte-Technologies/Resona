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
    existing_agent_columns = {row["name"] for row in db.execute("PRAGMA table_info(agent_runs)").fetchall()}
    schema = Path(current_app.root_path) / "schema.sql"
    db.executescript(schema.read_text(encoding="utf-8"))
    if existing_user_columns and "display_name" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if existing_user_columns and "email_verified_at" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
        db.execute("UPDATE users SET email_verified_at = CURRENT_TIMESTAMP")
    if existing_user_columns and "is_demo" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0")
    if existing_user_columns and "demo_enabled" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN demo_enabled INTEGER NOT NULL DEFAULT 1")
    if existing_user_columns and "session_version" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
    if existing_user_columns and "password_login_enabled" not in existing_user_columns:
        db.execute("ALTER TABLE users ADD COLUMN password_login_enabled INTEGER NOT NULL DEFAULT 1")
    db.execute("UPDATE users SET display_name = username WHERE display_name IS NULL OR trim(display_name) = ''")
    if existing_agent_columns and "client_request_id" not in existing_agent_columns:
        db.execute("ALTER TABLE agent_runs ADD COLUMN client_request_id TEXT")
    if existing_agent_columns and "steps" not in existing_agent_columns:
        db.execute("ALTER TABLE agent_runs ADD COLUMN steps INTEGER")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS agent_runs_user_request_id "
        "ON agent_runs(user_id, client_request_id) WHERE client_request_id IS NOT NULL"
    )
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
    if username == "demo":
        current_app.logger.warning("ADMIN_USERNAME=demo is reserved; the environment administrator was not synchronized.")
        return
    email = current_app.config.get("ADMIN_EMAIL", "").strip().lower() or f"{username}@resona.local"
    db = get_db()
    db.execute(
        "INSERT INTO users (username, email, email_verified_at, password_hash, is_admin) VALUES (?, ?, CURRENT_TIMESTAMP, ?, 1) "
        "ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, email_verified_at=CURRENT_TIMESTAMP, is_admin=1",
        (username, email, generate_password_hash(password, method="pbkdf2:sha256:600000")),
    )
    db.commit()
    from .user_storage import initialize_user_storage, user_root
    if not user_root(username).exists():
        initialize_user_storage(username)


def sync_demo_account():
    """Create the reserved public demo once without overwriting admin changes."""
    db = get_db()
    demo = db.execute("SELECT * FROM users WHERE lower(username) = 'demo'").fetchone()
    if demo is None:
        cursor = db.execute(
            "INSERT INTO users(username, email, display_name, email_verified_at, password_hash, is_demo, demo_enabled) "
            "VALUES ('demo', '', 'Demo', CURRENT_TIMESTAMP, ?, 1, 1)",
            (generate_password_hash("", method="pbkdf2:sha256:600000"),),
        )
        demo_id = cursor.lastrowid
    else:
        demo_id = demo["id"]
        if not demo["is_demo"]:
            db.execute(
                "UPDATE users SET username='demo', email='', display_name='Demo', email_verified_at=CURRENT_TIMESTAMP, "
                "password_hash=?, is_admin=0, is_demo=1, demo_enabled=1 WHERE id=?",
                (generate_password_hash("", method="pbkdf2:sha256:600000"), demo_id),
            )
        else:
            db.execute(
                "UPDATE users SET username='demo', email='', display_name='Demo', email_verified_at=CURRENT_TIMESTAMP, is_admin=0 WHERE id=?",
                (demo_id,),
            )
    db.commit()
    from .demo import ensure_demo_workspace
    ensure_demo_workspace()
    return demo_id


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
        "INSERT INTO users (username, email, email_verified_at, password_hash, is_admin) VALUES (?, ?, CURRENT_TIMESTAMP, ?, 1) "
        "ON CONFLICT(username) DO UPDATE SET email=excluded.email, password_hash=excluded.password_hash, email_verified_at=CURRENT_TIMESTAMP, is_admin=1",
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
        sync_demo_account()
