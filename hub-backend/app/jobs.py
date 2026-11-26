from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.db import Database, utc_now_iso
from app.storage import StorageBackend, create_storage

logger = logging.getLogger(__name__)


def run_media_cleanup(
    db: Database,
    storage: StorageBackend,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Physically delete media blobs for expired rejected/stale items while keeping sha256/phash.

    Policy from IMPLEMENTATION_PLAN.md:
    - Rejected items older than 7 days: delete media files, mark is_deleted=1, preserve hashes.
    - Unreviewed pending items older than 14 days (+7 days retention): reject & delete media files.
    - Orphaned unattached uploads older than 24 hours: delete media files & mark is_deleted=1.
    - Expired PoW challenges: prune old rows.
    """
    ref_now = now or datetime.now(timezone.utc)
    rejected_cutoff = (
        ref_now - timedelta(days=settings.rejected_media_retention_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_pending_cutoff = (
        ref_now
        - timedelta(days=settings.stale_pending_days + settings.rejected_media_retention_days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    orphan_cutoff = (ref_now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pow_cutoff = (ref_now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now_iso = ref_now.strftime("%Y-%m-%dT%H:%M:%SZ")

    keys_to_delete: list[str] = []
    deleted_rejected_media = 0
    expired_stale_items = 0
    deleted_orphan_media = 0
    pruned_challenges = 0

    with db.write() as conn:
        # 1. Mark stale pending items (> 14 + 7 days) as rejected
        stale_rows = conn.execute(
            """
            SELECT id FROM items
            WHERE status = 'pending' AND created_at <= ?
            """,
            (stale_pending_cutoff,),
        ).fetchall()
        for sr in stale_rows:
            conn.execute(
                """
                UPDATE items
                SET status = 'rejected', reject_reason = 'stale_expired', updated_at = ?
                WHERE id = ?
                """,
                (now_iso, sr["id"]),
            )
            expired_stale_items += 1

        # 2. Delete media blobs for rejected items past retention (and stale-expired items)
        rej_media_rows = conn.execute(
            """
            SELECT m.id, m.r2_key
            FROM media_assets m
            JOIN items i ON i.id = m.item_id
            WHERE m.is_deleted = 0
              AND i.status = 'rejected'
              AND (i.updated_at <= ? OR i.reject_reason = 'stale_expired')
            """,
            (rejected_cutoff,),
        ).fetchall()
        for mr in rej_media_rows:
            keys_to_delete.append(mr["r2_key"])
            conn.execute(
                "UPDATE media_assets SET is_deleted = 1 WHERE id = ?",
                (mr["id"],),
            )
            deleted_rejected_media += 1

        # 3. Delete orphaned uploads never attached to an item (>24h old)
        orphan_rows = conn.execute(
            """
            SELECT id, r2_key FROM media_assets
            WHERE item_id IS NULL AND is_deleted = 0 AND created_at <= ?
            """,
            (orphan_cutoff,),
        ).fetchall()
        for orow in orphan_rows:
            keys_to_delete.append(orow["r2_key"])
            conn.execute(
                "UPDATE media_assets SET is_deleted = 1 WHERE id = ?",
                (orow["id"],),
            )
            deleted_orphan_media += 1

        # 4. Prune expired PoW challenges
        cur = conn.execute(
            "DELETE FROM pow_challenges WHERE expires_at <= ?",
            (pow_cutoff,),
        )
        pruned_challenges = cur.rowcount or 0

    for key in keys_to_delete:
        storage.delete(key)

    result = {
        "deleted_rejected_media": deleted_rejected_media,
        "expired_stale_items": expired_stale_items,
        "deleted_orphan_media": deleted_orphan_media,
        "pruned_challenges": pruned_challenges,
    }
    logger.info("Media cleanup completed: %s", result)
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    settings.ensure_dirs()
    db = Database(settings.database_path)
    storage = create_storage(settings)
    summary = run_media_cleanup(db, storage, settings)
    print(f"[{utc_now_iso()}] Cleanup summary: {summary}")


if __name__ == "__main__":
    main()
