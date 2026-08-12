import pytest
from capjs_server.testing import solve

from resona import create_app
from resona.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "STORAGE_ROOT": str(tmp_path / "storage"),
        "CLOSEAI_API_KEY": "",
        "RESEND_API_KEY": "",
        "RESEND_FROM_EMAIL": "",
        "RESEND_FROM_NAME": "Resona",
        "PUBLIC_BASE_URL": "https://resona.test",
        "ADMIN_PASSWORD": "",
        "CAPTCHA_CHALLENGE_COUNT": 2,
        "CAPTCHA_CHALLENGE_DIFFICULTY": 1,
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client, path="/auth/register"):
    client.get(path)
    with client.session_transaction() as session:
        return session["csrf_token"]


def solve_captcha(client):
    challenge = client.post("/captcha/challenge").get_json()
    solutions = solve(challenge["token"], challenge["challenge"])
    result = client.post("/captcha/redeem", json={"token": challenge["token"], "solutions": solutions})
    assert result.status_code == 200
    return result.get_json()["token"]


@pytest.fixture()
def captcha(client):
    return lambda: solve_captcha(client)


@pytest.fixture()
def registered(client, captcha):
    token = csrf(client)
    response = client.post("/auth/register", data={
        "csrf_token": token,
        "cap-token": captcha(),
        "display_name": "Listener",
        "username": "listener",
        "email": "listener@example.com",
        "password": "healing-sound-123",
    })
    assert response.status_code == 302
    with client.session_transaction() as session:
        verification_token = session["testing_verification_token"]
    assert client.get(f"/auth/verify-email/{verification_token}").status_code == 302
    login_csrf = csrf(client, "/auth/login")
    response = client.post("/auth/login", data={
        "csrf_token": login_csrf,
        "cap-token": captcha(),
        "identity": "listener",
        "password": "healing-sound-123",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/player/")
    return client
