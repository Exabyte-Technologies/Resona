import base64
import hashlib
import json
import logging
import re
import secrets
import time
from urllib.parse import urlsplit

import requests
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from cryptography.fernet import InvalidToken
from flask import Blueprint, current_app, flash, g, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

from .db import get_db
from .security import USERNAME_RE
from .secret_store import decrypt_setting
from .user_controls import user_control_enabled
from .user_storage import delete_user_storage, initialize_user_storage, user_root


exabyte_bp = Blueprint("exabyte", __name__, url_prefix="/auth")
oauth = OAuth()
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
REQUIRED_SCOPES = {"openid", "profile", "email"}
PROVIDER = "exabyte"
TRANSACTION_MAX_AGE = 600


def _setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"].strip() if row else ""


def get_exabyte_settings():
    client_id = _setting("exabyte_oidc_client_id")
    encrypted_secret = _setting("exabyte_oidc_client_secret_encrypted")
    try:
        client_secret = decrypt_setting(encrypted_secret)
    except (InvalidToken, ValueError, UnicodeError):
        current_app.logger.error("The saved Exabyte client secret could not be decrypted")
        client_secret = ""
    return {
        "issuer": current_app.config.get("EXABYTE_OIDC_ISSUER", "").strip().rstrip("/"),
        "client_id": client_id,
        "client_secret": client_secret,
        "callback_url": current_app.config.get("EXABYTE_OIDC_CALLBACK_URL", "").strip(),
        "scopes": current_app.config.get("EXABYTE_OIDC_SCOPES", "").strip(),
        "configured": bool(client_id and client_secret),
    }


def exabyte_is_configured():
    settings = get_exabyte_settings()
    return bool(
        settings["configured"]
        and settings["issuer"]
        and settings["callback_url"]
        and set(settings["scopes"].split()) == REQUIRED_SCOPES
    )


def init_exabyte_oidc(app):
    # Authlib debug logging can include PKCE verifier material; never emit it.
    logging.getLogger("authlib").setLevel(logging.WARNING)
    oauth.init_app(app)


def _register_client(settings):
    scopes = settings["scopes"].split()
    if set(scopes) != REQUIRED_SCOPES:
        current_app.logger.error("Exabyte OIDC is disabled because scopes must be exactly: openid profile email")
        return None
    return oauth.register(
        name=PROVIDER,
        overwrite=True,
        client_id=settings["client_id"],
        client_secret=settings["client_secret"],
        server_metadata_url=f"{settings['issuer']}/.well-known/openid-configuration",
        client_kwargs={
            "scope": " ".join(scopes),
            "code_challenge_method": "S256",
            "token_endpoint_auth_method": "client_secret_basic",
            "id_token_signed_response_alg": "RS256",
        },
    )


def external_identity(user_id):
    return get_db().execute(
        "SELECT * FROM external_identities WHERE provider = ? AND user_id = ?",
        (PROVIDER, user_id),
    ).fetchone()


def safe_local_path(value):
    candidate = str(value or url_for("player.index"))
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/") or candidate.startswith("//"):
        return url_for("player.index")
    return candidate


def _client():
    if not exabyte_is_configured():
        return None
    return _register_client(get_exabyte_settings())


def _validated_metadata(client):
    metadata = client.load_server_metadata()
    if metadata.get("issuer", "").rstrip("/") != current_app.config["EXABYTE_OIDC_ISSUER"]:
        raise ValueError("discovery issuer mismatch")
    required = ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri", "revocation_endpoint")
    if any(not metadata.get(name) for name in required):
        raise ValueError("incomplete provider discovery")
    return metadata


def begin_exabyte_authorization(purpose="login", link_user_id=None, next_path=None):
    client = _client()
    if client is None:
        flash("Sign in with Exabyte is not configured yet.", "error")
        return redirect(url_for("account.settings") if purpose == "link" else url_for("auth.login"))
    if not user_control_enabled("login"):
        flash("User sign-in has been disabled by the administrator.", "error")
        return redirect(url_for("account.settings") if purpose == "link" else url_for("auth.login"))
    session["exabyte_oidc_started_at"] = int(time.time())
    session["exabyte_oidc_purpose"] = purpose
    session["exabyte_oidc_link_user_id"] = link_user_id
    session["exabyte_oidc_after_login"] = safe_local_path(next_path)
    try:
        _validated_metadata(client)
        return client.authorize_redirect(current_app.config["EXABYTE_OIDC_CALLBACK_URL"])
    except (OAuthError, requests.RequestException, ValueError) as exc:
        current_app.logger.warning("Exabyte authorization discovery failed (%s)", type(exc).__name__)
        _clear_oidc_transaction(clear_login_session=purpose == "login")
        flash("Exabyte Accounts is temporarily unavailable. Please try again later.", "error")
        return redirect(url_for("account.settings") if purpose == "link" else url_for("auth.login"))


