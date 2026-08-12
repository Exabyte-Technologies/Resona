import os
import sys
from pathlib import Path


REQUIRED = (
    "SECRET_KEY",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "CLOSEAI_API_KEY",
    "LETSENCRYPT_EMAIL",
)


def quote(value):
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Deployment secrets cannot contain newlines or NUL bytes")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: render_env.py OUTPUT_PATH")
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit("Missing required GitHub secrets: " + ", ".join(missing))
    values = {
        "SECRET_KEY": os.environ["SECRET_KEY"],
        "DATABASE_PATH": "instance/resona.sqlite3",
        "RESONA_STORAGE_ROOT": "instance/storage",
        "RESONA_USER_QUOTA_BYTES": "1073741824",
        "ADMIN_USERNAME": os.environ["ADMIN_USERNAME"],
        "ADMIN_PASSWORD": os.environ["ADMIN_PASSWORD"],
        "CLOSEAI_BASE_URL": "https://api.openai-proxy.org",
        "CLOSEAI_API_KEY": os.environ["CLOSEAI_API_KEY"],
        "CLOSEAI_MODEL": "gpt-5.6-sol",
        "LETSENCRYPT_EMAIL": os.environ["LETSENCRYPT_EMAIL"],
        "PUBLIC_BASE_URL": "https://resona.neuorise.com",
        "SESSION_COOKIE_SECURE": "1",
    }
    output = Path(sys.argv[1])
    output.write_text("".join(f"{name}={quote(value)}\n" for name, value in values.items()), encoding="utf-8")
    output.chmod(0o600)


if __name__ == "__main__":
    main()
