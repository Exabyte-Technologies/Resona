import pytest

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
        "ADMIN_PASSWORD": "",
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def csrf(client, path="/auth/register"):
    client.get(path)
    with client.session_transaction() as session:
        return session["csrf_token"]


@pytest.fixture()
def registered(client):
    token = csrf(client)
    response = client.post("/auth/register", data={
        "csrf_token": token,
        "username": "listener",
        "email": "listener@example.com",
        "password": "healing-sound-123",
    })
    assert response.status_code == 302
    return client
