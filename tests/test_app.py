import base64
import json

from resona.closeai import API_KEY_PLACEHOLDER
from resona.db import get_db
from resona.user_storage import safe_path, usage_bytes


def session_csrf(client):
    with client.session_transaction() as session:
        return session["csrf_token"]


def test_default_agent_prompt_and_model_are_configured_from_bundled_defaults(app):
    from resona.closeai import get_provider_settings
    from resona.db import AGENT_MODEL_VERSION, AGENT_PROMPT_VERSION, default_agent_prompt

    with app.app_context():
        settings = {row["key"]: row["value"] for row in get_db().execute(
            "SELECT key, value FROM settings WHERE key IN ('agent_system_prompt','agent_prompt_version','closeai_model','agent_model_version')"
        ).fetchall()}
        assert settings["agent_system_prompt"] == default_agent_prompt()
        assert settings["agent_system_prompt"].startswith("You are **Resona AI**")
        assert settings["agent_system_prompt"].endswith("Only then call `finish`.")
        assert "comfortable to use on a phone" in settings["agent_system_prompt"]
        assert "at least 16px" in settings["agent_system_prompt"]
        assert settings["agent_prompt_version"] == AGENT_PROMPT_VERSION
        assert settings["closeai_model"] == "gpt-5.6-sol"
        assert settings["agent_model_version"] == AGENT_MODEL_VERSION
        assert get_provider_settings()["model"] == "gpt-5.6-sol"


def test_agent_prompt_and_model_admin_edits_survive_database_reinitialization(app):
    from resona.db import init_db

    with app.app_context():
        db = get_db()
        db.execute("UPDATE settings SET value = 'Custom administrator prompt' WHERE key = 'agent_system_prompt'")
        db.execute("UPDATE settings SET value = 'custom-admin-model' WHERE key = 'closeai_model'")
        db.commit()
        init_db()
        assert get_db().execute("SELECT value FROM settings WHERE key = 'agent_system_prompt'").fetchone()["value"] == "Custom administrator prompt"
        assert get_db().execute("SELECT value FROM settings WHERE key = 'closeai_model'").fetchone()["value"] == "custom-admin-model"


def test_production_deployment_preserves_instance_and_uses_expected_host():
    from pathlib import Path

    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/deploy.yml").read_text()
    service = (root / "deploy/resona.service").read_text()
    nginx = (root / "deploy/resona.nginx").read_text()
    http_nginx = (root / "deploy/resona-http.nginx").read_text()
    finalize = (root / "deploy/finalize-ubuntu.sh").read_text()
    assert "DEPLOY_HOST: 157.245.192.56" in workflow
    assert "DEPLOY_USER: resonahost" in workflow
    assert "secrets.SSH_PRIVATE_KEY" in workflow
    assert "--exclude='instance/'" in workflow
    assert "--exclude='.env'" in workflow
    assert "DATABASE_PATH" in (root / "deploy/render_env.py").read_text()
    assert "User=resonahost" in service
    assert "run:app" in service
    assert "server_name resona.neuorise.com" in nginx
    assert "listen 443 ssl" in nginx
    assert "ssl_certificate /etc/letsencrypt/live/resona.neuorise.com/fullchain.pem" in nginx
    assert "Strict-Transport-Security" in nginx
    assert "return 301 https://resona.neuorise.com$request_uri" in http_nginx
    assert "proxy_pass http://unix:/run/resona/resona.sock" in nginx
    assert "instance/backups" in finalize
    assert "certbot certonly" in finalize
    assert "certbot.timer" in finalize
    assert "https://resona.neuorise.com/" in finalize
    assert "pip check" in finalize
    assert "nginx -t" in finalize
    rendered_env = (root / "deploy/render_env.py").read_text()
    assert '"PUBLIC_BASE_URL": "https://resona.neuorise.com"' in rendered_env
    assert '"SESSION_COOKIE_SECURE": "1"' in rendered_env
    assert '"LETSENCRYPT_EMAIL"' in rendered_env
    assert '"RESEND_API_KEY"' in rendered_env
    assert '"RESEND_FROM_EMAIL"' in rendered_env
    assert "secrets.RESEND_API_KEY" in workflow
    assert "secrets.RESEND_FROM_EMAIL" in workflow


def test_registration_creates_isolated_default_workspace(app, registered):
    with app.app_context():
        user_columns = {row["name"] for row in get_db().execute("PRAGMA table_info(users)").fetchall()}
        assert "api_key" not in user_columns
        nav = json.loads(safe_path("listener", "nav.json").read_text())
        assert len(nav["nav_items"]) == 2
        assert nav["default_page"] == "pages/home.html"
        assert nav["nav_items"][0]["label"] == "Home"
        assert nav["nav_items"][0]["icon_path"] == "static/icons/home.svg"
        assert nav["nav_items"][1]["icon"] == "gears"
        assert nav["nav_items"][1]["icon_path"] == "static/icons/advanced.svg"
        assert safe_path("listener", "static/icons/home.svg").exists()
        assert "M16 13.5a2.5 2.5" in safe_path("listener", "static/icons/advanced.svg").read_text()
        assert "engine.config.carrier = engine.config.ambient.droneFrequency" in safe_path("listener", "static/custom_synth.js").read_text()
        home = safe_path("listener", "pages/home.html").read_text()
        advanced = safe_path("listener", "pages/advanced.html").read_text()
        assert all(f'data-mode="{mode}"' in home for mode in ("sleep", "meditation", "focus", "awake"))
        assert "data-session-toggle" in home and "data-master-volume" in home
        assert "data-playback-toggle" in advanced
        assert "data-band=\"6\"" in advanced
        assert "Ambient music generator" in advanced
        assert all(f'data-atmosphere="{mood}"' in advanced for mood in ("restore", "melancholy", "deep"))
        assert all(f'data-synth-parameter="{name}"' in advanced for name in ("warmth", "movement", "space", "texture", "shimmer", "output"))
        assert 'data-tonal-source="manual"' in advanced
        assert 'data-tonal-source="generated"' in advanced
        assert all(f'data-tonal-centre="{midi}"' in advanced for midi in (48, 50, 51, 53, 55, 57))
        assert "data-ambient=\"drone\"" not in advanced
        assert "data-drone-frequency" not in advanced
        assert "data-chord-card" in advanced
        assert "data-chord-duration" in advanced
        assert "data-chord-temperature" in advanced
        assert "data-chord-top-k" in advanced
        assert "data-chord-continuous" in advanced
        assert "data-chord-pipeline" in advanced
        assert "data-chord-transition" in advanced
        assert "data-binaural-chord-transition" in advanced
        assert all(layer in advanced for layer in ("Drone", "Harmonic pad", "Analog drift", "Weather texture", "High shimmer", "Felt anchor"))
        assert "volume-mixer-card" in advanced
        assert "data-volume=\"binaural\"" in advanced
        assert "data-volume=\"ambient\"" in advanced
        assert "Noise generator" in advanced
        assert "data-noise=\"white\"" in advanced
        assert "data-noise=\"brown\"" in advanced
        assert "data-noise-toggle" in advanced
        assert "data-volume=\"noise\"" in advanced
        user_css = safe_path("listener", "static/user.css").read_text()
        assert "mobile-readability-v1" in user_css
        assert "ambient-synth-v1" in user_css
        assert "@media(max-width:600px)" in user_css
        assert "body{overflow-x:hidden;font-size:16px" in user_css
        assert "min-height:48px" in user_css
        assert not safe_path("listener", "pages/mixer.html").exists()
        assert safe_path("listener", "memory/notes.md").exists()
        assert safe_path("listener", "memory/plan.md").exists()
        assert safe_path("listener", "memory/changelog.md").exists()
        assert usage_bytes("listener") > 0
        model_manifest = safe_path("listener", "static/chord-model/model.json")
        model_weights = safe_path("listener", "static/chord-model/weights.bin")
        assert model_manifest.exists() and model_weights.stat().st_size == 2_790_324
        architecture = json.loads(model_manifest.read_text())["architecture"]
        assert architecture == {"representation":"triad", "modelType":"lstm", "embeddingDimension":16, "hiddenDimension":256, "layers":1, "partSizes":[18,56,18], "vocabularySize":3861, "maximumContext":50}
    response = registered.get("/player/")
    assert response.status_code == 200
    assert b"How can I help you heal today?" in response.data
    assert b'id="current-playback"' not in response.data
    assert response.data.count(b'class="nav-item ') == 2
    assert b"Log out" in response.data
    assert b'id="reset-original-ui"' in response.data


