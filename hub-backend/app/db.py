from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import Settings
from app.ulid import generate_prefixed_id


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS seq_generator (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO seq_generator (id, current_seq) VALUES (1, 0);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    seq INTEGER UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    content_type TEXT NOT NULL DEFAULT 'mixed',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    nsfw_level INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'web',
    submitter_hash TEXT NOT NULL DEFAULT '',
    likes INTEGER NOT NULL DEFAULT 0,
    dislikes INTEGER NOT NULL DEFAULT 0,
    reports INTEGER NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0.0,
    reviewer_id TEXT,
    reject_reason TEXT,
    created_at TEXT NOT NULL,
    published_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_status_seq ON items(status, seq);
CREATE INDEX IF NOT EXISTS idx_items_status_created ON items(status, created_at);
CREATE INDEX IF NOT EXISTS idx_items_content_hash ON items(content_hash);
CREATE INDEX IF NOT EXISTS idx_items_submitter_hash ON items(submitter_hash);
CREATE INDEX IF NOT EXISTS idx_items_nsfw_score ON items(nsfw_level, score);

CREATE TABLE IF NOT EXISTS item_tombstones (
    seq INTEGER PRIMARY KEY,
    item_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tombstones_item_id ON item_tombstones(item_id);

CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY,
    item_id TEXT,
    sha256 TEXT NOT NULL,
    phash TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL,
    r2_key TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    w INTEGER NOT NULL DEFAULT 0,
    h INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    visibility TEXT NOT NULL DEFAULT 'private',
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_sha256 ON media_assets(sha256);
CREATE INDEX IF NOT EXISTS idx_media_phash ON media_assets(phash);
CREATE INDEX IF NOT EXISTS idx_media_item_id ON media_assets(item_id);

CREATE TABLE IF NOT EXISTS bot_instances (
    instance_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    client_version TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    is_banned INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS feedback_events (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    voter_hash TEXT NOT NULL,
    group_hash TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(item_id, instance_id, voter_hash, action)
);

CREATE INDEX IF NOT EXISTS idx_feedback_item_action ON feedback_events(item_id, action);
CREATE INDEX IF NOT EXISTS idx_feedback_item_group ON feedback_events(item_id, group_hash, action);

CREATE TABLE IF NOT EXISTS submitter_reputation (
    submitter_hash TEXT PRIMARY KEY,
    user_token_hash TEXT UNIQUE,
    trust_level INTEGER NOT NULL DEFAULT 0,
    approved_cnt INTEGER NOT NULL DEFAULT 0,
    rejected_cnt INTEGER NOT NULL DEFAULT 0,
    muted_until TEXT
);

CREATE TABLE IF NOT EXISTS admins (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'moderator',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    admin_id TEXT,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at);

CREATE TABLE IF NOT EXISTS pow_challenges (
    challenge_id TEXT PRIMARY KEY,
    nonce_prefix TEXT NOT NULL,
    difficulty_bits INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""


class Database:
    """Thread-safe SQLite WAL database wrapper."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._write_lock = threading.RLock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,  # explicit transaction control
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def init_schema(self) -> None:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA_SQL)
            finally:
                conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()

    def next_global_seq(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "UPDATE seq_generator SET current_seq = current_seq + 1 "
            "WHERE id = 1 RETURNING current_seq"
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO seq_generator (id, current_seq) VALUES (1, 1)"
            )
            return 1
        return int(row["current_seq"])

    def current_global_seq(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT current_seq FROM seq_generator WHERE id = 1"
        ).fetchone()
        return int(row["current_seq"]) if row else 0

    def seed_admin(self, settings: Settings, password_hasher) -> None:
        with self.write() as conn:
            existing = conn.execute(
                "SELECT id FROM admins WHERE username = ?",
                (settings.admin_username,),
            ).fetchone()
            if existing is not None:
                return
            count_row = conn.execute("SELECT COUNT(*) AS cnt FROM admins").fetchone()
            if count_row and int(count_row["cnt"]) > 0:
                return
            admin_id = generate_prefixed_id("adm")
            pwd_hash = password_hasher(settings.admin_password)
            conn.execute(
                """
                INSERT INTO admins (id, username, password_hash, role, is_active, created_at)
                VALUES (?, ?, ?, 'super_admin', 1, ?)
                """,
                (admin_id, settings.admin_username, pwd_hash, utc_now_iso()),
            )
