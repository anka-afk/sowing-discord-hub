from __future__ import annotations

import hashlib
import io
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import imagehash
from PIL import Image

# Protect against decompression bombs
Image.MAX_IMAGE_PIXELS = 40_000_000


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_text_hash(text: str) -> str:
    normalized = " ".join(text.strip().split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_phash(image_bytes: bytes) -> str:
    """Compute perceptual hash (64-bit hex) for static image or first frame of GIF."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        rgb = img.convert("RGB")
        return str(imagehash.phash(rgb))


def phash_distance(hash_a: str, hash_b: str) -> int:
    try:
        return int(imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b))
    except Exception:
        return 64


@dataclass
class MediaDupResult:
    duplicate: bool
    match_type: str | None = None  # "exact" | "similar"
    matched_sha256: str | None = None
    distance: int | None = None


class DedupStore:
    """Local SQLite + in-memory index for message, content SHA-256, and media SHA-256/pHash dedup."""

    def __init__(self, db_path: str | Path = ":memory:", phash_threshold: int = 8) -> None:
        self.db_path = str(db_path)
        self.phash_threshold = phash_threshold
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_tables()

        # In-memory mirrors for O(1) lookup and fast pHash scan
        self._seen_messages: set[tuple[int, str]] = set()
        self._seen_contents: set[str] = set()
        self._media_sha256: set[str] = set()
        self._media_phashes: list[tuple[str, str]] = []  # (sha256, phash)
        self._load_memory_cache()

    def _init_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_messages (
                group_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (group_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS seen_content_hashes (
                content_hash TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS seen_media (
                sha256 TEXT PRIMARY KEY,
                phash TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_seen_media_phash ON seen_media(phash);
            """
        )
        self._conn.commit()

    def _load_memory_cache(self) -> None:
        cur = self._conn.cursor()
        for group_id, message_id in cur.execute("SELECT group_id, message_id FROM seen_messages"):
            self._seen_messages.add((int(group_id), str(message_id)))
        for (content_hash,) in cur.execute("SELECT content_hash FROM seen_content_hashes"):
            self._seen_contents.add(str(content_hash))
        for sha256, phash in cur.execute("SELECT sha256, phash FROM seen_media"):
            self._media_sha256.add(str(sha256))
            if phash:
                self._media_phashes.append((str(sha256), str(phash)))

    def close(self) -> None:
        self._conn.close()

    def is_message_seen(self, group_id: int, message_id: str | int) -> bool:
        return (int(group_id), str(message_id)) in self._seen_messages

    def mark_message_seen(self, group_id: int, message_id: str | int, trigger_source: str) -> None:
        key = (int(group_id), str(message_id))
        if key in self._seen_messages:
            return
        self._seen_messages.add(key)
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_messages (group_id, message_id, trigger_source, created_at) VALUES (?, ?, ?, ?)",
            (key[0], key[1], trigger_source, time.time()),
        )
        self._conn.commit()

    def is_content_duplicate(self, content_hash: str) -> bool:
        return content_hash in self._seen_contents

    def mark_content_seen(self, content_hash: str) -> None:
        if content_hash in self._seen_contents:
            return
        self._seen_contents.add(content_hash)
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_content_hashes (content_hash, created_at) VALUES (?, ?)",
            (content_hash, time.time()),
        )
        self._conn.commit()

    def check_media_duplicate(self, sha256: str, phash: str | None = None) -> MediaDupResult:
        if sha256 in self._media_sha256:
            return MediaDupResult(duplicate=True, match_type="exact", matched_sha256=sha256, distance=0)
        if phash:
            for existing_sha, existing_phash in self._media_phashes:
                dist = phash_distance(phash, existing_phash)
                if dist <= self.phash_threshold:
                    return MediaDupResult(
                        duplicate=True,
                        match_type="similar",
                        matched_sha256=existing_sha,
                        distance=dist,
                    )
        return MediaDupResult(duplicate=False)

    def mark_media_seen(self, sha256: str, phash: str) -> None:
        if sha256 in self._media_sha256:
            return
        self._media_sha256.add(sha256)
        if phash:
            self._media_phashes.append((sha256, phash))
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_media (sha256, phash, created_at) VALUES (?, ?, ?)",
            (sha256, phash, time.time()),
        )
        self._conn.commit()
