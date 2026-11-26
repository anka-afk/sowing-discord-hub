from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from app.config import Settings
from app.db import Database
from app.deps import get_db, get_settings
from app.security import sha256_hex
from app.services import serialize_meme_item

router = APIRouter(prefix="/api/v1", tags=["feed"])


def _parse_tag_list(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for entry in raw:
        for part in entry.split(","):
            t = part.strip()
            if t and t not in out:
                out.append(t)
    return out


def _matches_tags(
    item_tags: list[str],
    include_tags: list[str],
    exclude_tags: list[str],
) -> bool:
    tag_set = set(item_tags)
    if include_tags and not (tag_set & set(include_tags)):
        return False
    if exclude_tags and (tag_set & set(exclude_tags)):
        return False
    return True


@router.get("/feed")
def get_feed(
    request: Request,
    after_seq: int | None = Query(default=None, ge=0),
    before_seq: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
    tags: list[str] | None = Query(default=None),
    exclude_tags: list[str] | None = Query(default=None),
    max_nsfw: int = Query(default=1, ge=0, le=2),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> Response:
    inc_tags = _parse_tag_list(tags)
    exc_tags = _parse_tag_list(exclude_tags)

    with db.read() as conn:
        if after_seq is not None:
            # Incremental cursor sync (ascending seq + tombstones)
            rows = conn.execute(
                """
                SELECT * FROM items
                WHERE status = 'approved'
                  AND seq IS NOT NULL
                  AND seq > ?
                  AND nsfw_level <= ?
                ORDER BY seq ASC
                """,
                (after_seq, max_nsfw),
            ).fetchall()

            matched = [
                r
                for r in rows
                if _matches_tags(json.loads(r["tags_json"] or "[]"), inc_tags, exc_tags)
            ]
            has_more = len(matched) > limit
            page_rows = matched[:limit]
            items = [serialize_meme_item(r, settings) for r in page_rows]

            if has_more and page_rows:
                upper_seq = int(page_rows[-1]["seq"])
                tomb_rows = conn.execute(
                    """
                    SELECT seq, item_id FROM item_tombstones
                    WHERE seq > ? AND seq <= ?
                    ORDER BY seq ASC
                    """,
                    (after_seq, upper_seq),
                ).fetchall()
            else:
                tomb_rows = conn.execute(
                    """
                    SELECT seq, item_id FROM item_tombstones
                    WHERE seq > ?
                    ORDER BY seq ASC
                    """,
                    (after_seq,),
                ).fetchall()

            tombstones: list[str] = []
            max_tomb_seq = after_seq
            for tr in tomb_rows:
                max_tomb_seq = max(max_tomb_seq, int(tr["seq"]))
                if tr["item_id"] not in tombstones:
                    tombstones.append(tr["item_id"])

            max_item_seq = int(page_rows[-1]["seq"]) if page_rows else after_seq
            if has_more:
                next_seq = max_item_seq
            else:
                next_seq = max(after_seq, max_item_seq, max_tomb_seq)
        else:
            # Browse / historical reverse-chronological mode
            params: list[Any] = [max_nsfw]
            where = "status = 'approved' AND seq IS NOT NULL AND nsfw_level <= ?"
            if before_seq is not None:
                where += " AND seq < ?"
                params.append(before_seq)
            rows = conn.execute(
                f"SELECT * FROM items WHERE {where} ORDER BY seq DESC",
                params,
            ).fetchall()

            matched = [
                r
                for r in rows
                if _matches_tags(json.loads(r["tags_json"] or "[]"), inc_tags, exc_tags)
            ]
            has_more = len(matched) > limit
            page_rows = matched[:limit]
            items = [serialize_meme_item(r, settings) for r in page_rows]
            tombstones = []
            if before_seq is None:
                next_seq = db.current_global_seq(conn)
            else:
                next_seq = int(page_rows[-1]["seq"]) if page_rows else 0

    body = {
        "items": items,
        "tombstones": tombstones,
        "next_seq": next_seq,
        "has_more": has_more,
    }
    raw_json = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    etag = f'W/"{sha256_hex(raw_json)[:32]}"'

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )

    return JSONResponse(
        content=body,
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@router.get("/random")
def get_random_items(
    limit: int = Query(default=20, ge=1, le=50),
    tags: list[str] | None = Query(default=None),
    exclude_tags: list[str] | None = Query(default=None),
    max_nsfw: int = Query(default=1, ge=0, le=2),
    older_than_days: int | None = Query(default=None, ge=0),
    within_days: int | None = Query(default=None, ge=0),
    min_score: float | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    inc_tags = _parse_tag_list(tags)
    exc_tags = _parse_tag_list(exclude_tags)

    now = datetime.now(timezone.utc)
    clauses = ["status = 'approved'", "seq IS NOT NULL", "nsfw_level <= ?"]
    params: list[Any] = [max_nsfw]

    if older_than_days is not None:
        cutoff = (now - timedelta(days=older_than_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        clauses.append("published_at <= ?")
        params.append(cutoff)

    if within_days is not None:
        cutoff = (now - timedelta(days=within_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        clauses.append("published_at >= ?")
        params.append(cutoff)

    if min_score is not None:
        clauses.append("score >= ?")
        params.append(min_score)

    sql = f"SELECT * FROM items WHERE {' AND '.join(clauses)} ORDER BY seq DESC LIMIT 500"
    with db.read() as conn:
        rows = conn.execute(sql, params).fetchall()

    candidates = [
        r
        for r in rows
        if _matches_tags(json.loads(r["tags_json"] or "[]"), inc_tags, exc_tags)
    ]

    if len(candidates) <= limit:
        random.shuffle(candidates)
        chosen = candidates
    else:
        # Weighted random sampling without replacement (higher Wilson score gets higher weight)
        pool = list(candidates)
        weights = [1.0 + max(0.0, float(r["score"])) / 10.0 for r in pool]
        chosen = []
        for _ in range(limit):
            total_w = sum(weights)
            pick = random.uniform(0.0, total_w)
            acc = 0.0
            picked_idx = len(pool) - 1
            for i, w in enumerate(weights):
                acc += w
                if acc >= pick:
                    picked_idx = i
                    break
            chosen.append(pool.pop(picked_idx))
            weights.pop(picked_idx)

    return {"items": [serialize_meme_item(r, settings) for r in chosen]}


@router.get("/items/{item_id}")
def get_item_detail(
    item_id: str,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    with db.read() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            tomb = conn.execute(
                "SELECT seq FROM item_tombstones WHERE item_id = ? LIMIT 1",
                (item_id,),
            ).fetchone()
            if tomb is not None:
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail="Item has been taken down",
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found",
            )

        if row["status"] in ("takedown", "pending_recheck"):
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Item has been taken down or suspended pending recheck",
            )
        if row["status"] != "approved":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found",
            )

        return serialize_meme_item(row, settings)
