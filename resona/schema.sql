CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT,
    email_verified_at TEXT,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0,
    demo_enabled INTEGER NOT NULL DEFAULT 1,
    session_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL CHECK(purpose IN ('registration', 'email_change')),
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS captcha_redemptions (
    token_hash TEXT PRIMARY KEY,
    used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS password_resets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT
);

CREATE TABLE IF NOT EXISTS playback_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    config_json TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    endpoint TEXT,
    scope TEXT NOT NULL DEFAULT 'global',
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_request_id TEXT,
    prompt TEXT NOT NULL,
    summary TEXT,
    steps INTEGER,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO settings(key, value) VALUES (
    'agent_system_prompt',
    ''
);
INSERT OR IGNORE INTO settings(key, value) VALUES ('closeai_api_key', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('closeai_base_url', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('closeai_model', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('agent_max_steps', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('resend_api_key', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('resend_from_email', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('resend_from_name', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('agent_prompt_version', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('agent_model_version', '');
INSERT OR IGNORE INTO settings(key, value) VALUES ('user_registration_enabled', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('user_login_enabled', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('password_recovery_enabled', '1');
INSERT OR IGNORE INTO settings(key, value) VALUES ('profile_editing_enabled', '1');
