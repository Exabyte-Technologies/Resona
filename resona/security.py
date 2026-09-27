import functools
import hmac
import re
import time

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")

# Browsers normalise backslashes to forward slashes and silently drop ASCII
# control characters while resolving a URL. That turns "/\evil.com" and
# "/<TAB>/evil.com" into the protocol-relative "//evil.com", so a plain
# startswith("//") check is not enough to keep a redirect on this origin.
_URL_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

_DUMMY_PASSWORD_HASH = None
_RATE_LIMIT_BUCKETS = {}
_RATE_LIMIT_MAX_KEYS = 4096


def dummy_password_hash():
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = generate_password_hash(
            "resona-constant-time-placeholder", method="pbkdf2:sha256:600000"
        )
    return _DUMMY_PASSWORD_HASH


def constant_time_password_check(stored_hash, password):
    """Verify a password and always pay the full PBKDF2 cost.

    Callers must use this even when no account was found, otherwise a missing
    user returns instantly while a real one spends ~40 ms in PBKDF2 and the
    difference becomes a remote username/e-mail enumeration oracle.
    """
    return check_password_hash(stored_hash or dummy_password_hash(), password)


def safe_local_path(value, default=None):
    """Return `value` only when it is a same-site absolute path.

    Rejects absolute URLs, protocol-relative URLs, and the backslash or
    control-character spellings that browsers rewrite into them.
    """
    candidate = _URL_CONTROL_CHARACTERS.sub("", str(value or ""))
    if "\\" in candidate:
        return default
    if not candidate.startswith("/") or candidate.startswith("//"):
        return default
    return candidate


def rate_limited(key, limit, window_seconds):
    """Record an attempt for `key` and report whether it is over budget.

    State is per worker process, so with N gunicorn workers the effective
    ceiling is `limit * N`. This is a speed bump against credential stuffing
    and e-mail flooding, not a substitute for a shared rate-limit store.
    """
    if current_app.config.get("TESTING"):
        return False
    now = time.monotonic()
    attempts = [moment for moment in _RATE_LIMIT_BUCKETS.get(key, ()) if now - moment < window_seconds]
    attempts.append(now)
    _RATE_LIMIT_BUCKETS[key] = attempts
    if len(_RATE_LIMIT_BUCKETS) > _RATE_LIMIT_MAX_KEYS:
        for stale_key, stale_attempts in list(_RATE_LIMIT_BUCKETS.items()):
            if not stale_attempts or now - stale_attempts[-1] >= window_seconds:
                _RATE_LIMIT_BUCKETS.pop(stale_key, None)
    return len(attempts) > limit


def rotate_session_version(db, user_id, keep_current_session=False):
    """Invalidate existing sessions for a user after a credential change.

    Returns the new session version. Pass `keep_current_session` when the
    change was made by the account owner, so other devices are signed out
    without forcing the person who just changed their password to sign in again.
    """
    db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (user_id,))
    row = db.execute("SELECT session_version FROM users WHERE id = ?", (user_id,)).fetchone()
    version = row["session_version"] if row else None
    if keep_current_session and version is not None and session.get("user_id") == user_id:
        session["session_version"] = version
    return version


def client_scope():
    """Best-effort identifier for the remote client, used for rate limiting."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(**kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("admin.login"))
        if not g.user["is_admin"]:
            abort(403)
        return view(**kwargs)
    return wrapped


def require_csrf():
    expected = session.get("csrf_token", "")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, supplied):
        abort(400, "Invalid CSRF token")
