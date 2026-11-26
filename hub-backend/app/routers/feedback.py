from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import Settings
from app.db import Database, utc_now_iso
from app.deps import AuthenticatedInstance, get_db, get_settings, require_instance
from app.schemas import (
    VALID_REPORT_REASONS,
    FeedbackBatchRequest,
    FeedbackBatchResponse,
)
from app.services import compute_wilson_score
from app.ulid import generate_prefixed_id

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


@router.post("/batch", response_model=FeedbackBatchResponse)
def submit_feedback_batch(
    body: FeedbackBatchRequest,
    instance: AuthenticatedInstance = Depends(require_instance),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> FeedbackBatchResponse:
    if len(body.events) > settings.feedback_batch_max:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Batch exceeds maximum of {settings.feedback_batch_max} events",
        )

    now = utc_now_iso()
    cutoff_24h = (
        datetime.now(timezone.utc)
        - timedelta(hours=settings.feedback_instance_min_age_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    accepted = 0
    deduplicated = 0
    capped = 0
    ignored = 0
    affected_items: set[str] = set()
    circuit_broken: list[str] = []

    with db.write() as conn:
        for ev in body.events:
            item_row = conn.execute(
                "SELECT id, status FROM items WHERE id = ?",
                (ev.item_id,),
            ).fetchone()
            if item_row is None or item_row["status"] != "approved":
                ignored += 1
                continue

            reason = ev.reason
            if ev.action == "report":
                if reason and reason not in VALID_REPORT_REASONS:
                    reason = "other"
                elif not reason:
                    reason = "other"
            else:
                reason = None

            # Check duplicate first so repeated retries report deduplicated rather than capped
            exists = conn.execute(
                """
                SELECT 1 FROM feedback_events
                WHERE item_id = ? AND instance_id = ? AND voter_hash = ? AND action = ?
                LIMIT 1
                """,
                (ev.item_id, instance.instance_id, ev.voter_hash, ev.action),
            ).fetchone()
            if exists is not None:
                deduplicated += 1
                continue

            # Per-group contribution cap for a given (item_id, group_hash, action)
            grp_cnt_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM feedback_events
                WHERE item_id = ? AND group_hash = ? AND action = ?
                """,
                (ev.item_id, ev.group_hash, ev.action),
            ).fetchone()
            if grp_cnt_row and int(grp_cnt_row["cnt"]) >= settings.feedback_max_group_contribution:
                capped += 1
                continue

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO feedback_events (
                    id, item_id, instance_id, voter_hash, group_hash,
                    action, reason, occurred_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generate_prefixed_id("fbk"),
                    ev.item_id,
                    instance.instance_id,
                    ev.voter_hash,
                    ev.group_hash,
                    ev.action,
                    reason,
                    ev.occurred_at,
                    now,
                ),
            )
            if cur.rowcount == 0:
                deduplicated += 1
            else:
                accepted += 1
                affected_items.add(ev.item_id)

        # Recompute aggregates, Wilson Score, and circuit breaker per affected item
        for item_id in affected_items:
            counts = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN action = 'like' THEN 1 ELSE 0 END) AS likes,
                    SUM(CASE WHEN action = 'dislike' THEN 1 ELSE 0 END) AS dislikes,
                    SUM(CASE WHEN action = 'report' THEN 1 ELSE 0 END) AS reports
                FROM feedback_events
                WHERE item_id = ?
                """,
                (item_id,),
            ).fetchone()
            likes = int(counts["likes"] or 0)
            dislikes = int(counts["dislikes"] or 0)
            reports = int(counts["reports"] or 0)
            score = compute_wilson_score(likes, dislikes)

            conn.execute(
                """
                UPDATE items
                SET likes = ?, dislikes = ?, reports = ?, score = ?, updated_at = ?
                WHERE id = ?
                """,
                (likes, dislikes, reports, score, now, item_id),
            )

            # Circuit breaker: >=3 distinct non-banned instances registered >=24h ago
            mature_reporters = conn.execute(
                """
                SELECT COUNT(DISTINCT fe.instance_id) AS cnt
                FROM feedback_events fe
                JOIN bot_instances bi ON bi.instance_id = fe.instance_id
                WHERE fe.item_id = ?
                  AND fe.action = 'report'
                  AND bi.is_banned = 0
                  AND bi.created_at <= ?
                """,
                (item_id, cutoff_24h),
            ).fetchone()

            if (
                mature_reporters
                and int(mature_reporters["cnt"]) >= settings.feedback_report_threshold
            ):
                cur_item = conn.execute(
                    "SELECT status FROM items WHERE id = ?", (item_id,)
                ).fetchone()
                if cur_item and cur_item["status"] == "approved":
                    tomb_seq = db.next_global_seq(conn)
                    conn.execute(
                        """
                        UPDATE items
                        SET status = 'pending_recheck', updated_at = ?
                        WHERE id = ?
                        """,
                        (now, item_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO item_tombstones (seq, item_id, reason, created_at)
                        VALUES (?, ?, 'report_circuit_breaker', ?)
                        """,
                        (tomb_seq, item_id, now),
                    )
                    circuit_broken.append(item_id)

    return FeedbackBatchResponse(
        accepted=accepted,
        deduplicated=deduplicated,
        capped=capped,
        ignored=ignored,
        circuit_broken_items=circuit_broken,
    )
