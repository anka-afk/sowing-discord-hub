from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.config import Settings
from app.db import Database, utc_now_iso
from app.deps import get_db, get_settings, get_storage
from app.routers.submissions import process_and_store_submission_media
from app.schemas import (
    CollectorIngestRequest,
    CollectorIngestResponse,
    MediaUploadResponse,
)
from app.security import salted_hmac, verify_collector_hmac
from app.services import validate_and_normalize_blocks
from app.storage import StorageBackend
from app.ulid import generate_ulid

router = APIRouter(prefix="/api/v1/internal/collector", tags=["internal"])


async def _require_collector_hmac(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    ts = request.headers.get("x-collector-timestamp")
    sig = request.headers.get("x-collector-signature")
    if not ts or not sig:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired collector HMAC signature",
        )
    nonce = request.headers.get("x-collector-nonce")
    raw_body = (
        request.scope["collector_raw_body"]
        if "collector_raw_body" in request.scope
        else await request.body()
    )
    replay_cache = getattr(request.app.state, "collector_replay_cache", None)
    if not verify_collector_hmac(
        ts,
        sig,
        request.method,
        request.url.path,
        raw_body,
        settings,
        nonce=nonce,
        replay_cache=replay_cache,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired collector HMAC signature",
        )


@router.post("/media", response_model=MediaUploadResponse)
async def collector_upload_media(
    _: None = Depends(_require_collector_hmac),
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> MediaUploadResponse:
    return await process_and_store_submission_media(
        file=file,
        settings=settings,
        db=db,
        storage=storage,
        uploader_hash=salted_hmac("collector", settings, namespace="submitter"),
    )


@router.post(
    "/ingest",
    response_model=CollectorIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def collector_ingest(
    body: CollectorIngestRequest,
    _: None = Depends(_require_collector_hmac),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> CollectorIngestResponse:
    now = utc_now_iso()
    clean_tags = [t.strip()[:32] for t in body.tags if t.strip()][:12]
    submitter_hash = (
        body.submitter_hash.strip()[:128]
        if body.submitter_hash
        else salted_hmac(
            body.source_group_hash or "official_collector",
            settings,
            namespace="collector",
        )
    )
    attribution = (
        body.attribution.model_dump()
        if body.attribution
        else {"submitter_name": "投稿群精选", "origin_label": "官方投稿群"}
    )

    with db.write() as conn:
        # Compute normalized blocks & content_hash first so idempotent collector retries
        # carrying an already-attached media_id still return duplicate=True cleanly.
        norm_blocks, media_ids, content_type, content_hash = validate_and_normalize_blocks(
            body.blocks,
            conn,
            settings,
            require_unattached=False,
        )

        # Deduplicate identical collector ingests already approved or pending
        existing = conn.execute(
            """
            SELECT id FROM items
            WHERE content_hash = ?
              AND status IN ('approved', 'pending', 'pending_recheck')
            LIMIT 1
            """,
            (content_hash,),
        ).fetchone()
        if existing is not None:
            return CollectorIngestResponse(
                submission_id=existing["id"],
                status="pending",
                duplicate=True,
            )

        # For genuinely new items, strictly require every referenced media_id is unattached
        for mid in media_ids:
            mrow = conn.execute(
                "SELECT item_id FROM media_assets WHERE id = ?",
                (mid,),
            ).fetchone()
            if mrow is None or mrow["item_id"] is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Media asset {mid} is already attached to another item",
                )

        submission_id = generate_ulid()
        payload_json = json.dumps(
            {
                "blocks": norm_blocks,
                "attribution": attribution,
                "source_msg_id": body.source_msg_id,
            },
            ensure_ascii=False,
        )
        tags_json = json.dumps(clean_tags, ensure_ascii=False)

        conn.execute(
            """
            INSERT OR IGNORE INTO submitter_reputation (
                submitter_hash, trust_level, approved_cnt, rejected_cnt
            )
            VALUES (?, 1, 0, 0)
            """,
            (submitter_hash,),
        )

        conn.execute(
            """
            INSERT INTO items (
                id, seq, status, content_type, title, summary, payload_json, tags_json,
                nsfw_level, content_hash, source_type, submitter_hash,
                likes, dislikes, reports, score, reviewer_id, reject_reason,
                created_at, published_at, updated_at
            )
            VALUES (
                ?, NULL, 'pending', ?, ?, '', ?, ?,
                0, ?, 'collector_qq', ?,
                0, 0, 0, 0.0, NULL, NULL,
                ?, NULL, ?
            )
            """,
            (
                submission_id,
                content_type,
                body.title.strip()[:200],
                payload_json,
                tags_json,
                content_hash,
                submitter_hash,
                now,
                now,
            ),
        )

        for mid in media_ids:
            conn.execute(
                "UPDATE media_assets SET item_id = ? WHERE id = ? AND item_id IS NULL",
                (submission_id, mid),
            )

    return CollectorIngestResponse(
        submission_id=submission_id,
        status="pending",
        duplicate=False,
    )
