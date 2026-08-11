import json

from resona.closeai import API_KEY_PLACEHOLDER
from resona.db import get_db
from resona.user_storage import safe_path, usage_bytes


def session_csrf(client):
    with client.session_transaction() as session:
        return session["csrf_token"]


def test_registration_creates_isolated_default_workspace(app, registered):
    with app.app_context():
        user_columns = {row["name"] for row in get_db().execute("PRAGMA table_info(users)").fetchall()}
        assert "api_key" not in user_columns
        nav = json.loads(safe_path("listener", "nav.json").read_text())
        assert len(nav["nav_items"]) == 1
        assert nav["default_page"] == "pages/home.html"
        assert nav["nav_items"][0]["label"] == "Home"
        assert nav["nav_items"][0]["icon_path"] == "static/icons/home.svg"
        assert safe_path("listener", "static/icons/home.svg").exists()
        assert "configure(engine)" in safe_path("listener", "static/custom_synth.js").read_text()
        assert "data-playback-toggle" in safe_path("listener", "pages/home.html").read_text()
        assert "data-band=\"6\"" in safe_path("listener", "pages/home.html").read_text()
        assert not safe_path("listener", "pages/mixer.html").exists()
        assert safe_path("listener", "memory/notes.md").exists()
        assert usage_bytes("listener") > 0
    response = registered.get("/player/")
    assert response.status_code == 200
    assert b"How can I help you heal today?" in response.data
    assert b'id="current-playback"' not in response.data
    assert response.data.count(b'class="nav-item ') == 1
    assert b"Log out" in response.data


def test_player_migrates_only_untouched_legacy_default_workspace(app, registered):
    from resona.user_storage import _legacy_home_page

    with app.app_context():
        legacy_nav = {
            "default_page": "pages/home.html",
            "nav_items": [
                {"id": "home", "label": "Generator", "target_html": "pages/home.html"},
                {"id": "mixer", "label": "Mixer", "target_html": "pages/mixer.html"},
                {"id": "binaural", "label": "Binaural", "target_html": "pages/binaural.html"},
                {"id": "noise", "label": "Noise", "target_html": "pages/noise.html"},
                {"id": "history", "label": "History", "target_html": "pages/history.html"},
                {"id": "profile", "label": "Profile", "target_html": "pages/profile.html"},
            ],
        }
        safe_path("listener", "nav.json").write_text(json.dumps(legacy_nav), encoding="utf-8")
        safe_path("listener", "pages/home.html").write_text(_legacy_home_page(), encoding="utf-8")
        safe_path("listener", "pages/mixer.html").write_text("legacy page", encoding="utf-8")
    response = registered.get("/player/")
    assert response.status_code == 200
    with app.app_context():
        migrated = json.loads(safe_path("listener", "nav.json").read_text())
        assert [item["id"] for item in migrated["nav_items"]] == ["home"]
        assert "data-playback-toggle" in safe_path("listener", "pages/home.html").read_text()
        assert not safe_path("listener", "pages/mixer.html").exists()


def test_authentication_gate_blocks_other_user_storage(app, registered):
    with app.app_context():
        from resona.user_storage import initialize_user_storage
        initialize_user_storage("other")
    assert registered.get("/storage/other/pages/home.html").status_code == 403
    assert registered.get("/storage/listener/pages/home.html").status_code == 200


def test_path_traversal_is_rejected(app):
    with app.app_context():
        for path in ("../secret", "/etc/passwd", "pages/../../secret"):
            try:
                safe_path("listener", path)
                assert False, path
            except ValueError:
                pass


def test_csrf_is_required_for_state_changes(registered):
    response = registered.post("/player/api/history", json={"title": "Quiet", "config": {}})
    assert response.status_code == 400


def test_history_is_owned_by_current_user(app, registered):
    response = registered.post(
        "/player/api/history",
        headers={"X-CSRF-Token": session_csrf(registered)},
        json={"title": "Night rain", "config": {"beat": 4}, "duration_seconds": 120},
    )
    assert response.status_code == 201
    assert registered.get("/player/api/history").get_json()[0]["title"] == "Night rain"


def test_agent_fails_safely_without_external_key(app, registered):
    response = registered.post(
        "/agent/modify",
        headers={"X-CSRF-Token": session_csrf(registered)},
        json={"prompt": "Make it calmer", "credential": API_KEY_PLACEHOLDER},
    )
    assert response.status_code == 422
    assert "No server-side CloseAI API key" in response.get_json()["error"]
    with app.app_context():
        run = get_db().execute("SELECT status FROM agent_runs ORDER BY id DESC").fetchone()
        assert run["status"] == "failed"


def test_agent_rejects_missing_or_replaced_client_placeholder(registered):
    for credential in (None, "sk-client-secret", "{{SOME_OTHER_KEY}}"):
        response = registered.post(
            "/agent/modify",
            headers={"X-CSRF-Token": session_csrf(registered)},
            json={"prompt": "Make it calmer", "credential": credential},
        )
        assert response.status_code == 400


