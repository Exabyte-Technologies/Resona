import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import click
import requests
from cryptography.fernet import InvalidToken
from flask import current_app

from .db import get_db
from .secret_store import decrypt_setting
from .user_storage import delete_user_storage


MAX_AVATAR_BYTES = 2 * 1024 * 1024
MIME_TYPES = {
    "image/png": ("png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/jpeg": ("jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/webp": ("webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
    "image/gif": ("gif", lambda data: data.startswith((b"GIF87a", b"GIF89a"))),
}


def avatar_root():
    root = Path(current_app.config["EXABYTE_AVATAR_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _remove_user_avatars(user_id):
    for path in avatar_root().glob(f"{int(user_id)}.*"):
        if path.is_file() and path.parent == avatar_root():
            path.unlink(missing_ok=True)


def _validated_picture_url(value):
    picture = urlsplit(value)
    issuer = urlsplit(current_app.config["EXABYTE_OIDC_ISSUER"])
    if (
        picture.scheme != "https"
        or picture.username
        or picture.password
        or picture.netloc != issuer.netloc
        or not picture.path.startswith("/api/v1/profile-events/avatar/")
        or picture.fragment
    ):
        raise ValueError("invalid avatar source")
    return value


def _download_avatar(url):
    response = requests.get(
        _validated_picture_url(url), stream=True, allow_redirects=False, timeout=(3, 5)
    )
    try:
        response.raise_for_status()
        mime_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if mime_type not in MIME_TYPES:
            raise ValueError("unsupported avatar type")
        content_length = response.headers.get("Content-Length", "")
        if content_length.isdigit() and int(content_length) > MAX_AVATAR_BYTES:
            raise ValueError("avatar too large")
        chunks = []
        total = 0
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_AVATAR_BYTES:
                raise ValueError("avatar too large")
            chunks.append(chunk)
        data = b"".join(chunks)
        extension, validator = MIME_TYPES[mime_type]
        if not data or not validator(data):
            raise ValueError("avatar content did not match its media type")
        return data, mime_type, extension
    finally:
        response.close()


def _retry_avatar(job, message):
    attempts = job["attempts"] + 1
    db = get_db()
    if attempts >= 8:
        db.execute("DELETE FROM exabyte_avatar_jobs WHERE user_id = ?", (job["user_id"],))
        current_app.logger.warning("Exabyte avatar synchronization stopped after repeated failures (%s)", message)
    else:
        delay = min(60, 2 ** attempts)
        next_attempt = (datetime.now(timezone.utc) + timedelta(minutes=delay)).isoformat()
        db.execute(
            "UPDATE exabyte_avatar_jobs SET attempts = ?, next_attempt_at = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
            (attempts, next_attempt, job["user_id"]),
        )
    db.commit()


def process_avatar_jobs(limit=20):
    db = get_db()
    jobs = db.execute(
        "SELECT * FROM exabyte_avatar_jobs WHERE next_attempt_at <= ? ORDER BY next_attempt_at LIMIT ?",
        (datetime.now(timezone.utc).isoformat(), limit),
    ).fetchall()
    completed = 0
    for job in jobs:
        identity = db.execute(
            "SELECT * FROM external_identities WHERE provider = 'exabyte' AND user_id = ?", (job["user_id"],)
        ).fetchone()
        if identity is None or identity["profile_revision"] != job["profile_revision"]:
            db.execute("DELETE FROM exabyte_avatar_jobs WHERE user_id = ?", (job["user_id"],))
            db.commit()
            continue
        if not job["encrypted_url"]:
            _remove_user_avatars(job["user_id"])
            db.execute(
                "UPDATE external_identities SET avatar_filename = NULL, avatar_mime_type = NULL WHERE id = ?",
                (identity["id"],),
            )
            db.execute("DELETE FROM exabyte_avatar_jobs WHERE user_id = ?", (job["user_id"],))
            db.commit()
            completed += 1
            continue
        try:
            url = decrypt_setting(job["encrypted_url"])
            data, mime_type, extension = _download_avatar(url)
            filename = f"{job['user_id']}.{extension}"
            with tempfile.NamedTemporaryFile(dir=avatar_root(), delete=False) as temporary:
                temporary.write(data)
                temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o640)
            _remove_user_avatars(job["user_id"])
            os.replace(temporary_path, avatar_root() / filename)
            db.execute(
                "UPDATE external_identities SET avatar_filename = ?, avatar_mime_type = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (filename, mime_type, identity["id"]),
            )
            db.execute("DELETE FROM exabyte_avatar_jobs WHERE user_id = ?", (job["user_id"],))
            db.commit()
            completed += 1
        except (InvalidToken, OSError, requests.RequestException, ValueError) as exc:
            _retry_avatar(job, type(exc).__name__)
    return completed


def purge_anonymized_accounts():
    db = get_db()
    rows = db.execute(
        "SELECT users.*, external_identities.id AS identity_id FROM users "
        "JOIN external_identities ON external_identities.user_id = users.id "
        "WHERE external_identities.provider = 'exabyte' AND external_identities.account_status = 'anonymized' "
        "AND external_identities.purge_after IS NOT NULL AND external_identities.purge_after <= ?",
        (datetime.now(timezone.utc).isoformat(),),
    ).fetchall()
    purged = 0
    for user in rows:
        if user["is_admin"]:
            admin_count = db.execute("SELECT COUNT(*) AS count FROM users WHERE is_admin = 1").fetchone()["count"]
            if admin_count <= 1:
                db.execute(
                    "UPDATE external_identities SET sync_warning = ? WHERE id = ?",
                    ("This anonymized account is the final Resona administrator and requires administrative intervention before deletion.", user["identity_id"]),
                )
                db.commit()
                continue
        try:
            delete_user_storage(user["username"])
            _remove_user_avatars(user["id"])
        except OSError:
            current_app.logger.exception("Could not purge anonymized Resona storage for user %s", user["id"])
            continue
        db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        db.commit()
        purged += 1
    return purged


@click.command("exabyte-maintenance")
def maintenance_command():
    avatars = process_avatar_jobs()
    purged = purge_anonymized_accounts()
    click.echo(f"Exabyte maintenance complete: {avatars} avatar job(s), {purged} account purge(s).")


def init_app(app):
    app.cli.add_command(maintenance_command)
