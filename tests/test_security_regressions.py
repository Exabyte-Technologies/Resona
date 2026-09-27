"""Regression tests for the authentication and redirect hardening pass.

Each test corresponds to a finding that was reproducible against the code
before the fix, so a failure here means a real vulnerability has come back.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from resona.db import get_db, purge_expired_credentials
from resona.security import safe_local_path

from conftest import csrf


RESET_PASSWORD = "brand-new-password-99"


def _insert_reset_token(app, user_id):
    token = secrets.token_urlsafe(36)
    digest = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO password_resets(user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, digest, expires),
        )
        db.commit()
    return token


def _user_id(app, username="listener"):
    with app.app_context():
        return get_db().execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()["id"]


def _version(app, user_id):
    with app.app_context():
        return get_db().execute("SELECT session_version FROM users WHERE id = ?", (user_id,)).fetchone()["session_version"]


# --- Unsafe redirect -------------------------------------------------------


def test_safe_local_path_rejects_every_origin_escaping_spelling():
    # Browsers rewrite backslashes to slashes and drop control characters, so
    # all of these resolve to https://evil.com/ from a page on this origin.
    for payload in ("//evil.com", "/\\evil.com", "/\\/evil.com", "/\t/evil.com", "/\\\\evil.com",
                    "https://evil.com", "http:/\\/evil.com", "evil.com", ""):
        assert safe_local_path(payload, "/player/") == "/player/", payload

    for payload in ("/player/", "/account/", "/storage/listener/index.html"):
        assert safe_local_path(payload, "/player/") == payload, payload


def test_login_next_parameter_cannot_leave_the_origin(client, captcha):
    token = csrf(client)
    response = client.post("/auth/register", data={
        "csrf_token": token,
        "cap-token": captcha(),
        "display_name": "Redirect",
        "username": "redirectuser",
        "email": "redirect@example.com",
        "password": "healing-sound-123",
        "accept_terms": "1",
        "accept_privacy": "1",
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        verification = session["testing_verification_token"]
    assert client.get(f"/auth/verify-email/{verification}").status_code == 302

    for payload in ("/\\evil.com", "/\\/evil.com", "/\t/evil.com", "//evil.com"):
        login_csrf = csrf(client, "/auth/login")
        response = client.post(
            f"/auth/login?next={payload}",
            data={
                "csrf_token": login_csrf,
                "cap-token": captcha(),
                "identity": "redirectuser",
                "password": "healing-sound-123",
            },
        )
        assert response.status_code == 302, payload
        location = response.headers["Location"]
        assert "evil.com" not in location, payload
        assert location.startswith("/"), payload


def test_login_next_parameter_still_honours_local_paths(client, captcha):
    token = csrf(client)
    client.post("/auth/register", data={
        "csrf_token": token,
        "cap-token": captcha(),
        "display_name": "Local Next",
        "username": "localnext",
        "email": "localnext@example.com",
        "password": "healing-sound-123",
        "accept_terms": "1",
        "accept_privacy": "1",
    })
    with client.session_transaction() as session:
        verification = session["testing_verification_token"]
    client.get(f"/auth/verify-email/{verification}")

    login_csrf = csrf(client, "/auth/login")
    response = client.post("/auth/login?next=/account/", data={
        "csrf_token": login_csrf,
        "cap-token": captcha(),
        "identity": "localnext",
        "password": "healing-sound-123",
    })
    assert response.headers["Location"].endswith("/account/")


# --- User enumeration ------------------------------------------------------


def test_unknown_identity_still_pays_the_full_password_hash_cost(client, captcha, monkeypatch):
    """A missing account must not return faster than a real one."""
    import resona.security as security_module

    calls = []
    real_check = security_module.check_password_hash

    def counting_check(stored_hash, password):
        calls.append(stored_hash)
        return real_check(stored_hash, password)

    monkeypatch.setattr(security_module, "check_password_hash", counting_check)

    token = csrf(client)
    client.post("/auth/login", data={
        "csrf_token": token,
        "cap-token": captcha(),
        "identity": "no-such-account",
        "password": "healing-sound-123",
    })
    assert len(calls) == 1
    # The dummy hash is a real pbkdf2 value, so the work factor matches.
    assert calls[0].startswith("pbkdf2:sha256:600000$")


def test_unknown_and_wrong_password_return_the_same_response(client, captcha):
    """Both failure modes must be indistinguishable to an unauthenticated caller."""
    client.post("/auth/register", data={
        "csrf_token": csrf(client),
        "cap-token": captcha(),
        "display_name": "Known",
        "username": "knownuser",
        "email": "known@example.com",
        "password": "healing-sound-123",
        "accept_terms": "1",
        "accept_privacy": "1",
    })
    with client.session_transaction() as session:
        verification = session["testing_verification_token"]
    client.get(f"/auth/verify-email/{verification}")

    missing = client.post("/auth/login", data={
        "csrf_token": csrf(client, "/auth/login"),
        "cap-token": captcha(),
        "identity": "no-such-account",
        "password": "healing-sound-123",
    })
    wrong = client.post("/auth/login", data={
        "csrf_token": csrf(client, "/auth/login"),
        "cap-token": captcha(),
        "identity": "knownuser",
        "password": "not-the-password",
    })
    assert missing.status_code == wrong.status_code == 200
    assert "Location" not in missing.headers and "Location" not in wrong.headers
    # Neither failure may establish a session.
    with client.session_transaction() as session:
        assert "user_id" not in session
    assert client.get("/player/").headers["Location"].startswith("/auth/login")


# --- Session invalidation --------------------------------------------------


def test_password_reset_signs_out_existing_sessions(app, registered, captcha):
    client = registered
    user_id = _user_id(app)
    assert client.get("/player/").status_code == 200
    assert _version(app, user_id) == 0

    token = _insert_reset_token(app, user_id)
    response = client.post(f"/auth/reset/{token}", data={
        "csrf_token": csrf(client, f"/auth/reset/{token}"),
        "password": RESET_PASSWORD,
    })
    assert response.status_code == 302
    assert _version(app, user_id) == 1

    # The session that existed before the reset must no longer be accepted.
    assert client.get("/player/").headers["Location"].startswith("/auth/login")

    logged_in = client.post("/auth/login", data={
        "csrf_token": csrf(client, "/auth/login"),
        "cap-token": captcha(),
        "identity": "listener",
        "password": RESET_PASSWORD,
    })
    assert logged_in.status_code == 302
    assert client.get("/player/").status_code == 200


def test_account_password_change_keeps_current_session_but_bumps_version(app, registered):
    from conftest import solve_captcha

    client = registered
    user_id = _user_id(app)
    assert _version(app, user_id) == 0

    response = client.post("/account/", data={
        "csrf_token": csrf(client, "/account/"),
        "cap-token": solve_captcha(client),
        "action": "update",
        "display_name": "Listener",
        "email": "listener@example.com",
        "current_password": "healing-sound-123",
        "new_password": "another-new-password-77",
    })
    assert response.status_code in (200, 302)
    assert _version(app, user_id) == 1
    # The person who changed their own password is not signed out.
    assert client.get("/player/").status_code == 200


# --- Password reset table hygiene ------------------------------------------


def test_purge_removes_expired_and_spent_reset_tokens(app):
    user_id = _user_id(app, "demo") if False else 1
    now = datetime.now(timezone.utc)
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO password_resets(user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, "expired", (now - timedelta(hours=2)).isoformat()),
        )
        db.execute(
            "INSERT INTO password_resets(user_id, token_hash, expires_at, used_at) VALUES (?, ?, ?, ?)",
            (user_id, "spent", (now + timedelta(hours=2)).isoformat(),
             (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")),
        )
        db.execute(
            "INSERT INTO password_resets(user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (user_id, "fresh", (now + timedelta(hours=2)).isoformat()),
        )
        db.commit()

    with app.app_context():
        purge_expired_credentials()
        remaining = {row["token_hash"] for row in get_db().execute("SELECT token_hash FROM password_resets").fetchall()}

    assert remaining == {"fresh"}


# --- Brute force throttling -------------------------------------------------


def test_login_is_throttled_after_repeated_failures(app, client, captcha):
    from conftest import solve_captcha

    # The limiter is disabled under TESTING so the suite stays deterministic.
    app.config["TESTING"] = False
    try:
        statuses = []
        for _ in range(12):
            token = csrf(client, "/auth/login")
            statuses.append(client.post("/auth/login", data={
                "csrf_token": token,
                "cap-token": solve_captcha(client),
                "identity": "listener",
                "password": "definitely-wrong",
            }).status_code)
        assert 429 in statuses
        assert statuses.index(429) < len(statuses) - 1
    finally:
        app.config["TESTING"] = True


# --- Response headers -------------------------------------------------------


def test_security_headers_include_csp_hardening_and_hsts(client):
    response = client.get("/auth/login", base_url="https://resona.test")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    csp = response.headers["Content-Security-Policy"]
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "Strict-Transport-Security" in response.headers


def test_session_cookie_is_secure_by_default():
    import os
    from resona import _session_cookie_secure

    os.environ.pop("SESSION_COOKIE_SECURE", None)
    os.environ["PUBLIC_BASE_URL"] = "https://resona.example.com"
    assert _session_cookie_secure() is True

    os.environ["PUBLIC_BASE_URL"] = "http://localhost:5000"
    assert _session_cookie_secure() is False

    os.environ["SESSION_COOKIE_SECURE"] = "1"
    assert _session_cookie_secure() is True
    os.environ.pop("SESSION_COOKIE_SECURE", None)