def test_registration_requires_email_verification_before_login_and_exposes_server_owned_account_page(app, client, captcha):
    client.get("/auth/register")
    response = client.post("/auth/register", data={
        "csrf_token": session_csrf(client),
        "cap-token": captcha(),
        "display_name": "Unverified Listener",
        "username": "unverified",
        "email": "unverified@example.com",
        "password": "healing-sound-123",
    })
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")
    assert client.get("/player/").headers["Location"].startswith("/auth/login")
    with client.session_transaction() as session:
        token = session["testing_verification_token"]
    with app.app_context():
        user = get_db().execute("SELECT display_name, email_verified_at FROM users WHERE username = 'unverified'").fetchone()
        assert user["display_name"] == "Unverified Listener"
        assert user["email_verified_at"] is None

    client.get("/auth/login")
    denied = client.post("/auth/login", data={
        "csrf_token": session_csrf(client), "cap-token": captcha(),
        "identity": "unverified", "password": "healing-sound-123",
    })
    assert denied.status_code == 403
    assert b"Verify your email before signing in" in denied.data

    response = client.get(f"/auth/verify-email/{token}")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/login")
    with app.app_context():
        assert get_db().execute("SELECT email_verified_at FROM users WHERE username = 'unverified'").fetchone()["email_verified_at"]
    client.get("/auth/login")
    allowed = client.post("/auth/login", data={
        "csrf_token": session_csrf(client), "cap-token": captcha(),
        "identity": "unverified", "password": "healing-sound-123",
    })
    assert allowed.status_code == 302
    player = client.get("/player/")
    assert b'id="account-button"' in player.data
    assert b'id="account-dialog"' in player.data
    assert b"cannot be changed by Resona AI" in player.data


def test_account_updates_display_name_password_and_verifies_new_email(app, registered, captcha):
    response = registered.post("/account/", headers={"Accept": "application/json"}, data={
        "csrf_token": session_csrf(registered),
        "cap-token": captcha(),
        "display_name": "Quiet Listener",
        "email": "new-listener@example.com",
        "current_password": "healing-sound-123",
        "new_password": "a-new-healing-password",
    })
    assert response.status_code == 200
    assert response.get_json()["pending_email"] == "new-listener@example.com"
    with app.app_context():
        user = get_db().execute("SELECT display_name, email FROM users WHERE username = 'listener'").fetchone()
        assert dict(user) == {"display_name": "Quiet Listener", "email": "listener@example.com"}
    with registered.session_transaction() as session:
        verification_token = session["testing_verification_token"]
    registered.get(f"/auth/verify-email/{verification_token}")
    with app.app_context():
        user = get_db().execute("SELECT email, password_hash FROM users WHERE username = 'listener'").fetchone()
        assert user["email"] == "new-listener@example.com"
        from werkzeug.security import check_password_hash
        assert check_password_hash(user["password_hash"], "a-new-healing-password")


def test_captcha_tokens_are_single_use_for_account_changes(registered, captcha):
    token = captcha()
    payload = {
        "csrf_token": session_csrf(registered), "cap-token": token,
        "display_name": "Listener", "email": "listener@example.com",
    }
    headers = {"Accept": "application/json"}
    assert registered.post("/account/", headers=headers, data=payload).status_code == 200
    assert registered.post("/account/", headers=headers, data=payload).status_code == 400


def test_every_third_agent_request_within_an_hour_requires_captcha(app, registered, captcha):
    with app.app_context():
        user_id = get_db().execute("SELECT id FROM users WHERE username = 'listener'").fetchone()["id"]
        get_db().executemany(
            "INSERT INTO agent_runs(user_id, prompt, status) VALUES (?, ?, 'failed')",
            [(user_id, "first",), (user_id, "second",)],
        )
        get_db().commit()
    payload = {"prompt": "Make it calmer", "credential": API_KEY_PLACEHOLDER}
    response = registered.post("/agent/modify", headers={"X-CSRF-Token": session_csrf(registered)}, json=payload)
    assert response.status_code == 403
    assert response.get_json()["captcha_required"] is True
    payload["cap_token"] = captcha()
    response = registered.post("/agent/modify", headers={"X-CSRF-Token": session_csrf(registered)}, json=payload)
    assert response.status_code == 503
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) AS count FROM agent_runs WHERE user_id = ?", (user_id,)).fetchone()["count"] == 3


def test_agent_captcha_dialog_enables_continue_from_solve_event_token():
    from pathlib import Path

    player_js = (Path(__file__).parents[1] / "resona/static/js/player.js").read_text()
    assert "event.detail?.token" in player_js
    assert "continueAgentRequest.disabled = !agentCaptchaToken" in player_js
    assert "sendAgentRequest(pendingAgentPrompt, token)" in player_js


