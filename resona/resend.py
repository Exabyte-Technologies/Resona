import html

import requests
from flask import current_app

from .db import get_db


RESEND_EMAILS_URL = "https://api.resend.com/emails"


def _setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"].strip() if row else ""


def get_resend_settings():
    database_key = _setting("resend_api_key")
    return {
        "api_key": database_key or current_app.config.get("RESEND_API_KEY", "").strip(),
        "from_email": _setting("resend_from_email") or current_app.config.get("RESEND_FROM_EMAIL", "").strip(),
        "from_name": _setting("resend_from_name") or current_app.config.get("RESEND_FROM_NAME", "Resona").strip(),
        "key_source": "admin" if database_key else ("environment" if current_app.config.get("RESEND_API_KEY", "").strip() else "none"),
    }


def resend_is_configured():
    settings = get_resend_settings()
    return bool(settings["api_key"] and settings["from_email"])


def send_email(recipient, subject, html_body, text_body):
    settings = get_resend_settings()
    if not settings["api_key"] or not settings["from_email"]:
        raise RuntimeError("Resend API key and sender email must be configured")
    sender = settings["from_email"]
    if settings["from_name"]:
        sender = f'{settings["from_name"]} <{sender}>'
    response = requests.post(
        RESEND_EMAILS_URL,
        headers={
            "Authorization": f'Bearer {settings["api_key"]}',
            "Content-Type": "application/json",
        },
        json={
            "from": sender,
            "to": [recipient],
            "subject": subject,
            "html": html_body,
            "text": text_body,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def send_welcome_email(recipient, username):
    safe_username = html.escape(username)
    return send_email(
        recipient,
        "Welcome to Resona",
        (
            "<h1>Welcome to Resona</h1>"
            f"<p>Hi {safe_username}, your private healing-music space is ready.</p>"
            "<p>You can now shape your binaural beats, ambient layers, and interface from Resona.</p>"
        ),
        f"Hi {username}, your private Resona healing-music space is ready.",
    )


def send_email_verification_email(recipient, display_name, verification_url, purpose="registration"):
    safe_name = html.escape(display_name)
    safe_url = html.escape(verification_url, quote=True)
    changing = purpose == "email_change"
    subject = "Confirm your new Resona email" if changing else "Verify your Resona email"
    introduction = "Confirm this new email address for your Resona account." if changing else "Verify your email address to finish setting up your Resona account."
    return send_email(
        recipient,
        subject,
        (
            f"<p>Hi {safe_name},</p>"
            f"<p>{html.escape(introduction)}</p>"
            f'<p><a href="{safe_url}">Verify my email</a></p>'
            "<p>This link expires in 24 hours. If you did not request this, you can safely ignore it.</p>"
        ),
        f"Hi {display_name}, {introduction}\n\n{verification_url}\n\nThis link expires in 24 hours.",
    )


def send_password_reset_email(recipient, username, reset_url):
    safe_username = html.escape(username)
    safe_url = html.escape(reset_url, quote=True)
    return send_email(
        recipient,
        "Reset your Resona password",
        (
            f"<p>Hi {safe_username},</p>"
            "<p>Use the link below to reset your Resona password. It expires in 30 minutes.</p>"
            f'<p><a href="{safe_url}">Reset my password</a></p>'
            "<p>If you did not request this, you can safely ignore this email.</p>"
        ),
        (
            f"Hi {username}, reset your Resona password within 30 minutes:\n\n"
            f"{reset_url}\n\nIf you did not request this, you can safely ignore this email."
        ),
    )
