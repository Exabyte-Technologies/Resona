from .db import get_db


USER_CONTROL_KEYS = {
    "registration": "user_registration_enabled",
    "login": "user_login_enabled",
    "password_recovery": "password_recovery_enabled",
    "profile_editing": "profile_editing_enabled",
}


def get_user_controls():
    rows = get_db().execute(
        f"SELECT key, value FROM settings WHERE key IN ({','.join('?' for _ in USER_CONTROL_KEYS)})",
        tuple(USER_CONTROL_KEYS.values()),
    ).fetchall()
    values = {row["key"]: row["value"] for row in rows}
    return {name: values.get(key, "1") == "1" for name, key in USER_CONTROL_KEYS.items()}


def user_control_enabled(name):
    if name not in USER_CONTROL_KEYS:
        raise ValueError("Unknown user control")
    return get_user_controls()[name]
