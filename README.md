# Resona

Resona is an adaptive healing-music web application. It combines a procedural Web Audio engine with a persistent Vibe Agent that can reshape each user's pages, navigation, theme, and custom synthesis configuration while the protected player shell and microphone remain fixed.

## Included

- Flask application factory with `auth_bp`, `admin_bp`, `player_bp`, `agent_bp`, and `storage_bp`
- Registration, login, password reset flow, profiles, session history, and a separate admin login
- One focused default Home page with binaural-band selection and play/stop controls, driven by each user's private `nav.json`
- Continuous binaural, colored-noise, and polyphonic Web Audio synthesis
- A private browser-side LSTM chord generator that retunes the ambient pads without a generation API
- Voice or text Vibe Agent interface with validated HTML, CSS, JS, JSON, SVG, and Markdown writes
- Multi-turn autonomous agent runtime with list, read, write, replace, move, delete, navigation, validation, and registered-skill tools
- Per-user 1 GB quotas, authenticated storage routes, path isolation, snapshots, and rollback
- Server-side CloseAI proxying with a client credential placeholder and OpenAI-compatible chat integration
- Resend-powered registration welcome emails and secure password reset links
- Admin controls for shared AI and email provider configuration, users, prompts, skills, and additional admins

## Local setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
FLASK_APP=run.py .venv/bin/flask create-admin
FLASK_APP=run.py .venv/bin/flask run
```

For local debugging with detailed AI agent traces, run:

```sh
python run.py debug
```

This enables Flask debug mode and prints each user's AI prompt, step progress, model response, tool call and arguments, tool result, validation event, and final outcome. Large strings are truncated and credentials, API keys, passwords, tokens, authorization headers, and secrets are redacted. A normal `python run.py` launch keeps agent tracing disabled.

Set `SECRET_KEY` before deployment. To enable Vibe Agent calls, either set `CLOSEAI_API_KEY` on the server or save it from the admin control center. Browsers send only the literal `{{RESONA_SERVER_API_KEY}}` placeholder; Resona resolves the active server key, base URL, and model and makes the provider request itself. No provider key is generated for or exposed to an individual user. User workspaces default to `instance/storage`; use `RESONA_STORAGE_ROOT` to mount a dedicated server volume.

To enable email, save a Resend API key, sender name, and sender email in the admin control center, or set `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, and `RESEND_FROM_NAME` on the server. Registration sends a welcome email without blocking account access if delivery fails. Password recovery sends a single-use link that expires after 30 minutes and keeps the response identical for known and unknown addresses. Set `PUBLIC_BASE_URL` to the public HTTPS origin in production so emailed links always use the canonical application address. The sender domain must be verified in Resend.

When `ADMIN_PASSWORD` is non-empty, startup creates or synchronizes the administrator named by `ADMIN_USERNAME` (default `admin`). `ADMIN_EMAIL` is optional and defaults to `<username>@resona.local`. Restart the application after changing these values.

The Vibe Agent continues calling the model and workspace tools until it explicitly finishes with a valid workspace. `AGENT_MAX_STEPS` defaults to 80 and can be adjusted from 1–200 in the admin provider panel. On every turn the agent receives its current step, total limit, and remaining budget, with instructions to avoid redundant work without rushing or skipping validation. Every run creates one rollback snapshot before the first tool call. Registered HTTPS skills are exposed through `invoke_skill`; the server credential is attached only for approved CloseAI hosts, and direct image or audio results can be saved into the user's quota-controlled storage.

The trained chord checkpoint is exported with `scripts/export_chord_model.py` into a browser-readable float32 tensor package. Each user receives the roughly 3 MB package in `static/chord-model/`, counted against their private storage quota. The player downloads those authenticated static files and performs the LSTM forward pass, top-k sampling, and chord decoding entirely in JavaScript; there is no chord-generation backend endpoint. The model is the supplied triad LSTM (not a Transformer) with 3,861 chord labels and a 50-chord maximum context.

## Tests

```sh
.venv/bin/python -m pytest -q
```

The development server is not intended for production. Deploy behind a production WSGI server and TLS, set `SESSION_COOKIE_SECURE=1`, and back up the database and storage volume together. Environment-managed provider credentials are preferred for production; credentials saved through the control center are stored in the Resona database and are never rendered back to administrators or clients.