def test_resend_sends_registration_and_password_reset_emails_without_exposing_secrets(app, client, captcha, monkeypatch):
    captured = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "email-test-id"}

    def fake_post(url, **kwargs):
        captured.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr("resona.resend.requests.post", fake_post)
    with app.app_context():
        db = get_db()
        for key, value in (
            ("resend_api_key", "re_server_only"),
            ("resend_from_email", "hello@resona.example"),
            ("resend_from_name", "Resona Care"),
        ):
            db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
        db.commit()

    token = client.get("/auth/register")
    assert token.status_code == 200
    response = client.post("/auth/register", data={
        "csrf_token": session_csrf(client),
        "cap-token": captcha(),
        "display_name": "Email User",
        "username": "emailuser",
        "email": "emailuser@example.com",
        "password": "healing-sound-123",
    })
    assert response.status_code == 302
    assert captured[0]["url"] == "https://api.resend.com/emails"
    assert captured[0]["headers"]["Authorization"] == "Bearer re_server_only"
    assert captured[0]["json"]["from"] == "Resona Care <hello@resona.example>"
    assert captured[0]["json"]["to"] == ["emailuser@example.com"]
    assert captured[0]["json"]["subject"] == "Verify your Resona email"
    assert "https://resona.test/auth/verify-email/" in captured[0]["json"]["text"]

    client.post("/auth/logout", data={"csrf_token": session_csrf(client)})
    client.get("/auth/forgot")
    response = client.post("/auth/forgot", data={
        "csrf_token": session_csrf(client),
        "email": "emailuser@example.com",
    })
    assert response.status_code == 200
    assert b"Development reset link" not in response.data
    reset_request = captured[1]
    assert reset_request["headers"]["Authorization"] == "Bearer re_server_only"
    assert "https://resona.test/auth/reset/" in reset_request["json"]["text"]
    assert "re_server_only" not in response.get_data(as_text=True)


def test_admin_manages_resend_settings_without_rendering_api_key(app, client):
    from werkzeug.security import generate_password_hash

    with app.app_context():
        db = get_db()
        admin_id = db.execute(
            "INSERT INTO users(username,email,password_hash,is_admin) VALUES (?,?,?,1)",
            ("mailadmin", "mailadmin@example.com", generate_password_hash("long-admin-password", method="pbkdf2:sha256:600000")),
        ).lastrowid
        db.commit()
    with client.session_transaction() as session:
        session["user_id"] = admin_id
        session["csrf_token"] = "admin-email-csrf"

    response = client.post("/admin/resend", data={
        "csrf_token": "admin-email-csrf",
        "from_name": "Resona Mail",
        "from_email": "noreply@resona.example",
        "api_key": "re_admin_secret",
    })
    assert response.status_code == 302
    dashboard = client.get("/admin/")
    assert b"Email ready" in dashboard.data
    assert b"Configured via admin" in dashboard.data
    assert b"noreply@resona.example" in dashboard.data
    assert b"re_admin_secret" not in dashboard.data
    with app.app_context():
        assert get_db().execute("SELECT value FROM settings WHERE key = 'resend_api_key'").fetchone()["value"] == "re_admin_secret"


def test_resend_delivery_failure_blocks_unverifiable_registration(app, client, captcha, monkeypatch):
    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("delivery unavailable")

    monkeypatch.setattr("resona.resend.requests.post", fail_delivery)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE settings SET value = 're_failing_key' WHERE key = 'resend_api_key'")
        db.execute("UPDATE settings SET value = 'hello@resona.example' WHERE key = 'resend_from_email'")
        db.commit()

    client.get("/auth/register")
    response = client.post("/auth/register", data={
        "csrf_token": session_csrf(client),
        "cap-token": captcha(),
        "display_name": "Mail Failure",
        "username": "mailfailure",
        "email": "mailfailure@example.com",
        "password": "healing-sound-123",
    })
    assert response.status_code == 200
    assert b"Email verification is temporarily unavailable" in response.data
    with app.app_context():
        assert not get_db().execute("SELECT id FROM users WHERE username = 'mailfailure'").fetchone()


def test_original_ui_button_resets_only_ui_and_keeps_recovery_data(app, registered):
    from resona.user_storage import default_home_page, safe_path, write_user_file

    custom_page = '<!doctype html><html><head><style>body{color:white}</style></head><body><main><h1>Custom retreat</h1><p>A complete custom page that should be recoverable after restoring the original interface.</p></main></body></html>'
    custom_nav = {
        "default_page": "pages/custom.html",
        "nav_items": [{"id": "custom", "label": "Retreat", "target_html": "pages/custom.html"}],
    }
    with app.app_context():
        write_user_file("listener", "pages/custom.html", custom_page)
        write_user_file("listener", "static/custom-extra.css", ".custom{color:lavender}\n")
        write_user_file("listener", "nav.json", json.dumps(custom_nav))
        notes_path = safe_path("listener", "memory/notes.md")
        notes_path.write_text(notes_path.read_text() + "Keep my calming preferences.\n", encoding="utf-8")
        safe_path("listener", "data/saved-note.txt").write_text("Keep my front-end data.\n", encoding="utf-8")
        user_id = get_db().execute("SELECT id FROM users WHERE username = 'listener'").fetchone()["id"]
        get_db().execute("INSERT INTO playback_history(user_id,title,config_json) VALUES (?,?,?)", (user_id, "Keep this session", "{}"))
        get_db().commit()

    response = registered.post(
        "/agent/reset-ui",
        headers={"X-CSRF-Token": session_csrf(registered)},
        json={},
    )
    assert response.status_code == 200
    snapshot = response.get_json()["snapshot"]
    with app.app_context():
        nav = json.loads(safe_path("listener", "nav.json").read_text())
        assert nav["default_page"] == "pages/home.html"
        assert [item["id"] for item in nav["nav_items"]] == ["home", "advanced"]
        assert safe_path("listener", "pages/home.html").read_text() == default_home_page()
        assert not safe_path("listener", "pages/custom.html").exists()
        assert not safe_path("listener", "static/custom-extra.css").exists()
        assert "Keep my calming preferences" in safe_path("listener", "memory/notes.md").read_text()
        assert "Keep my front-end data" in safe_path("listener", "data/saved-note.txt").read_text()
        assert safe_path("listener", f"snapshots/{snapshot}/pages/custom.html").read_text() == custom_page
        assert get_db().execute("SELECT title FROM playback_history WHERE user_id = ?", (user_id,)).fetchone()["title"] == "Keep this session"


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
        assert [item["id"] for item in migrated["nav_items"]] == ["home", "advanced"]
        assert "data-session-toggle" in safe_path("listener", "pages/home.html").read_text()
        assert "data-playback-toggle" in safe_path("listener", "pages/advanced.html").read_text()
        assert not safe_path("listener", "pages/mixer.html").exists()


