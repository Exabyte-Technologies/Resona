import hashlib
import math
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, url_for

from .db import get_db
from .resend import resend_is_configured, send_email_verification_email


EMAIL_VERIFICATION_RESEND_SECONDS = 60


def verification_resend_wait(user_id):
    row = get_db().execute(
        "SELECT created_at FROM email_verifications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if not row:
        return 0
    created_at = datetime.fromisoformat(row["created_at"])
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - created_at).total_seconds()
    return max(0, math.ceil(EMAIL_VERIFICATION_RESEND_SECONDS - elapsed))


def issue_email_verification(user_id, display_name, email, purpose):
    token = secrets.token_urlsafe(36)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    db = get_db()
    db.execute("UPDATE email_verifications SET used_at = CURRENT_TIMESTAMP WHERE user_id = ? AND purpose = ? AND used_at IS NULL", (user_id, purpose))
    db.execute(
        "INSERT INTO email_verifications(user_id, email, token_hash, purpose, expires_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, email, digest, purpose, expires),
    )
    verify_path = url_for("auth.verify_email", token=token)
    base_url = current_app.config.get("PUBLIC_BASE_URL", "").rstrip("/")
    verification_url = base_url + verify_path if base_url else url_for("auth.verify_email", token=token, _external=True)
    if resend_is_configured():
        try:
            send_email_verification_email(email, display_name, verification_url, purpose)
        except Exception as exc:
            raise RuntimeError("Resend could not deliver the verification email") from exc
    elif not current_app.testing:
        raise RuntimeError("Email verification requires configured Resend credentials")
    return token


def verification_record(token):
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = get_db().execute(
        "SELECT * FROM email_verifications WHERE token_hash = ? AND used_at IS NULL",
        (digest,),
    ).fetchone()
    if not row or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
        return None
    return row
