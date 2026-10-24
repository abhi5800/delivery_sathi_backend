"""SQLite storage for seller onboarding."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.getenv("SELLER_BOT_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sathi.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sellers (
    phone TEXT PRIMARY KEY,
    profile_name TEXT,
    seller_name TEXT,
    business_name TEXT,
    language TEXT,
    step TEXT NOT NULL DEFAULT 'hello',
    state_json TEXT NOT NULL DEFAULT '{}',
    needs_human INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    onboarded_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    body TEXT NOT NULL,
    wa_message_id TEXT UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_phone ON messages (phone, id);
CREATE TABLE IF NOT EXISTS forms (
    token TEXT PRIMARY KEY,
    phone TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    submitted_at TEXT
);
-- Hackathon only: credentials are stored unencrypted. Encrypt before real sellers use this.
CREATE TABLE IF NOT EXISTS seller_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    new = not os.path.exists(DB_PATH)
    with connect() as conn:
        conn.executescript(SCHEMA)
    if new:
        os.chmod(DB_PATH, 0o600)


def get_or_create_seller(phone, profile_name=None):
    with connect() as conn:
        row = conn.execute("SELECT * FROM sellers WHERE phone = ?", (phone,)).fetchone()
        if row:
            if profile_name and not row["profile_name"]:
                conn.execute("UPDATE sellers SET profile_name = ? WHERE phone = ?", (profile_name, phone))
            return dict(row)
        ts = now()
        conn.execute(
            "INSERT INTO sellers (phone, profile_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (phone, profile_name, ts, ts),
        )
        return dict(conn.execute("SELECT * FROM sellers WHERE phone = ?", (phone,)).fetchone())


def save_seller(phone, state, step, language, needs_human=False):
    ts = now()
    with connect() as conn:
        conn.execute(
            """UPDATE sellers SET state_json = ?, step = ?, language = ?, seller_name = ?, business_name = ?,
                   needs_human = ?, updated_at = ?,
                   onboarded_at = CASE WHEN ? = 'live' AND onboarded_at IS NULL THEN ? ELSE onboarded_at END
               WHERE phone = ?""",
            (json.dumps(state, ensure_ascii=False), step, language, state.get("seller_name"),
             state.get("business_name"), int(needs_human), ts, step, ts, phone),
        )


def reset_seller(phone):
    with connect() as conn:
        conn.execute("DELETE FROM sellers WHERE phone = ?", (phone,))
        conn.execute("DELETE FROM messages WHERE phone = ?", (phone,))
        conn.execute("DELETE FROM forms WHERE phone = ?", (phone,))


def log_message(phone, direction, body, wa_message_id=None):
    """Store a message. Returns False if this WhatsApp message id was already seen (Meta retries)."""
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO messages (phone, direction, body, wa_message_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (phone, direction, body, wa_message_id, now()),
            )
        except sqlite3.IntegrityError:
            return False
    return True


def recent_messages(phone, limit=8):
    with connect() as conn:
        rows = conn.execute(
            "SELECT direction, body FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?", (phone, limit)
        ).fetchall()
    return [{"from": "seller" if r["direction"] == "in" else "sathi", "text": r["body"]} for r in reversed(rows)]


def create_form(token, phone, kind):
    with connect() as conn:
        conn.execute("INSERT INTO forms (token, phone, kind, created_at) VALUES (?, ?, ?, ?)", (token, phone, kind, now()))


def get_form(token):
    with connect() as conn:
        row = conn.execute("SELECT * FROM forms WHERE token = ?", (token,)).fetchone()
    return dict(row) if row else None


def submit_form(token, phone, kind, data):
    with connect() as conn:
        conn.execute("UPDATE forms SET submitted_at = ? WHERE token = ?", (now(), token))
        conn.execute(
            "INSERT INTO seller_credentials (phone, kind, data_json, created_at) VALUES (?, ?, ?, ?)",
            (phone, kind, json.dumps(data), now()),
        )


def list_sellers():
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM sellers ORDER BY updated_at DESC").fetchall()]