def test_server_proxy_resolves_admin_key_without_exposing_it(app, monkeypatch):
    from resona.closeai import chat

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"summary":"ok","files":{}}'}}]}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("resona.closeai.requests.post", fake_post)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE settings SET value = ? WHERE key = 'closeai_api_key'", ("sk-server-only",))
        db.execute("UPDATE settings SET value = ? WHERE key = 'closeai_model'", ("gpt-test",))
        db.commit()
        result = chat([{"role": "user", "content": "hello"}], API_KEY_PLACEHOLDER)

    assert json.loads(result)["summary"] == "ok"
    assert captured["headers"]["Authorization"] == "Bearer sk-server-only"
    assert captured["json"]["model"] == "gpt-test"
    assert API_KEY_PLACEHOLDER not in json.dumps(captured)


def test_generated_content_cannot_escape_shell(app):
    from resona.user_storage import validate_content
    with app.app_context():
        for name, content in (
            ("pages/bad.html", "content"),
            ("pages/bad.html", "Updated with a light blue scheme"),
            ("pages/bad.html", '<script>alert(1)</script>'),
            ("static/bad.js", 'window.parent.document.body.remove()'),
            ("static/bad.js", 'document.querySelector("#app")'),
        ):
            try:
                validate_content(name, content)
                assert False, content
            except ValueError:
                pass


def test_admin_login_is_separate_and_protected(app, client):
    with app.app_context():
        from werkzeug.security import generate_password_hash
        db = get_db()
        db.execute("INSERT INTO users(username,email,password_hash,is_admin) VALUES (?,?,?,1)", ("rootadmin", "admin@example.com", generate_password_hash("long-admin-password", method="pbkdf2:sha256:600000")))
        db.commit()
    client.get("/admin/login")
    token = session_csrf(client)
    response = client.post("/admin/login", data={"csrf_token": token, "identity": "rootadmin", "password": "long-admin-password"})
    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200
    response = client.post("/admin/provider", data={
        "csrf_token": session_csrf(client),
        "base_url": "https://api.openai-proxy.org",
        "model": "gpt-admin-test",
        "api_key": "sk-never-render-this",
        "agent_max_steps": "160",
    })
    assert response.status_code == 302
    dashboard = client.get("/admin/")
    assert b"Configured via admin" in dashboard.data
    assert b"sk-never-render-this" not in dashboard.data
    assert b'value="160"' in dashboard.data


