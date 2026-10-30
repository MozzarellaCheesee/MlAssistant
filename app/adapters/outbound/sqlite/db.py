import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL DEFAULT 'default',
    kind        TEXT NOT NULL CHECK (kind IN ('task','meeting','reminder')),
    title       TEXT NOT NULL,
    starts_at   TEXT,
    ends_at     TEXT,
    due_date    TEXT,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','cancelled')),
    created_at  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user','import')),
    source_text TEXT,
    notified_at TEXT,
    rrule       TEXT,
    exdates     TEXT,
    external_id TEXT
);
CREATE INDEX IF NOT EXISTS items_user_due ON items(user_id, due_date);
CREATE TABLE IF NOT EXISTS items_vec (item_id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS dialog_state (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, yandex_uid TEXT UNIQUE NOT NULL, login TEXT NOT NULL,
                                  display_name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth_sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    role       TEXT CHECK (role IN ('user','assistant')),
    text       TEXT NOT NULL,
    source     TEXT CHECK (source IN ('text','voice')),
    items_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_user ON messages(user_id, id);
CREATE TABLE IF NOT EXISTS oauth_states (state TEXT PRIMARY KEY, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calendar_links (user_id TEXT PRIMARY KEY, email TEXT NOT NULL, token_enc TEXT NOT NULL,
                                           refresh_enc TEXT, expires_at TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: str):
        # один connection + глобальный lock, пул при росте нагрузки
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(SCHEMA)
