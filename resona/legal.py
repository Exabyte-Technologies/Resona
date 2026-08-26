from flask import Blueprint, render_template

from .db import get_db


legal_bp = Blueprint("legal", __name__)
TERMS_VERSION = "2026-08-26"
PRIVACY_VERSION = "2026-08-26"


def consent_submitted(form):
    return form.get("accept_terms") == "1" and form.get("accept_privacy") == "1"


def record_legal_acceptance(user_id, context):
    get_db().execute(
        "INSERT INTO legal_acceptances(user_id, terms_version, privacy_version, context) VALUES (?, ?, ?, ?)",
        (user_id, TERMS_VERSION, PRIVACY_VERSION, context),
    )


@legal_bp.get("/terms")
def terms():
    return render_template("legal/terms.html", policy_version=TERMS_VERSION)


@legal_bp.get("/privacy")
def privacy():
    return render_template("legal/privacy.html", policy_version=PRIVACY_VERSION)
