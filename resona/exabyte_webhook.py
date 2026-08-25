import hashlib
import hmac
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, request

from .db import get_db
from .exabyte_oidc import EMAIL_RE, PROVIDER, get_exabyte_settings
from .secret_store import encrypt_setting


webhook_bp = Blueprint("exabyte_webhook", __name__)
MAX_BODY_BYTES = 256 * 1024
MAX_CLOCK_SKEW_SECONDS = 300
EVENT_ID_RE = re.compile(r"^evt_[A-Za-z0-9_-]{3,76}$")
DELIVERY_ID_RE = re.compile(r"^dlv_[A-Za-z0-9_-]{3,76}$")
PROFILE_TYPES = {"user.profile.snapshot.v1", "user.profile.updated.v1"}
LIFECYCLE_TYPES = {
    "user.account.status_changed.v1",
    "user.security.password_changed.v1",
    "user.sessions.revoked.v1",
    "user.connection.revoked.v1",
}
ALLOWED_TYPES = PROFILE_TYPES | LIFECYCLE_TYPES | {"product.integration.test.v1"}
PROFILE_FIELDS = {"name", "preferred_username", "locale", "zoneinfo", "picture", "email", "email_verified"}
BLOCKED_STATUSES = {"suspended", "disabled", "pending_deletion", "anonymized"}


class InvalidEvent(ValueError):
    pass


def _error(code, status):
    return {"error": code}, status


def _verified_event(raw_body):
    settings = get_exabyte_settings()
    secret = settings["webhook_secret"]
    if not secret:
        raise RuntimeError("webhook secret unavailable")
    timestamp = request.headers.get("X-Exabyte-Timestamp", "")
    supplied = request.headers.get("X-Exabyte-Signature", "")
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
        raise PermissionError("stale_timestamp")
    expected = hmac.new(
        secret.encode("utf-8"), timestamp.encode("ascii") + b"." + raw_body, hashlib.sha256
    ).hexdigest()
    received = supplied[3:] if supplied.startswith("v1=") else ""
    if len(received) != len(expected) or not hmac.compare_digest(received, expected):
        raise PermissionError("invalid_signature")
    try:
        event = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InvalidEvent("invalid_json")
    if not isinstance(event, dict):
        raise InvalidEvent("invalid_event")
    event_id = request.headers.get("X-Exabyte-Event-Id", "")
    delivery_id = request.headers.get("X-Exabyte-Delivery-Id", "")
    if not EVENT_ID_RE.fullmatch(event_id) or event_id != event.get("id"):
        raise InvalidEvent("event_id_mismatch")
    if not DELIVERY_ID_RE.fullmatch(delivery_id):
        raise InvalidEvent("invalid_delivery_id")
    if event.get("specversion") != "1.0" or event.get("datacontenttype") != "application/json":
        raise InvalidEvent("invalid_envelope")
    if str(event.get("source", "")).rstrip("/") != settings["issuer"]:
        raise InvalidEvent("invalid_source")
    if event.get("type") not in ALLOWED_TYPES:
        raise InvalidEvent("unsupported_event")
    event["_delivery_id"] = delivery_id
    return event


def _identity(subject):
    return get_db().execute(
        "SELECT * FROM external_identities WHERE provider = ? AND subject = ?",
        (PROVIDER, subject),
    ).fetchone()


def _profile_changes(event):
    revision = event.get("profile_revision")
    data = event.get("data")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise InvalidEvent("invalid_profile_revision")
    if event.get("origin") != "accounts" or not isinstance(data, dict):
        raise InvalidEvent("invalid_profile_event")
    changes = data.get("changes")
    changed_fields = data.get("changed_fields")
    if not isinstance(changes, dict) or not isinstance(changed_fields, list):
        raise InvalidEvent("invalid_profile_changes")
    if set(changed_fields) != set(changes) or not set(changes).issubset(PROFILE_FIELDS):
        raise InvalidEvent("invalid_profile_changes")
    return revision, changes


def _queue_avatar(user_id, revision, picture):
    if picture is not None and not isinstance(picture, str):
        raise InvalidEvent("invalid_picture")
    encrypted_url = encrypt_setting(picture) if picture else ""
    get_db().execute(
        "INSERT INTO exabyte_avatar_jobs(user_id, encrypted_url, profile_revision) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET encrypted_url=excluded.encrypted_url, profile_revision=excluded.profile_revision, "
        "attempts=0, next_attempt_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP",
        (user_id, encrypted_url, revision),
    )


