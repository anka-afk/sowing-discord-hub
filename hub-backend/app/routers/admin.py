from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status

from app.config import Settings
from app.db import Database, utc_now_iso
from app.deps import (
    AuthenticatedAdmin,
    get_db,
    get_settings,
    get_storage,
    require_admin,
    require_admin_or_query_token,
)
from app.media import (
    MediaValidationError,
    find_similar_items_for_admin,
    process_uploaded_media,
)
from app.schemas import (
    AdminInfo,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminReviewRequest,
    AdminReviewResponse,
    AdminTakedownRequest,
    AdminTakedownResponse,
    RenderedImageRef,
)
from app.security import (
    create_admin_jwt,
    detect_privacy_warnings,
    verify_password,
)
from app.services import (
    build_auto_summary,
    extract_plain_texts,
    get_submitter_stats,
    serialize_meme_item,
)
from app.storage import StorageBackend
from app.ulid import generate_prefixed_id

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _write_audit_log(
    conn: sqlite3.Connection,
    admin_id: str,
    action: str,
    target_type: str,
    target_id: str,
    detail: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (id, admin_id, action, target_type, target_id, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generate_prefixed_id("aud"),
            admin_id,
            action,
            target_type,
            target_id,
            json.dumps(detail, ensure_ascii=False),
            utc_now_iso(),
        ),
    )


@router.post("/login", response_model=AdminLoginResponse)
def admin_login(
    body: AdminLoginRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> AdminLoginResponse:
    with db.read() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role, is_active FROM admins WHERE username = ?",
            (body.username.strip(),),
        ).fetchone()

    if row is None or int(row["is_active"]) != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not verify_password(row["password_hash"], body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    token = create_admin_jwt(
        admin_id=row["id"],
        username=row["username"],
        role=row["role"],
        settings=settings,
    )
    return AdminLoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_seconds,
        admin=AdminInfo(
            id=row["id"],
            username=row["username"],
            role=row["role"],
        ),
    )


@router.post("/media")
async def upload_admin_media(
    file: UploadFile = File(...),
    purpose: str = Form(default="chat_render"),
    admin: AuthenticatedAdmin = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> dict[str, Any]:
    """Admin media upload (primarily for browser-rendered chat_record slices)."""
    max_raw = max(settings.max_image_bytes, settings.max_gif_bytes) + 1024 * 1024
    raw_bytes = await file.read(max_raw + 1)
    if len(raw_bytes) > max_raw:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds maximum allowed size",
        )

    try:
        processed = process_uploaded_media(
            raw_bytes,
            settings,
            is_chat_render=(purpose == "chat_render"),
        )
    except MediaValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    media_id = generate_prefixed_id("med")
    private_key = f"private/{media_id}.{processed.ext}"
    storage.put_bytes(
        private_key,
        processed.data,
        content_type=processed.mime_type,
        immutable=False,
    )
    preview_url = f"/api/v1/admin/media/{media_id}"

    with db.write() as conn:
        conn.execute(
            """
            INSERT INTO media_assets (
                id, item_id, sha256, phash, mime_type, r2_key, url,
                w, h, size_bytes, visibility, is_deleted, created_at
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'private', 0, ?)
            """,
            (
                media_id,
                processed.sha256,
                processed.phash,
                processed.mime_type,
                private_key,
                preview_url,
                processed.width,
                processed.height,
                processed.size_bytes,
                utc_now_iso(),
            ),
        )

    return {
        "media_id": media_id,
        "url": preview_url,
        "mime": processed.mime_type,
        "w": processed.width,
        "h": processed.height,
        "size_bytes": processed.size_bytes,
    }


