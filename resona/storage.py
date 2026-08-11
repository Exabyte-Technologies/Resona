import mimetypes

from flask import Blueprint, abort, g, send_file

from .security import login_required
from .user_storage import safe_path


storage_bp = Blueprint("storage", __name__, url_prefix="/storage")


@storage_bp.get("/<username>/<path:asset_path>")
@login_required
def asset(username, asset_path):
    if g.user["username"] != username and not g.user["is_admin"]:
        abort(403)
    try:
        path = safe_path(username, asset_path)
    except ValueError:
        abort(404)
    if not path.is_file() or path.is_symlink():
        abort(404)
    mime, _ = mimetypes.guess_type(path.name)
    response = send_file(path, mimetype=mime or "application/octet-stream", conditional=True)
    response.headers["Cache-Control"] = "private, no-store"
    if path.suffix.lower() == ".html":
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'"
    return response

