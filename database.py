"""الاتصال بقاعدة بيانات SQLite وإنشاء الجداول والبيانات الأولية."""
import logging
import os
import sqlite3
import stat
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from flask import g

from config import Config

logger = logging.getLogger("voxshield")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'analyst', 'trainee')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    username TEXT NOT NULL,
    success INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_lookup ON login_attempts(ip, username, created_at);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    sample_rate INTEGER NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('genuine', 'fake')),
    attack_type TEXT,
    confidence REAL NOT NULL,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    probabilities_json TEXT NOT NULL,
    factors_json TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    spectrogram_json TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    stage_timings_json TEXT NOT NULL,
    processing_ms INTEGER NOT NULL,
    model_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    level TEXT NOT NULL CHECK (level IN ('medium', 'high')),
    status TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed')),
    created_at TEXT NOT NULL,
    reviewed_by INTEGER REFERENCES users(id),
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
CREATE TABLE IF NOT EXISTS simulation_progress (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scenario_key TEXT NOT NULL,
    last_step INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, scenario_key)
);
CREATE TABLE IF NOT EXISTS model_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trained_at TEXT NOT NULL,
    data_source TEXT NOT NULL,
    samples_count INTEGER NOT NULL,
    accuracy REAL NOT NULL,
    confusion_json TEXT NOT NULL,
    notes TEXT
);
"""


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso(value=None):
    return (value or utc_now()).replace(microsecond=0).isoformat()


def connect(readonly=False):
    """اتصال مستقل: للقراءة فقط بوضع mode=ro أو للكتابة عند تغيير البيانات."""
    path = Config.DATABASE_PATH
    if readonly:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
    else:
        conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def read_db():
    if "db_ro" not in g:
        g.db_ro = connect(readonly=True)
    return g.db_ro


def write_db():
    if "db_rw" not in g:
        g.db_rw = connect(readonly=False)
    return g.db_rw


def close_db(_exc=None):
    for key in ("db_ro", "db_rw"):
        conn = g.pop(key, None)
        if conn is not None:
            conn.close()


def query_all(sql, params=()):
    return [dict(row) for row in read_db().execute(sql, params).fetchall()]


def query_one(sql, params=()):
    row = read_db().execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql, params=()):
    conn = write_db()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid


def hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _restrict_file_permissions(path):
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logger.warning("تعذر تقييد صلاحيات ملف قاعدة البيانات")


def init_db():
    """إنشاء الجداول وإضافة الحسابات الافتراضية من .env إذا كان جدول المستخدمين فارغاً."""
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    conn = connect(readonly=False)
    try:
        conn.executescript(SCHEMA)
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            now = utc_iso()
            for username, password, role, display_name in Config.DUMMY_ACCOUNTS:
                if not username or not password:
                    logger.warning("تم تجاوز حساب افتراضي ناقص البيانات في .env للدور %s", role)
                    continue
                conn.execute(
                    "INSERT INTO users (username, display_name, password_hash, role, is_active, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (username.strip().lower(), display_name, hash_password(password), role, now),
                )
        conn.commit()
    finally:
        conn.close()
    _restrict_file_permissions(Config.DATABASE_PATH)