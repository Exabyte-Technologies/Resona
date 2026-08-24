import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


PREFIX = "fernet:v1:"


def _fernet():
    secret_key = current_app.config["SECRET_KEY"]
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")
    derived = hashlib.sha256(b"resona-settings-v1\0" + secret_key).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_setting(value):
    value = str(value or "")
    if not value:
        return ""
    return PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_setting(value):
    value = str(value or "")
    if not value:
        return ""
    if not value.startswith(PREFIX):
        raise InvalidToken
    return _fernet().decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
