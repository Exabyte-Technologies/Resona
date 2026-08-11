import functools
import hmac
import re

from flask import abort, g, redirect, request, session, url_for


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")


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