def _clear_oidc_transaction(clear_login_session=False):
    keys = [key for key in session if key.startswith("_state_exabyte_") or key.startswith("exabyte_oidc_")]
    if clear_login_session:
        session.clear()
    else:
        for key in keys:
            session.pop(key, None)


def _failure(message, purpose="login", status=302):
    _clear_oidc_transaction(clear_login_session=purpose == "login")
    flash(message, "error")
    target = "account.settings" if purpose == "link" and g.get("user") else "auth.login"
    response = redirect(url_for(target))
    response.status_code = status
    return response


def _jwt_algorithm(id_token):
    try:
        encoded = id_token.split(".", 1)[0]
        encoded += "=" * (-len(encoded) % 4)
        return json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8")).get("alg")
    except (AttributeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _claims(value):
    subject = value.get("sub")
    email = str(value.get("email") or "").strip().lower()
    if not isinstance(subject, str) or not subject.startswith("usr_"):
        raise ValueError("invalid subject")
    if not EMAIL_RE.fullmatch(email) or value.get("email_verified") is not True:
        raise ValueError("verified email required")
    display_name = str(value.get("name") or value.get("preferred_username") or "Exabyte user").strip()[:80]
    preferred_username = str(value.get("preferred_username") or "").strip().lower()
    return {
        "subject": subject,
        "email": email,
        "display_name": display_name or "Exabyte user",
        "preferred_username": preferred_username,
    }


def _available_username(preferred_username, subject):
    db = get_db()
    if preferred_username != "demo" and USERNAME_RE.fullmatch(preferred_username):
        if db.execute("SELECT 1 FROM users WHERE username = ?", (preferred_username,)).fetchone() is None:
            return preferred_username
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    for length in range(12, 25, 2):
        candidate = f"exabyte_{digest[:length]}"
        if db.execute("SELECT 1 FROM users WHERE username = ?", (candidate,)).fetchone() is None:
            return candidate
    raise ValueError("could not allocate username")


def _update_identity(identity, claims):
    db = get_db()
    owner = db.execute(
        "SELECT id FROM users WHERE email = ? AND id != ?", (claims["email"], identity["user_id"])
    ).fetchone()
    warning = None
    if owner:
        warning = "Your Exabyte email is already used by another Resona account, so the previous Resona email was retained."
        db.execute(
            "UPDATE users SET display_name = ?, email_verified_at = CURRENT_TIMESTAMP WHERE id = ?",
            (claims["display_name"], identity["user_id"]),
        )
    else:
        db.execute(
            "UPDATE users SET display_name = ?, email = ?, email_verified_at = CURRENT_TIMESTAMP WHERE id = ?",
            (claims["display_name"], claims["email"], identity["user_id"]),
        )
    db.execute(
        "UPDATE external_identities SET email = ?, display_name = ?, email_verified = 1, sync_warning = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (claims["email"], claims["display_name"], warning, identity["id"]),
    )
    db.commit()
    return identity["user_id"], warning


def _provision_user(claims):
    if not user_control_enabled("registration"):
        raise PermissionError("New account registration has been disabled by the administrator.")
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email = ?", (claims["email"],)).fetchone():
        raise FileExistsError("An existing Resona account already uses this email. Sign in with its password and link Exabyte from account settings.")
    username = _available_username(claims["preferred_username"], claims["subject"])
    workspace_created = False
    try:
        password_hash = generate_password_hash(secrets.token_urlsafe(48), method="pbkdf2:sha256:600000")
        cursor = db.execute(
            "INSERT INTO users(username, email, display_name, email_verified_at, password_hash, password_login_enabled) VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, 0)",
            (username, claims["email"], claims["display_name"], password_hash),
        )
        db.execute(
            "INSERT INTO external_identities(user_id, provider, subject, issuer, email, display_name, email_verified) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (cursor.lastrowid, PROVIDER, claims["subject"], current_app.config["EXABYTE_OIDC_ISSUER"], claims["email"], claims["display_name"]),
        )
        initialize_user_storage(username)
        workspace_created = True
        db.commit()
        return cursor.lastrowid, None
    except Exception:
        db.rollback()
        if workspace_created or user_root(username).exists():
            delete_user_storage(username)
        raise


def _link_user(user_id, claims):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None or not user["password_login_enabled"] or user["is_demo"]:
        raise PermissionError("This account cannot link an external identity.")
    existing_subject = db.execute(
        "SELECT user_id FROM external_identities WHERE provider = ? AND subject = ?", (PROVIDER, claims["subject"])
    ).fetchone()
    if existing_subject and existing_subject["user_id"] != user_id:
        raise FileExistsError("This Exabyte identity is already linked to another Resona account.")
    if external_identity(user_id):
        raise FileExistsError("This Resona account is already linked to Exabyte.")
    email_owner = db.execute("SELECT id FROM users WHERE email = ? AND id != ?", (claims["email"], user_id)).fetchone()
    if email_owner:
        raise FileExistsError("That Exabyte email belongs to another Resona account, so linking was not completed.")
    db.execute(
        "INSERT INTO external_identities(user_id, provider, subject, issuer, email, display_name, email_verified) VALUES (?, ?, ?, ?, ?, ?, 1)",
        (user_id, PROVIDER, claims["subject"], current_app.config["EXABYTE_OIDC_ISSUER"], claims["email"], claims["display_name"]),
    )
    db.execute(
        "UPDATE users SET display_name = ?, email = ?, email_verified_at = CURRENT_TIMESTAMP WHERE id = ?",
        (claims["display_name"], claims["email"], user_id),
    )
    db.commit()
    return user_id, None


def _revoke_tokens(client, token):
    endpoint = (client.server_metadata or {}).get("revocation_endpoint")
    if not endpoint:
        try:
            endpoint = client.load_server_metadata().get("revocation_endpoint")
        except Exception:
            endpoint = None
    if not endpoint:
        current_app.logger.warning("Exabyte token revocation endpoint was unavailable")
        return
    settings = get_exabyte_settings()
    for value in (token.get("refresh_token"), token.get("access_token")):
        if not value:
            continue
        try:
            requests.post(
                endpoint,
                auth=(settings["client_id"], settings["client_secret"]),
                data={"token": value},
                timeout=5,
            )
        except requests.RequestException:
            current_app.logger.warning("Exabyte token revocation could not be reached")


@exabyte_bp.get("/exabyte")
def login():
    if not user_control_enabled("login"):
        return redirect(url_for("auth.login"))
    return begin_exabyte_authorization(next_path=request.args.get("next"))


@exabyte_bp.get("/exabyte/callback")
def callback():
    purpose = session.get("exabyte_oidc_purpose", "login")
    started_at = session.get("exabyte_oidc_started_at", 0)
    if not started_at or int(time.time()) - int(started_at) > TRANSACTION_MAX_AGE:
        return _failure("The Exabyte sign-in request expired. Please start again.", purpose)
    if not user_control_enabled("login"):
        return _failure("User sign-in has been disabled by the administrator.", purpose)
    if request.args.get("error"):
        return _failure("Exabyte sign-in was cancelled or denied.", purpose)
    client = _client()
    if client is None:
        return _failure("Sign in with Exabyte is unavailable because its server configuration is incomplete.", purpose)
    token = {}
    try:
        _validated_metadata(client)
        token = client.authorize_access_token()
        if _jwt_algorithm(token.get("id_token")) != "RS256":
            raise ValueError("unexpected ID token algorithm")
        id_claims = dict(token["userinfo"])
        current_claims = dict(client.userinfo(token=token))
        if current_claims.get("sub") != id_claims.get("sub"):
            raise ValueError("userinfo subject mismatch")
        claims = _claims(current_claims)
        identity = get_db().execute(
            "SELECT * FROM external_identities WHERE provider = ? AND subject = ?",
            (PROVIDER, claims["subject"]),
        ).fetchone()
        if identity and identity["issuer"].rstrip("/") != current_app.config["EXABYTE_OIDC_ISSUER"]:
            raise ValueError("identity issuer mismatch")
        if purpose == "link":
            expected_user_id = session.get("exabyte_oidc_link_user_id")
            if not expected_user_id or session.get("user_id") != expected_user_id:
                raise PermissionError("The account-linking session is no longer valid.")
            user_id, warning = _link_user(expected_user_id, claims)
        elif identity:
            user_id, warning = _update_identity(identity, claims)
        else:
            user_id, warning = _provision_user(claims)
        user = get_db().execute("SELECT session_version FROM users WHERE id = ?", (user_id,)).fetchone()
        destination = safe_local_path(session.get("exabyte_oidc_after_login"))
    except PermissionError as exc:
        return _failure(str(exc), purpose)
    except FileExistsError as exc:
        return _failure(str(exc), purpose)
    except (OAuthError, requests.RequestException, KeyError, ValueError) as exc:
        current_app.logger.warning("Exabyte callback validation failed (%s)", type(exc).__name__)
        return _failure("Exabyte sign-in could not be securely validated. Please try again.", purpose)
    except Exception as exc:
        current_app.logger.error("Exabyte account provisioning failed (%s)", type(exc).__name__)
        return _failure("The Resona account could not be prepared. No changes were kept.", purpose)
    finally:
        if token:
            _revoke_tokens(client, token)

    session.clear()
    session["user_id"] = user_id
    session["session_version"] = user["session_version"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    if warning:
        flash(warning, "error")
    elif purpose == "link":
        flash("Your Exabyte identity is now linked to this Resona account.", "success")
    return redirect(destination)