def _apply_profile(identity, event):
    revision, changes = _profile_changes(event)
    current_revision = identity["profile_revision"] or 0
    if revision <= current_revision:
        return "stale"
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (identity["user_id"],)).fetchone()
    if user is None:
        return "unmatched"

    name = identity["display_name"]
    preferred_username = identity["preferred_username"]
    locale = identity["locale"]
    zoneinfo = identity["zoneinfo"]
    provider_email = identity["email"]
    email_verified = bool(identity["email_verified"])
    if "name" in changes:
        name = str(changes["name"] or "").strip()[:80]
    if "preferred_username" in changes:
        preferred_username = str(changes["preferred_username"] or "").strip().lower()[:80] or None
    if "locale" in changes:
        locale = str(changes["locale"] or "").strip()[:40] or None
    if "zoneinfo" in changes:
        zoneinfo = str(changes["zoneinfo"] or "").strip()[:80] or None
    if "email" in changes:
        candidate = str(changes["email"] or "").strip().lower()
        provider_email = candidate if candidate else identity["email"]
    if "email_verified" in changes:
        email_verified = changes["email_verified"] is True

    display_name = name or preferred_username or user["username"]
    warnings = []
    if event["type"] == "user.profile.updated.v1" and revision > current_revision + 1:
        warnings.append("Some Exabyte profile updates may have been missed. Sign in with Exabyte again to reconcile them.")
    db.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user["id"]))
    if email_verified and EMAIL_RE.fullmatch(provider_email or ""):
        owner = db.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?", (provider_email, user["id"])
        ).fetchone()
        if owner:
            warnings.append("Your Exabyte email is already used by another Resona account, so the previous Resona email was retained.")
        else:
            db.execute(
                "UPDATE users SET email = ?, email_verified_at = CURRENT_TIMESTAMP WHERE id = ?",
                (provider_email, user["id"]),
            )
    else:
        warnings.append("Your current Exabyte email is not verified, so the previous Resona email was retained.")

    db.execute(
        "UPDATE external_identities SET email = ?, display_name = ?, email_verified = ?, preferred_username = ?, locale = ?, zoneinfo = ?, "
        "profile_revision = ?, sync_warning = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (provider_email, name or "", int(email_verified), preferred_username, locale, zoneinfo, revision, " ".join(warnings) or None, identity["id"]),
    )
    if "picture" in changes:
        _queue_avatar(user["id"], revision, changes["picture"])
    return "applied"


def _apply_lifecycle(identity, event):
    if event.get("origin") != "accounts" or not isinstance(event.get("data"), dict):
        raise InvalidEvent("invalid_lifecycle_event")
    if identity is None:
        return "unmatched"
    db = get_db()
    event_type = event["type"]
    if event_type in {"user.security.password_changed.v1", "user.sessions.revoked.v1"}:
        db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (identity["user_id"],))
        return "sessions_revoked"
    if event_type == "user.connection.revoked.v1":
        db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (identity["user_id"],))
        db.execute(
            "UPDATE external_identities SET connection_status = 'revoked', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (identity["id"],),
        )
        return "connection_revoked"

    status = event["data"].get("status")
    if status not in BLOCKED_STATUSES | {"active"}:
        raise InvalidEvent("invalid_account_status")
    if identity["account_status"] == "anonymized" and status != "anonymized":
        return "anonymized_retained"
    purge_after = None
    if status == "anonymized":
        purge_after = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    db.execute(
        "UPDATE external_identities SET account_status = ?, purge_after = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, purge_after, identity["id"]),
    )
    if status in BLOCKED_STATUSES:
        db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (identity["user_id"],))
    return f"status_{status}"


def _process_event(event):
    event_type = event["type"]
    settings = get_exabyte_settings()
    if event_type == "product.integration.test.v1":
        if event.get("subject") != settings["client_id"] or not isinstance(event.get("data"), dict):
            raise InvalidEvent("invalid_test_event")
        return "tested", 0
    subject = event.get("subject")
    if not isinstance(subject, str) or not subject.startswith("usr_") or len(subject) > 80:
        raise InvalidEvent("invalid_subject")
    identity = _identity(subject)
    if event_type in PROFILE_TYPES:
        revision, _changes = _profile_changes(event)
        if identity is None:
            return "unmatched", revision
        if identity["connection_status"] != "active":
            return "disconnected", revision
        return _apply_profile(identity, event), revision
    revision = event.get("profile_revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise InvalidEvent("invalid_profile_revision")
    return _apply_lifecycle(identity, event), revision


@webhook_bp.post("/webhooks/exabyte")
def exabyte_webhook():
    if request.mimetype != "application/cloudevents+json":
        return _error("unsupported_content_type", 415)
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        return _error("payload_too_large", 413)
    raw_body = request.get_data(cache=False)
    if len(raw_body) > MAX_BODY_BYTES:
        return _error("payload_too_large", 413)
    try:
        event = _verified_event(raw_body)
        db = get_db()
        if db.execute("SELECT 1 FROM exabyte_webhook_events WHERE event_id = ?", (event["id"],)).fetchone():
            return "", 204
        outcome, revision = _process_event(event)
        db.execute(
            "INSERT INTO exabyte_webhook_events(event_id, delivery_id, event_type, subject, profile_revision, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (event["id"], event["_delivery_id"], event["type"], str(event.get("subject", ""))[:80], revision, outcome),
        )
        db.commit()
    except PermissionError as exc:
        return _error(str(exc), 401)
    except InvalidEvent as exc:
        return _error(str(exc), 400)
    except RuntimeError:
        return _error("webhook_unavailable", 503)
    except sqlite3.IntegrityError:
        get_db().rollback()
        if get_db().execute("SELECT 1 FROM exabyte_webhook_events WHERE event_id = ?", (event.get("id"),)).fetchone():
            return "", 204
        return _error("storage_unavailable", 503)
    except Exception:
        get_db().rollback()
        current_app.logger.exception("Exabyte webhook processing failed")
        return _error("processing_failed", 503)
    return "", 204