@router.get("/media/{media_id}")
def get_admin_media(
    media_id: str,
    admin: AuthenticatedAdmin = Depends(require_admin_or_query_token),
    db: Database = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> Response:
    with db.read() as conn:
        row = conn.execute(
            "SELECT r2_key, mime_type, is_deleted FROM media_assets WHERE id = ?",
            (media_id,),
        ).fetchone()
    if row is None or int(row["is_deleted"]) == 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media not found",
        )
    data = storage.get_bytes(row["r2_key"])
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media blob missing in storage",
        )
    return Response(
        content=data,
        media_type=row["mime_type"],
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/queue")
def get_admin_queue(
    status_filter: str = Query(default="pending", alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    admin: AuthenticatedAdmin = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    allowed_statuses = {"pending", "pending_recheck", "approved", "rejected", "takedown"}
    if status_filter.strip().lower() == "all":
        statuses = list(allowed_statuses)
    else:
        statuses = [
            s.strip()
            for s in status_filter.split(",")
            if s.strip() in allowed_statuses
        ]
        if not statuses:
            statuses = ["pending"]

    placeholders = ",".join("?" for _ in statuses)
    is_pending_only = set(statuses).issubset({"pending", "pending_recheck"})
    order_sql = (
        "COALESCE(sr.trust_level, 0) DESC, i.created_at ASC"
        if is_pending_only
        else "i.created_at DESC"
    )

    with db.read() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM items WHERE status IN ({placeholders})",
            statuses,
        ).fetchone()
        total = int(total_row["cnt"]) if total_row else 0

        rows = conn.execute(
            f"""
            SELECT i.*
            FROM items i
            LEFT JOIN submitter_reputation sr ON sr.submitter_hash = i.submitter_hash
            WHERE i.status IN ({placeholders})
            ORDER BY {order_sql}
            LIMIT ? OFFSET ?
            """,
            [*statuses, limit, offset],
        ).fetchall()

        enriched: list[dict[str, Any]] = []
        for row in rows:
            item_dict = serialize_meme_item(row, settings, for_admin=True)
            texts = extract_plain_texts(row["title"], item_dict["blocks"])
            item_dict["similar_items"] = find_similar_items_for_admin(
                conn,
                row["id"],
                threshold=settings.phash_similar_threshold,
            )
            item_dict["submitter_stats"] = get_submitter_stats(
                conn,
                row["submitter_hash"],
            )
            item_dict["privacy_warnings"] = detect_privacy_warnings(texts)
            enriched.append(item_dict)

    return {"items": enriched, "total": total}


def _extract_rendered_media_ids(
    req: AdminReviewRequest,
    block_index: int,
    existing_rendered: list[dict[str, Any]],
) -> list[str]:
    if req.chat_renders and str(block_index) in req.chat_renders:
        return [mid for mid in req.chat_renders[str(block_index)] if mid]
    if req.rendered_images:
        out: list[str] = []
        for entry in req.rendered_images:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
            elif isinstance(entry, RenderedImageRef) and entry.media_id.strip():
                out.append(entry.media_id.strip())
        return out
    # Keep already-attached rendered_images if re-approving a pending_recheck item
    return [
        str(ri["media_id"])
        for ri in existing_rendered
        if isinstance(ri, dict) and ri.get("media_id")
    ]


def _promote_media_to_public(
    conn: sqlite3.Connection,
    storage: StorageBackend,
    media_id: str,
    item_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT id, mime_type, r2_key, url, w, h, size_bytes, visibility, is_deleted
        FROM media_assets
        WHERE id = ?
        """,
        (media_id,),
    ).fetchone()
    if row is None or int(row["is_deleted"]) == 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Media asset missing or deleted: {media_id}",
        )
    ext = "gif" if row["mime_type"] == "image/gif" else "webp"
    public_key = f"public/m/{media_id}.{ext}"
    if row["visibility"] != "public" or row["r2_key"] != public_key:
        public_url = storage.move_to_public(
            private_key=row["r2_key"],
            public_key=public_key,
            content_type=row["mime_type"],
        )
        conn.execute(
            """
            UPDATE media_assets
            SET item_id = ?, r2_key = ?, url = ?, visibility = 'public'
            WHERE id = ?
            """,
            (item_id, public_key, public_url, media_id),
        )
    else:
        public_url = row["url"] or storage.public_url(public_key)
        conn.execute(
            "UPDATE media_assets SET item_id = ? WHERE id = ?",
            (item_id, media_id),
        )
    return {
        "media_id": media_id,
        "url": public_url,
        "mime": row["mime_type"],
        "w": int(row["w"]),
        "h": int(row["h"]),
        "size_bytes": int(row["size_bytes"]),
    }


@router.post("/items/{item_id}/review", response_model=AdminReviewResponse)
def review_item(
    item_id: str,
    body: AdminReviewRequest,
    admin: AuthenticatedAdmin = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> AdminReviewResponse:
    now = utc_now_iso()

    with db.write() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found",
            )

        submitter_hash = row["submitter_hash"]

        if body.action == "reject":
            reason = (body.reject_reason or "rejected").strip()[:200]
            conn.execute(
                """
                UPDATE items
                SET status = 'rejected', reviewer_id = ?, reject_reason = ?, updated_at = ?
                WHERE id = ?
                """,
                (admin.id, reason, now, item_id),
            )
            if submitter_hash:
                conn.execute(
                    """
                    INSERT INTO submitter_reputation (submitter_hash, trust_level, approved_cnt, rejected_cnt)
                    VALUES (?, 0, 0, 1)
                    ON CONFLICT(submitter_hash) DO UPDATE SET rejected_cnt = rejected_cnt + 1
                    """,
                    (submitter_hash,),
                )
            _write_audit_log(
                conn,
                admin.id,
                "review_reject",
                "item",
                item_id,
                {"reason": reason},
            )
            return AdminReviewResponse(
                item_id=item_id,
                status="rejected",
                seq=None,
                summary=row["summary"],
                published_at=None,
            )

        # Approve flow
        payload = json.loads(row["payload_json"])
        blocks: list[dict[str, Any]] = payload.get("blocks", [])
        title = body.title.strip()[:200] if body.title is not None else row["title"]
        tags = (
            [t.strip()[:32] for t in body.tags if t.strip()][:12]
            if body.tags is not None
            else json.loads(row["tags_json"] or "[]")
        )
        nsfw_level = body.nsfw_level if body.nsfw_level is not None else int(row["nsfw_level"])

        # Hard check: every chat_record block MUST have non-empty rendered_images
        updated_blocks: list[dict[str, Any]] = []
        first_chat_edited = False
        for idx, blk in enumerate(blocks):
            b = dict(blk)
            btype = b.get("type")

            if btype in ("image", "gif"):
                mid = b.get("media_id")
                if mid:
                    promoted = _promote_media_to_public(conn, storage, mid, item_id)
                    b.update(promoted)

            elif btype == "video_link":
                cmid = b.get("cover_media_id")
                if cmid:
                    promoted = _promote_media_to_public(conn, storage, cmid, item_id)
                    b["cover_url"] = promoted["url"]

            elif btype == "chat_record":
                if body.edited_chat_messages is not None and not first_chat_edited:
                    if len(body.edited_chat_messages) > settings.max_chat_nodes:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"edited_chat_messages exceeds max_chat_nodes ({settings.max_chat_nodes})",
                        )
                    b["messages"] = body.edited_chat_messages
                    first_chat_edited = True

                render_mids = _extract_rendered_media_ids(
                    body, idx, b.get("rendered_images") or []
                )
                if not render_mids:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Approving a chat_record item requires non-empty rendered_images",
                    )

                rendered_objs: list[dict[str, Any]] = []
                for rmid in render_mids:
                    promoted = _promote_media_to_public(conn, storage, rmid, item_id)
                    rendered_objs.append(promoted)
                b["rendered_images"] = rendered_objs

                # Also promote inline images inside chat_record messages
                new_msgs = []
                for msg in b.get("messages") or []:
                    mcopy = dict(msg)
                    new_content = []
                    for seg in mcopy.get("content") or []:
                        scopy = dict(seg)
                        if scopy.get("type") in ("image", "gif") and scopy.get("media_id"):
                            promoted = _promote_media_to_public(
                                conn, storage, scopy["media_id"], item_id
                            )
                            scopy.update(promoted)
                        new_content.append(scopy)
                    mcopy["content"] = new_content
                    new_msgs.append(mcopy)
                b["messages"] = new_msgs

            updated_blocks.append(b)

        summary = (body.summary or "").strip()
        if not summary:
            summary = build_auto_summary(title, updated_blocks, max_len=120)

        payload["blocks"] = updated_blocks
        new_seq = db.next_global_seq(conn)
        published_at = row["published_at"] or now

        conn.execute(
            """
            UPDATE items
            SET status = 'approved',
                seq = ?,
                title = ?,
                summary = ?,
                payload_json = ?,
                tags_json = ?,
                nsfw_level = ?,
                reviewer_id = ?,
                reject_reason = NULL,
                published_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_seq,
                title,
                summary,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(tags, ensure_ascii=False),
                nsfw_level,
                admin.id,
                published_at,
                now,
                item_id,
            ),
        )

        if submitter_hash:
            conn.execute(
                """
                INSERT INTO submitter_reputation (submitter_hash, trust_level, approved_cnt, rejected_cnt)
                VALUES (?, 0, 1, 0)
                ON CONFLICT(submitter_hash) DO UPDATE SET approved_cnt = approved_cnt + 1
                """,
                (submitter_hash,),
            )
            rep = conn.execute(
                "SELECT approved_cnt, rejected_cnt, trust_level FROM submitter_reputation WHERE submitter_hash = ?",
                (submitter_hash,),
            ).fetchone()
            if rep:
                app_c = int(rep["approved_cnt"])
                rej_c = int(rep["rejected_cnt"])
                if app_c >= 5 and app_c / max(1, app_c + rej_c) >= 0.8 and int(rep["trust_level"]) < 2:
                    conn.execute(
                        "UPDATE submitter_reputation SET trust_level = 2 WHERE submitter_hash = ?",
                        (submitter_hash,),
                    )

        _write_audit_log(
            conn,
            admin.id,
            "review_approve",
            "item",
            item_id,
            {"seq": new_seq, "nsfw_level": nsfw_level, "tags": tags},
        )

    return AdminReviewResponse(
        item_id=item_id,
        status="approved",
        seq=new_seq,
        summary=summary,
        published_at=published_at,
    )


@router.post("/items/{item_id}/takedown", response_model=AdminTakedownResponse)
async def takedown_item(
    item_id: str,
    body: AdminTakedownRequest,
    admin: AuthenticatedAdmin = Depends(require_admin),
    db: Database = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> AdminTakedownResponse:
    now = utc_now_iso()
    keys_to_delete: list[str] = []
    urls_to_purge: list[str] = []

    with db.write() as conn:
        row = conn.execute("SELECT id, status FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Item not found",
            )

        tomb_seq = db.next_global_seq(conn)
        conn.execute(
            """
            UPDATE items
            SET status = 'takedown', reviewer_id = ?, reject_reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (admin.id, body.reason.strip()[:200], now, item_id),
        )
        conn.execute(
            """
            INSERT INTO item_tombstones (seq, item_id, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (tomb_seq, item_id, body.reason.strip()[:200], now),
        )

        media_rows = conn.execute(
            "SELECT id, r2_key, url, visibility, is_deleted FROM media_assets WHERE item_id = ?",
            (item_id,),
        ).fetchall()
        for mr in media_rows:
            if int(mr["is_deleted"]) == 0:
                keys_to_delete.append(mr["r2_key"])
                if mr["visibility"] == "public" and mr["url"]:
                    urls_to_purge.append(mr["url"])
        conn.execute(
            "UPDATE media_assets SET is_deleted = 1 WHERE item_id = ?",
            (item_id,),
        )

        _write_audit_log(
            conn,
            admin.id,
            "takedown",
            "item",
            item_id,
            {
                "reason": body.reason.strip()[:200],
                "tombstone_seq": tomb_seq,
                "deleted_media_count": len(keys_to_delete),
            },
        )

    for key in keys_to_delete:
        storage.delete(key)
    if urls_to_purge:
        await storage.purge_urls(urls_to_purge)

    return AdminTakedownResponse(
        item_id=item_id,
        status="takedown",
        tombstone_seq=tomb_seq,
    )


@router.get("/stats")
def get_admin_stats(
    admin: AuthenticatedAdmin = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> dict[str, Any]:
    with db.read() as conn:
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM items GROUP BY status"
        ).fetchall()
        counts = {r["status"]: int(r["cnt"]) for r in status_rows}

        storage_row = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total_bytes FROM media_assets WHERE is_deleted = 0"
        ).fetchone()
        storage_bytes = int(storage_row["total_bytes"]) if storage_row else 0
        limit_bytes = settings.storage_limit_bytes
        usage_ratio = round(storage_bytes / limit_bytes, 6) if limit_bytes > 0 else 0.0

        top_rows = conn.execute(
            """
            SELECT * FROM items
            WHERE status = 'approved'
            ORDER BY score DESC, likes DESC, seq DESC
            LIMIT 10
            """
        ).fetchall()
        top_items = [serialize_meme_item(r, settings) for r in top_rows]

        audit_rows = conn.execute(
            """
            SELECT a.id, a.admin_id, adm.username AS admin_username, a.action,
                   a.target_type, a.target_id, a.detail_json, a.created_at
            FROM audit_logs a
            LEFT JOIN admins adm ON adm.id = a.admin_id
            ORDER BY a.created_at DESC
            LIMIT 20
            """
        ).fetchall()
        recent_audits = [
            {
                "id": r["id"],
                "admin_id": r["admin_id"],
                "admin_username": r["admin_username"],
                "action": r["action"],
                "target_type": r["target_type"],
                "target_id": r["target_id"],
                "detail": json.loads(r["detail_json"] or "{}"),
                "created_at": r["created_at"],
            }
            for r in audit_rows
        ]

    return {
        "pending_count": counts.get("pending", 0),
        "pending_recheck_count": counts.get("pending_recheck", 0),
        "approved_count": counts.get("approved", 0),
        "rejected_count": counts.get("rejected", 0),
        "takedown_count": counts.get("takedown", 0),
        "storage_bytes": storage_bytes,
        "storage_limit_bytes": limit_bytes,
        "storage_usage_ratio": usage_ratio,
        "top_items": top_items,
        "recent_audit_logs": recent_audits,
    }