def test_environment_admin_is_created_and_can_log_in(tmp_path):
    from resona import create_app

    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "admin-bootstrap-test",
        "DATABASE": str(tmp_path / "admin.sqlite3"),
        "STORAGE_ROOT": str(tmp_path / "storage"),
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "admin123",
        "ADMIN_EMAIL": "admin@example.com",
    })
    client = app.test_client()
    client.get("/admin/login")
    response = client.post("/admin/login", data={
        "csrf_token": session_csrf(client),
        "identity": "admin",
        "password": "admin123",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")
    assert client.get("/admin/").status_code == 200


def test_admin_can_edit_user_and_move_private_workspace(app, client):
    from resona.user_storage import initialize_user_storage, user_root
    from werkzeug.security import check_password_hash, generate_password_hash

    with app.app_context():
        db = get_db()
        admin_id = db.execute("INSERT INTO users(username,email,password_hash,is_admin) VALUES (?,?,?,1)", ("rootadmin", "root@example.com", generate_password_hash("long-admin-password", method="pbkdf2:sha256:600000"))).lastrowid
        user_id = db.execute("INSERT INTO users(username,email,password_hash) VALUES (?,?,?)", ("listener", "listener@example.com", generate_password_hash("healing-sound-123", method="pbkdf2:sha256:600000"))).lastrowid
        db.commit()
        initialize_user_storage("rootadmin")
        initialize_user_storage("listener")
    with client.session_transaction() as session:
        session["user_id"] = admin_id
        session["csrf_token"] = "admin-csrf"

    response = client.post(f"/admin/users/{user_id}/edit", data={
        "csrf_token": "admin-csrf",
        "username": "renewed_listener",
        "email": "renewed@example.com",
        "password": "replacement-password",
        "is_admin": "1",
    })
    assert response.status_code == 302
    with app.app_context():
        edited = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        assert edited["username"] == "renewed_listener"
        assert edited["email"] == "renewed@example.com"
        assert edited["is_admin"] == 1
        assert check_password_hash(edited["password_hash"], "replacement-password")
        assert not user_root("listener").exists()
        assert user_root("renewed_listener").exists()


def test_admin_can_delete_user_and_workspace_but_not_final_admin(app, client):
    from resona.user_storage import initialize_user_storage, user_root
    from werkzeug.security import generate_password_hash

    with app.app_context():
        db = get_db()
        admin_id = db.execute("INSERT INTO users(username,email,password_hash,is_admin) VALUES (?,?,?,1)", ("rootadmin", "root@example.com", generate_password_hash("long-admin-password", method="pbkdf2:sha256:600000"))).lastrowid
        user_id = db.execute("INSERT INTO users(username,email,password_hash) VALUES (?,?,?)", ("listener", "listener@example.com", generate_password_hash("healing-sound-123", method="pbkdf2:sha256:600000"))).lastrowid
        db.commit()
        initialize_user_storage("rootadmin")
        initialize_user_storage("listener")
    with client.session_transaction() as session:
        session["user_id"] = admin_id
        session["csrf_token"] = "admin-csrf"

    wrong_confirmation = client.post(f"/admin/users/{user_id}/delete", data={"csrf_token": "admin-csrf", "confirm_username": "wrong"})
    assert wrong_confirmation.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()

    deleted = client.post(f"/admin/users/{user_id}/delete", data={"csrf_token": "admin-csrf", "confirm_username": "listener"})
    assert deleted.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone() is None
        assert not user_root("listener").exists()

    final_admin = client.post(f"/admin/users/{admin_id}/delete", data={"csrf_token": "admin-csrf", "confirm_username": "rootadmin"})
    assert final_admin.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT id FROM users WHERE id = ?", (admin_id,)).fetchone()


def test_autonomous_agent_uses_multiple_file_tools_until_finish(app, registered, monkeypatch):
    calls = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "one", "type": "function", "function": {"name": "list_files", "arguments": '{"path":"pages"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "two", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"pages/home.html"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "three", "type": "function", "function": {"name": "replace_in_file", "arguments": json.dumps({"path": "pages/home.html", "old_text": "Find your frequency", "new_text": "Find your restorative frequency"})}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "four", "type": "function", "function": {"name": "validate_workspace", "arguments": "{}"}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "five", "type": "function", "function": {"name": "finish", "arguments": '{"summary":"Updated and validated the healing page."}'}}]},
    ]

    observed_progress = []

    def fake_completion(messages, tools, credential):
        assert credential == API_KEY_PLACEHOLDER
        assert any(tool["function"]["name"] == "move_path" for tool in tools)
        assert any(tool["function"]["name"] == "invoke_skill" for tool in tools)
        assert any(tool["function"]["name"] == "restore_snapshot" for tool in tools)
        progress = [message["content"] for message in messages if message["role"] == "system" and message["content"].startswith("Run progress:")]
        observed_progress.append(progress[-1])
        return calls.pop(0)

    monkeypatch.setattr("resona.agent_runtime.agent_completion", fake_completion)
    response = registered.post(
        "/agent/modify",
        headers={"X-CSRF-Token": session_csrf(registered)},
        json={"prompt": "Rename my healing page and make sure it works", "credential": API_KEY_PLACEHOLDER},
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result["steps"] == 5
    assert result["tools"] == ["list_files", "read_file", "replace_in_file", "validate_workspace", "finish"]
    assert observed_progress[0].startswith("Run progress: this is step 1 of 80; 79 steps remain")
    assert observed_progress[-1].startswith("Run progress: this is step 5 of 80; 75 steps remain")
    with app.app_context():
        assert "Find your restorative frequency" in safe_path("listener", "pages/home.html").read_text()
        assert safe_path("listener", f"snapshots/{result['snapshot']}/pages/home.html").exists()


def test_workspace_tools_move_and_delete_only_inside_user_sandbox(app, registered):
    from resona.agent_runtime import WorkspaceTools
    from resona.user_storage import create_snapshot

    with app.app_context():
        user_id = get_db().execute("SELECT id FROM users WHERE username = 'listener'").fetchone()["id"]
        tools = WorkspaceTools("listener", user_id)
        valid_page = '<!doctype html><html><head><link rel="stylesheet" href="../static/user.css"></head><body><main><h1>Temporary interface</h1><p>This is a complete temporary interface used to verify safe workspace file operations and validation behavior.</p></main></body></html>'
        tools.execute("write_file", {"path": "pages/temporary.html", "content": valid_page})
        tools.execute("move_path", {"source": "pages/temporary.html", "destination": "pages/moved.html"})
        assert safe_path("listener", "pages/moved.html").exists()
        tools.execute("delete_path", {"path": "pages/moved.html"})
        assert not safe_path("listener", "pages/moved.html").exists()
        try:
            tools.execute("delete_path", {"path": "nav.json"})
            assert False, "nav.json deletion should be blocked"
        except ValueError:
            pass
        snapshot_id = create_snapshot("listener")
        original = safe_path("listener", "pages/home.html").read_text()
        tools.execute("replace_in_file", {"path": "pages/home.html", "old_text": "Find your frequency", "new_text": "Changed after snapshot"})
        listed = json.loads(tools.execute("list_snapshots", {}))["snapshots"]
        assert snapshot_id in listed
        tools.execute("restore_snapshot", {"snapshot_id": snapshot_id})
        assert safe_path("listener", "pages/home.html").read_text() == original