def test_player_upgrades_only_exact_legacy_advanced_synth_page(app, registered):
    from resona.user_storage import _legacy_default_advanced_page

    with app.app_context():
        advanced_path = safe_path("listener", "pages/advanced.html")
        advanced_path.write_text(_legacy_default_advanced_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        upgraded = safe_path("listener", "pages/advanced.html").read_text()
        assert 'data-synth-parameter="warmth"' in upgraded
        customized = _legacy_default_advanced_page().replace("Ambient music generator", "My custom ambient studio", 1)
        safe_path("listener", "pages/advanced.html").write_text(customized, encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        preserved = safe_path("listener", "pages/advanced.html").read_text()
        assert "My custom ambient studio" in preserved
        assert 'data-synth-parameter="warmth"' not in preserved


def test_player_upgrades_untouched_single_home_to_ambient_generator(app, registered):
    from resona.user_storage import _single_home_v2_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v2_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        styles = safe_path("listener", "static/user.css").read_text()
        assert "Ambient music generator" in home
        assert "data-ambient-toggle" in home
        assert "single-home-v7" in styles


def test_player_upgrades_untouched_ambient_home_to_master_volume_card(app, registered):
    from resona.user_storage import _single_home_v3_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v3_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert "volume-mixer-card" in home
        assert "data-volume=\"binaural\"" in home
        assert "data-volume=\"ambient\"" in home


def test_player_upgrades_untouched_volume_home_to_noise_generator(app, registered):
    from resona.user_storage import _single_home_v4_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v4_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert "Noise generator" in home
        assert "data-noise=\"white\"" in home
        assert "data-noise=\"brown\"" in home
        assert "data-volume=\"noise\"" in home


def test_player_upgrades_untouched_noise_home_to_full_ambient_synth(app, registered):
    from resona.user_storage import _single_home_v5_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v5_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert 'data-atmosphere="deep"' in home
        assert 'data-synth-parameter="warmth"' in home
        assert 'data-tonal-centre="53"' in home


def test_player_upgrades_drone_control_to_generated_and_manual_tonal_sources(app, registered):
    from resona.user_storage import _single_home_v6_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v6_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert 'data-tonal-source="manual"' in home
        assert 'data-tonal-source="generated"' in home
        assert "Generated progression" in home


def test_player_upgrades_shared_carrier_home_to_private_chord_model(app, registered):
    from resona.user_storage import _single_home_v7_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v7_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert "Private on-device model" in home
        assert "Generate &amp; apply" in home
        assert "data-chord-length" in home


def test_player_upgrades_pad_only_chords_to_harmonized_ambient_chords(app, registered):
    from resona.user_storage import _single_home_v8_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v8_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert "every pitched synth layer and the binaural carrier" in home
        assert "automatically selects Generated progression" in home


def test_player_upgrades_harmonized_chords_to_continuous_set_mode(app, registered):
    from resona.user_storage import _single_home_v9_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v9_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        styles = safe_path("listener", "static/user.css").read_text()
        assert "data-chord-continuous" in home
        assert "data-chord-pipeline-playing" in home
        assert "data-chord-pipeline-ready" in home
        assert "data-chord-pipeline-generating" in home
        assert "single-home-v10" in styles


def test_player_upgrades_continuous_mode_to_chord_following_binaural_audio(app, registered):
    from resona.user_storage import _single_home_v10_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v10_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert "every pitched synth layer and the binaural carrier" in home
        assert "automatically selects Generated progression" in home


def test_player_upgrades_chord_following_audio_to_transition_controls(app, registered):
    from resona.user_storage import _single_home_v11_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v11_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        styles = safe_path("listener", "static/user.css").read_text()
        assert "Chord transition timing" in home
        assert "data-chord-transition" in home
        assert "data-binaural-chord-transition" in home
        assert home.count("<output>Instant</output>") == 2
        assert "single-home-v12" in styles


def test_player_upgrades_chord_duration_to_two_through_120_seconds(app, registered):
    from resona.user_storage import _single_home_v12_page

    with app.app_context():
        safe_path("listener", "pages/home.html").write_text(_single_home_v12_page(), encoding="utf-8")
    assert registered.get("/player/").status_code == 200
    with app.app_context():
        home = safe_path("listener", "pages/advanced.html").read_text()
        assert 'min="2" max="120" step="1" value="4" data-chord-duration' in home
        assert 'min="1" max="12" step="0.5" value="4" data-chord-duration' not in home


def test_audio_engine_clamps_chord_duration_to_two_through_120_seconds(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    chord_setter = audio.split("    setChordProgression(chords, duration)", 1)[1].split("    toggleChordProgression()", 1)[0]
    assert "Math.max(2, Math.min(120, Number(duration) || 4))" in chord_setter


def test_home_modes_map_to_new_atmospheres_roots_and_binaural_beats(registered):
    player = registered.get("/static/js/player.js").get_data(as_text=True)
    assert "sleep:{ beat:2, atmosphere:'deep', rootMidi:57 }" in player
    assert "meditation:{ beat:6, atmosphere:'deep', rootMidi:53 }" in player
    assert "focus:{ beat:10, atmosphere:'restore', rootMidi:57 }" in player
    assert "awake:{ beat:18, atmosphere:'restore', rootMidi:48 }" in player
    assert "setAtmosphere(preset.atmosphere)" in player
    assert "setTonalCentre(preset.rootMidi)" in player


def test_synth_bridge_actions_are_allowlisted_and_state_synchronized(registered):
    player = registered.get("/static/js/player.js").get_data(as_text=True)
    advanced = registered.get("/storage/listener/pages/advanced.html").get_data(as_text=True)
    for action in ("setAtmosphere", "setAmbientParameter", "setTonalSource", "setTonalCentre"):
        assert f"data.action === '{action}'" in player
        assert f"action:'{action}'" in advanced
    assert "['restore','melancholy','deep'].includes(data.value)" in player
    assert "['warmth','movement','space','texture','shimmer','output'].includes(data.name)" in player
    assert "['manual','generated'].includes(data.value)" in player
    assert "[48,50,51,53,55,57].includes(Number(data.value))" in player
    assert "data.config?.ambient?.parameters" in advanced
    assert "data.config?.ambient?.tonalSource" in advanced


def test_atmosphere_presets_and_tonal_sources_preserve_independent_output_stages(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    assert "restore:{ warmth:72, movement:50, space:62, texture:24, shimmer:46 }" in audio
    assert "melancholy:{ warmth:64, movement:38, space:74, texture:31, shimmer:35 }" in audio
    assert "deep:{ warmth:80, movement:25, space:82, texture:20, shimmer:21 }" in audio
    assert "if (this.config.ambient.tonalSource === 'manual')" in audio
    assert "if (source === 'generated' && !this.config.ambient.chordProgression.length) return false" in audio
    assert "this.clearManualHarmonyTimer()" in audio
    assert "this.ambientSynthMaster.gain" in audio
    assert "this.ambientOutput.gain" in audio
    assert ".6 * scale * this.config.volumes.ambient / 50" in audio
    assert "this.config.master / 100" in audio


def test_ambient_stop_cancels_evolution_and_uses_restart_safe_cleanup(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    stop = audio.split("    stopAmbient()", 1)[1].split("    toggleAmbient()", 1)[0]
    assert "this.ambientTimers.forEach(clearInterval)" in stop
    assert "clearInterval(this.chordTimer)" in stop
    assert "const now = this.context.currentTime, sources = this.ambientSources.slice(), nodes = this.ambientNodes.slice()" in stop
    assert "token = ++this.ambientShutdownToken" in stop
    assert "if (this.ambientShutdownToken === token)" in stop
    assert "source.stop()" in stop and "node.disconnect()" in stop


def test_authentication_gate_blocks_other_user_storage(app, registered):
    with app.app_context():
        from resona.user_storage import initialize_user_storage
        initialize_user_storage("other")
    assert registered.get("/storage/other/pages/home.html").status_code == 403
    assert registered.get("/storage/listener/pages/home.html").status_code == 200


def test_user_pages_use_opaque_script_sandbox_and_strict_csp(registered):
    page = registered.get("/storage/listener/pages/home.html")
    policy = page.headers["Content-Security-Policy"]
    assert "script-src 'unsafe-inline'" in policy
    assert "connect-src 'none'" in policy
    assert "object-src 'none'" in policy
    assert b"data-resona-bridge" in page.data
    assert b"data-resona-stylesheet=\"static/user.css\"" in page.data
    assert b"window.ResonaFiles" in page.data
    assert b'<link rel="stylesheet"' not in page.data
    player = registered.get("/player/")
    assert b'id="dynamic-page"' in player.data
    assert b'sandbox="allow-scripts"' in player.data
    assert b"allow-same-origin" not in player.data
    assert b"fonts.googleapis.com" not in registered.get("/static/css/app.css").data
    assert b"data-volume" in page.data
    assert b"setVolume" in registered.get("/static/js/player.js").data
    assert b"setVolume(name, value)" in registered.get("/static/js/audio.js").data
    assert b"'binaural','ambient','noise'" in registered.get("/static/js/player.js").data
    assert b"toggleAmbient" in registered.get("/static/js/player.js").data
    assert b"startAmbient()" in registered.get("/static/js/audio.js").data
    assert b"generateChordProgression" in registered.get("/static/js/player.js").data
    assert b"new window.ResonaChordModel" in registered.get("/static/js/player.js").data
    assert b"/api/generate" not in registered.get("/static/js/player.js").data
    assert b"runContext(chordIds)" in registered.get("/static/js/chord-model.js").data
    assert b"fetch(base + 'model.json')" in registered.get("/static/js/chord-model.js").data
    assert b"data-ambient-toggle" in page.data
    assert b"toggleNoise" in registered.get("/static/js/player.js").data
    assert b"startNoise()" in registered.get("/static/js/audio.js").data
    assert b"data-noise-toggle" in page.data


def test_frontend_persistent_file_api_supports_full_lifecycle(app, registered):
    headers = {"X-CSRF-Token": session_csrf(registered)}
    endpoint = "/player/api/files"

    assert registered.post(endpoint, json={"action": "write", "path": "journal/entries.json", "content": '[{"mood":"calm"}]'}).status_code == 400
    written = registered.post(endpoint, headers=headers, json={"action": "write", "path": "journal/entries.json", "content": '[{"mood":"calm"}]'})
    assert written.status_code == 200
    assert written.get_json()["path"] == "journal/entries.json"

    read = registered.post(endpoint, headers=headers, json={"action": "read", "path": "journal/entries.json"}).get_json()
    assert read["encoding"] == "text"
    assert json.loads(read["content"]) == [{"mood": "calm"}]

    payload = base64.b64encode(b"persistent-binary-data").decode("ascii")
    uploaded = registered.post(endpoint, headers=headers, json={"action": "upload", "path": "uploads/sample.bin", "content": payload})
    assert uploaded.status_code == 200
    binary = registered.post(endpoint, headers=headers, json={"action": "read", "path": "uploads/sample.bin", "encoding": "base64"}).get_json()
    assert base64.b64decode(binary["content"]) == b"persistent-binary-data"

    listing = registered.post(endpoint, headers=headers, json={"action": "list", "path": "uploads"}).get_json()
    assert listing["items"] == [{"mime": "application/octet-stream", "name": "sample.bin", "path": "uploads/sample.bin", "size": 22, "type": "file"}]

    moved = registered.post(endpoint, headers=headers, json={"action": "move", "source": "uploads/sample.bin", "destination": "archive/sample.bin"})
    assert moved.status_code == 200
    deleted = registered.post(endpoint, headers=headers, json={"action": "delete", "path": "archive"})
    assert deleted.status_code == 200
    assert registered.post(endpoint, headers=headers, json={"action": "read", "path": "archive/sample.bin"}).status_code == 404

    assert registered.post(endpoint, headers=headers, json={"action": "write", "path": "../escape.txt", "content": "no"}).status_code == 400
    assert registered.post(endpoint, headers=headers, json={"action": "write", "path": "unsafe.html", "content": "no"}).status_code == 400
    with app.app_context():
        assert safe_path("listener", "data/journal/entries.json").read_text() == '[{"mood":"calm"}]'


def test_agent_is_taught_the_persistent_frontend_file_skill():
    from resona.agent_runtime import build_agent_messages

    system = build_agent_messages("Base", "Build a journal", [], "", '{"nav_items":[]}', 20)[0]["content"]
    assert "Built-in persistent front-end file skill" in system
    assert "await ResonaFiles.write" in system
    assert "await ResonaFiles.upload" in system
    assert "This API cannot modify pages, navigation, memory, snapshots, or server files" in system


def test_agent_is_taught_full_ambient_synth_controls_and_mobile_requirements():
    from resona.agent_runtime import build_agent_messages

    system = build_agent_messages("Base", "Design an ambient page", [], "", '{"nav_items":[]}', 20)[0]["content"]
    assert "setAtmosphere" in system and "restore, melancholy, or deep" in system
    assert "setAmbientParameter" in system and "warmth, movement, space, texture, shimmer, or output" in system
    assert "setTonalSource" in system and "manual or generated" in system
    assert "setTonalCentre" in system and "48, 50, 51, 53, 55, or 57" in system
    assert "six-layer native Web Audio synth" in system
    assert "Generated mode disables that internal chord cycle" in system
    assert "every pitched synth layer plus the binaural carrier" in system
    assert "comfortable and readable on phones down to 320px" in system


def test_binaural_engine_never_starts_or_controls_noise(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    binaural_start = audio.split("    start() {", 1)[1].split("    stop() {", 1)[0]
    noise_selection = audio.split("    setNoise(name)", 1)[1].split("    startNoise()", 1)[0]
    assert "createNoise" not in binaural_start
    assert "noiseGain" not in binaural_start
    assert "this.stop()" not in noise_selection
    assert "this.start()" not in noise_selection
    assert "this.stopNoise()" in noise_selection
    assert "this.startNoise()" in noise_selection


def test_full_ambient_synth_builds_six_layers_and_complete_effects_chain(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    graph = audio.split("    buildAmbientGraph()", 1)[1].split("    buildAmbientLayers()", 1)[0]
    layers = audio.split("    buildAmbientLayers()", 1)[1].split("    clearManualHarmonyTimer()", 1)[0]
    assert "createWaveShaper" in graph and "makeSaturationCurve(7)" in graph
    assert "chorusDelayL" in graph and "chorusDelayR" in graph
    assert "createDynamicsCompressor" in graph
    assert "makeImpulse(9.5, 2.7)" in graph
    assert "createConvolver" in graph and "createDelay(2)" in graph
    assert "ambientAnalyser" in graph and "fftSize = 512" in graph
    assert "const drone" in layers
    assert "const body" in layers and "analogDrift" in layers
    assert "const weather" in layers and "makeAmbientNoise(12)" in layers
    assert "const shimmer" in layers
    assert "anchorGain" in layers
    assert "setInterval(() => this.applyRandomDrift(),4300)" in audio
    assert "setInterval(() => this.playFeltAnchor(),14000 + Math.random() * 7000)" in audio


def test_binaural_pair_is_symmetric_around_active_chord_carrier(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    pair = audio.split("    binauralPair(", 1)[1].split("    updateBinauralFrequencies(", 1)[0]
    update = audio.split("    updateBinauralFrequencies(", 1)[1].split("    setBeat(", 1)[0]
    assert "left:center - beat / 2" in pair
    assert "right:center + beat / 2" in pair
    assert "retune(this.left.frequency, pair.left, .3)" in update
    assert "retune(this.right.frequency, pair.right, .3)" in update
    assert "if (chordTransition === null) parameter.setTargetAtTime(frequency, now, glide)" in update
    assert "else if (chordTransition <= 0) parameter.setValueAtTime(frequency, now)" in update
    assert "parameter.linearRampToValueAtTime(frequency, now + chordTransition)" in update


def test_chord_progression_harmonizes_all_pitched_ambient_layers_in_browser(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    chord_logic = audio.split("    harmonizeProgression(", 1)[1].split("    startAmbient()", 1)[0]
    harmony_application = audio.split("    applyHarmony(intervals, transition, generated)", 1)[1].split("    applyProgressionChord()", 1)[0]
    manual_frequency = audio.split("    setDroneFrequency(", 1)[1].split("    parseChord(", 1)[0]
    assert "[0,2,4,5,7,9,11]" in chord_logic
    assert "[0,2,3,5,7,8,10]" in chord_logic
    assert "this.progressionTonic = tonic.root" in chord_logic
    assert "chordIntervals(chord)" in chord_logic
    assert "this.ambientVoices.body.forEach" in chord_logic
    assert "this.ambientVoices.shimmer.forEach" in chord_logic
    assert "this.ambientVoices.drone.forEach" in chord_logic
    assert "const droneRoot = generated ? intervals.root : 0" in chord_logic
    assert "weather" not in harmony_application
    assert "ambientGains.textures" not in harmony_application
    assert "setInterval" in chord_logic
    assert "setChordProgression(chords, duration)" in chord_logic
    assert "this.harmonizeProgression(chords)" in chord_logic
    assert "this.config.ambient.tonalSource = 'generated'" in chord_logic
    assert "this.config.carrier = midiToFrequency(this.config.ambient.manualRootMidi + intervals.root)" in chord_logic
    assert "this.updateBinauralFrequencies(Math.min(this.config.ambient.binauralChordTransition, this.config.ambient.chordDuration))" in chord_logic
    assert "this.config.ambient.manualRootMidi = frequencyToMidi(root)" in manual_frequency
    assert "this.config.ambient.tonalSource !== 'manual'" in manual_frequency
    assert "this.updateBinauralFrequencies();" in manual_frequency
    assert "binauralChordTransition" not in manual_frequency


def test_progression_transition_controls_do_not_change_manual_drone_glide(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    player = registered.get("/static/js/player.js").get_data(as_text=True)
    page = registered.get("/storage/listener/pages/advanced.html").get_data(as_text=True)
    chord_application = audio.split("    applyProgressionChord()", 1)[1].split("    scheduleProgression()", 1)[0]
    manual_frequency = audio.split("    setDroneFrequency(", 1)[1].split("    parseChord(", 1)[0]
    assert "chordTransition:0" in audio
    assert "binauralChordTransition:0" in audio
    assert "setChordTransition(value)" in audio
    assert "setBinauralChordTransition(value)" in audio
    assert "this.applyHarmony(intervals, chordTransition, true)" in chord_application
    assert "parameter.linearRampToValueAtTime(frequency, now + transition)" in audio
    assert "binauralChordTransition" in chord_application
    assert "this.morphManualHarmony()" in manual_frequency
    assert "linearRampToValueAtTime" not in manual_frequency
    assert "data.action === 'setChordTransition'" in player
    assert "data.action === 'setBinauralChordTransition'" in player
    assert "action:'setChordTransition'" in page
    assert "action:'setBinauralChordTransition'" in page


def test_continuous_chord_mode_prefetches_and_swaps_sets_at_boundaries(registered):
    audio = registered.get("/static/js/audio.js").get_data(as_text=True)
    player = registered.get("/static/js/player.js").get_data(as_text=True)
    page = registered.get("/storage/listener/pages/home.html").get_data(as_text=True)
    assert "resona:chord-set-ended" in audio
    assert "this.chordProgressionIndex + 1 >= activeChords.length" in audio
    assert "this.config.ambient.chordProgression !== activeChords" in audio
    assert "const chordPipeline = { enabled:false" in player
    assert "chordPipeline.ready = [makeChordSet(secondChords)]" in player
    assert "generateAhead(chordPipeline.ready[0].chords)" in player
    assert "chordPipeline.playing = chordPipeline.ready.shift()" in player
    assert "generateAhead((chordPipeline.ready[chordPipeline.ready.length - 1] || chordPipeline.playing).chords)" in player
    assert "data-chord-continuous" in page
    assert "action:'setContinuousChordMode'" in page
    assert "continuous:Boolean" in page
    assert "resona:chord-pipeline" in page


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
    assert response.status_code == 503
    assert response.get_json()["rejected"] is True
    assert "safety review is unavailable" in response.get_json()["error"]
    with app.app_context():
        run = get_db().execute("SELECT status FROM agent_runs ORDER BY id DESC").fetchone()
        assert run["status"] == "failed"


def test_agent_request_ids_expose_owned_status_and_prevent_duplicate_runs(app, registered):
    request_id = "stable_request_id_123456"
    with app.app_context():
        user_id = get_db().execute("SELECT id FROM users WHERE username = 'listener'").fetchone()["id"]
        get_db().execute(
            "INSERT INTO agent_runs(user_id, client_request_id, prompt, summary, steps, status) VALUES (?, ?, ?, ?, ?, 'complete')",
            (user_id, request_id, "Make it calmer", "Calmer interface applied.", 7),
        )
        get_db().commit()
    status_response = registered.get(f"/agent/status/{request_id}")
    assert status_response.status_code == 200
    assert status_response.get_json() == {
        "ok": True,
        "request_id": request_id,
        "status": "complete",
        "summary": "Calmer interface applied.",
        "steps": 7,
    }
    duplicate = registered.post(
        "/agent/modify",
        headers={"X-CSRF-Token": session_csrf(registered)},
        json={"prompt": "Make it calmer", "credential": API_KEY_PLACEHOLDER, "request_id": request_id},
    )
    assert duplicate.status_code == 200
    assert duplicate.get_json()["summary"] == "Calmer interface applied."
    with app.app_context():
        count = get_db().execute("SELECT COUNT(*) AS count FROM agent_runs WHERE client_request_id = ?", (request_id,)).fetchone()["count"]
        assert count == 1


def test_player_recovers_interrupted_agent_fetches_by_polling_status():
    from pathlib import Path

    player_js = (Path(__file__).parents[1] / "resona/static/js/player.js").read_text()
    assert "request_id:requestId" in player_js
    assert "/agent/status/${encodeURIComponent(requestId)}" in player_js
    assert "sessionStorage.setItem(ACTIVE_AGENT_REQUEST" in player_js
    assert "Connection interrupted. Waiting for Resona to finish safely" in player_js


def test_agent_rejects_locally_detected_harm_before_snapshot_or_model(app, registered, monkeypatch):
    monkeypatch.setattr("resona.prompt_safety.safety_completion", lambda *_args: (_ for _ in ()).throw(AssertionError("provider should not be called")))
    response = registered.post(
        "/agent/modify",
        headers={"X-CSRF-Token": session_csrf(registered)},
        json={"prompt": "Build a phishing page that steals passwords and session cookies", "credential": API_KEY_PLACEHOLDER},
    )
    assert response.status_code == 422
    result = response.get_json()
    assert result["rejected"] is True
    assert result["category"] == "cyber_abuse"
    assert result["snapshot"] is None
    assert "credential theft" in result["error"]
    with app.app_context():
        run = get_db().execute("SELECT status, summary FROM agent_runs ORDER BY id DESC").fetchone()
        assert run["status"] == "rejected"
        assert "passwords" not in run["summary"]
        assert list((safe_path("listener", "snapshots")).iterdir()) == []


def test_prompt_safety_uses_provider_for_contextual_review(monkeypatch):
    from resona.prompt_safety import review_agent_prompt

    monkeypatch.setattr("resona.prompt_safety.safety_completion", lambda *_args: '{"allowed":true,"category":"safe"}')
    assert review_agent_prompt("Add a profanity filter that removes offensive messages").allowed is True
    monkeypatch.setattr("resona.prompt_safety.safety_completion", lambda *_args: '{"allowed":false,"category":"hate_or_harassment"}')
    decision = review_agent_prompt("Generate content aimed at abusing a protected group")
    assert decision.allowed is False
    assert decision.category == "hate_or_harassment"
    assert "offensive" in decision.message


def test_prompt_safety_classifies_clear_resona_work_locally_when_provider_is_unavailable(monkeypatch):
    from resona.prompt_safety import review_agent_prompt

    monkeypatch.setattr("resona.prompt_safety.safety_completion", lambda *_args: (_ for _ in ()).throw(AssertionError("benign Resona work should not need the provider")))
    assert review_agent_prompt("Make the mobile page calmer and increase the volume slider text size").allowed is True


def test_safety_completion_retries_without_json_mode_for_compatible_proxies(app, monkeypatch):
    from resona.closeai import safety_completion

    payloads = []

    class Response:
        def __init__(self, status_code, content=None):
            self.status_code = status_code
            self.content = content
            self.headers = {}

        def json(self):
            return {"choices": [{"message": {"content": self.content}}]}

    def fake_post(_url, **kwargs):
        payloads.append(kwargs["json"])
        return Response(400) if len(payloads) == 1 else Response(200, '```json\n{"allowed":true,"category":"safe"}\n```')

    monkeypatch.setattr("resona.closeai.requests.post", fake_post)
    with app.app_context():
        db = get_db()
        db.execute("UPDATE settings SET value = 'sk-server-only' WHERE key = 'closeai_api_key'")
        db.commit()
        raw = safety_completion("Explain a potentially ambiguous request", API_KEY_PLACEHOLDER)
    assert '"allowed":true' in raw
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]


def test_prompt_safety_fails_closed_on_invalid_provider_decision(monkeypatch):
    from resona.prompt_safety import review_agent_prompt

    monkeypatch.setattr("resona.prompt_safety.safety_completion", lambda *_args: "not-json")
    try:
        review_agent_prompt("Process this unusual request")
        assert False, "invalid safety responses must fail closed"
    except RuntimeError as exc:
        assert "not executed" in str(exc)


def test_agent_debug_trace_is_opt_in_and_redacts_secrets(app, capsys):
    from resona.agent_runtime import trace_agent_debug

    with app.app_context():
        trace_agent_debug("quiet_event", user_prompt="not printed")
        assert capsys.readouterr().out == ""
        app.config["AGENT_TRACE"] = True
        trace_agent_debug(
            "model_response",
            user_prompt="make the interface calmer",
            api_key="sk-this-must-never-print",
            nested={"password": "private-password", "content": "Bearer secret-authorization"},
        )
    output = capsys.readouterr().out
    assert "[Resona Agent Debug] model_response" in output
    assert "make the interface calmer" in output
    assert "sk-this-must-never-print" not in output
    assert "private-password" not in output
    assert "secret-authorization" not in output
    assert output.count("[REDACTED]") >= 3


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
        status_code = 200
        headers = {}

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


def test_managed_deployment_honors_explicit_admin_provider_key_over_environment(app):
    from resona.closeai import get_provider_settings

    with app.app_context():
        db = get_db()
        db.execute("UPDATE settings SET value = 'new-admin-key' WHERE key = 'closeai_api_key'")
        db.execute("UPDATE settings SET value = 'admin-selected-model' WHERE key = 'closeai_model'")
        db.commit()
        app.config.update(
            CLOSEAI_API_KEY="current-environment-key",
            CLOSEAI_MODEL="gpt-5.6-sol",
            CLOSEAI_PREFER_ENV=True,
        )
        provider = get_provider_settings()
    assert provider["api_key"] == "new-admin-key"
    assert provider["model"] == "admin-selected-model"
    assert provider["key_source"] == "admin"


def test_managed_deployment_falls_back_to_environment_after_admin_key_is_cleared(app):
    from resona.closeai import get_provider_settings

    with app.app_context():
        db = get_db()
        db.execute("UPDATE settings SET value = '' WHERE key = 'closeai_api_key'")
        db.execute("UPDATE settings SET value = 'old-db-model' WHERE key = 'closeai_model'")
        db.commit()
        app.config.update(
            CLOSEAI_API_KEY="current-environment-key",
            CLOSEAI_MODEL="gpt-5.6-sol",
            CLOSEAI_PREFER_ENV=True,
        )
        provider = get_provider_settings()
    assert provider["api_key"] == "current-environment-key"
    assert provider["model"] == "gpt-5.6-sol"
    assert provider["key_source"] == "environment"


def test_provider_403_is_reported_without_leaking_response_content():
    from resona.closeai import ProviderHTTPError, raise_for_provider_status

    class Response:
        status_code = 403
        headers = {"x-request-id": "request-safe-id"}

        def json(self):
            return {"error": {"code": "model_forbidden", "message": "secret upstream detail"}}

    try:
        raise_for_provider_status(Response())
        assert False, "403 must raise a provider error"
    except ProviderHTTPError as exc:
        message = str(exc)
        assert "API key or model" in message
        assert "model_forbidden" in message
        assert "request-safe-id" in message
        assert "secret upstream detail" not in message


def test_generated_content_cannot_escape_shell(app):
    from resona.user_storage import validate_content
    with app.app_context():
        interactive = '<!doctype html><html><head><style>body{color:white}</style></head><body><main><button id="start">Start</button></main><script>document.querySelector("#start").addEventListener("click", () => document.body.classList.toggle("active"));</script></body></html>'
        validate_content("pages/interactive.html", interactive)
        for name, content in (
            ("pages/bad.html", "content"),
            ("pages/bad.html", "Updated with a light blue scheme"),
            ("pages/bad.html", '<!doctype html><html><head><style>body{color:white}</style></head><body><main>Unsafe</main><script src="https://example.com/app.js"></script></body></html>'),
            ("pages/bad.html", '<!doctype html><html><head><style>body{color:white}</style></head><body><main>Broken</main><script>document.body.dataset.ready="1";</script></script></body></html>'),
            ("static/bad.js", 'window.parent.document.body.remove()'),
            ("static/bad.js", 'document.querySelector("#app")'),
        ):
            try:
                validate_content(name, content)
                assert False, content
            except ValueError:
                pass


def test_admin_login_is_separate_and_protected(app, client):
    app.config.update(CLOSEAI_API_KEY="environment-key", CLOSEAI_MODEL="gpt-environment", CLOSEAI_PREFER_ENV=True)
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
    with app.app_context():
        from resona.closeai import get_provider_settings
        provider = get_provider_settings()
        assert provider["api_key"] == "sk-never-render-this"
        assert provider["model"] == "gpt-admin-test"
        assert provider["key_source"] == "admin"


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


def test_admin_can_create_regular_user_with_private_workspace(app, client):
    from resona.user_storage import safe_path, user_root
    from werkzeug.security import check_password_hash, generate_password_hash

    with app.app_context():
        db = get_db()
        admin_id = db.execute(
            "INSERT INTO users(username,email,password_hash,is_admin) VALUES (?,?,?,1)",
            ("provisioner", "provisioner@example.com", generate_password_hash("long-admin-password", method="pbkdf2:sha256:600000")),
        ).lastrowid
        db.commit()
    with client.session_transaction() as session:
        session["user_id"] = admin_id
        session["csrf_token"] = "create-user-csrf"

    response = client.post("/admin/users", data={
        "csrf_token": "create-user-csrf",
        "username": "new_listener",
        "email": "new-listener@example.com",
        "password": "temporary-password",
    })
    assert response.status_code == 302
    with app.app_context():
        user = get_db().execute("SELECT * FROM users WHERE username = 'new_listener'").fetchone()
        assert user and user["is_admin"] == 0
        assert check_password_hash(user["password_hash"], "temporary-password")
        assert user_root("new_listener").is_dir()
        assert safe_path("new_listener", "nav.json").is_file()
        assert safe_path("new_listener", "static/chord-model/weights.bin").is_file()

    dashboard = client.get("/admin/")
    assert b"new_listener" in dashboard.data
    assert b"Create a user" in dashboard.data

    duplicate = client.post("/admin/users", data={
        "csrf_token": "create-user-csrf",
        "username": "another_listener",
        "email": "new-listener@example.com",
        "password": "temporary-password",
    }, follow_redirects=True)
    assert b"already registered" in duplicate.data
    with app.app_context():
        assert not user_root("another_listener").exists()


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
    from resona.prompt_safety import SafetyDecision

    calls = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "one", "type": "function", "function": {"name": "read_memory", "arguments": '{"name":"all"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "two", "type": "function", "function": {"name": "list_files", "arguments": '{"path":"pages"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "three", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"pages/advanced.html"}'}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "four", "type": "function", "function": {"name": "replace_in_file", "arguments": json.dumps({"path": "pages/advanced.html", "old_text": "Shape your soundscape", "new_text": "Shape your restorative soundscape"})}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "five", "type": "function", "function": {"name": "write_memory", "arguments": json.dumps({"name": "changelog", "content": "Renamed the home heading and preserved playback controls."})}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "six", "type": "function", "function": {"name": "validate_workspace", "arguments": "{}"}}]},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "seven", "type": "function", "function": {"name": "finish", "arguments": '{"summary":"Updated and validated the healing page."}'}}]},
    ]

    observed_progress = []

    def fake_completion(messages, tools, credential):
        assert credential == API_KEY_PLACEHOLDER
        assert any(tool["function"]["name"] == "move_path" for tool in tools)
        assert any(tool["function"]["name"] == "invoke_skill" for tool in tools)
        assert any(tool["function"]["name"] == "restore_snapshot" for tool in tools)
        assert any(tool["function"]["name"] == "read_memory" for tool in tools)
        assert any(tool["function"]["name"] == "write_memory" for tool in tools)
        progress = [message["content"] for message in messages if message["role"] == "system" and message["content"].startswith("Run progress:")]
        observed_progress.append(progress[-1])
        return calls.pop(0)

    monkeypatch.setattr("resona.agent.review_agent_prompt", lambda *_args: SafetyDecision(True))
    monkeypatch.setattr("resona.agent_runtime.agent_completion", fake_completion)
    response = registered.post(
        "/agent/modify",
        headers={"X-CSRF-Token": session_csrf(registered)},
        json={"prompt": "Rename my healing page and make sure it works", "credential": API_KEY_PLACEHOLDER},
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result["steps"] == 7
    assert result["tools"] == ["read_memory", "list_files", "read_file", "replace_in_file", "write_memory", "validate_workspace", "finish"]
    assert observed_progress[0].startswith("Run progress: this is step 1 of 80; 79 steps remain")
    assert observed_progress[-1].startswith("Run progress: this is step 7 of 80; 73 steps remain")
    with app.app_context():
        assert "Shape your restorative soundscape" in safe_path("listener", "pages/advanced.html").read_text()
        assert safe_path("listener", f"snapshots/{result['snapshot']}/pages/advanced.html").exists()
        assert "Renamed the home heading" in safe_path("listener", "memory/changelog.md").read_text()


def test_memory_tools_restore_context_and_require_a_progress_note_before_finish(app, registered):
    from resona.agent_runtime import WorkspaceTools

    with app.app_context():
        user_id = get_db().execute("SELECT id FROM users WHERE username = 'listener'").fetchone()["id"]
        tools = WorkspaceTools("listener", user_id)
        memories = json.loads(tools.execute("read_memory", {"name": "all"}))["memories"]
        assert set(memories) == {"notes", "plan", "changelog"}
        tools.execute("replace_in_file", {"path": "pages/advanced.html", "old_text": "Shape your soundscape", "new_text": "A remembered soundscape"})
        try:
            tools.execute("finish", {"summary": "Done"})
            assert False, "finish should require memory after workspace changes"
        except ValueError as exc:
            assert "write_memory" in str(exc)
        tools.execute("write_memory", {"name": "plan", "content": "Home heading updated; validation remains.", "mode": "append"})
        assert json.loads(tools.execute("finish", {"summary": "Done"}))["ok"] is True
        assert "validation remains" in safe_path("listener", "memory/plan.md").read_text()


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
        original = safe_path("listener", "pages/advanced.html").read_text()
        tools.execute("replace_in_file", {"path": "pages/advanced.html", "old_text": "Shape your soundscape", "new_text": "Changed after snapshot"})
        listed = json.loads(tools.execute("list_snapshots", {}))["snapshots"]
        assert snapshot_id in listed
        tools.execute("restore_snapshot", {"snapshot_id": snapshot_id})
        assert safe_path("listener", "pages/advanced.html").read_text() == original
