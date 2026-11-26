from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.config import Settings
from app.db import Database, utc_now_iso
from app.deps import (
    extract_bearer_token,
    get_db,
    get_settings,
    get_storage,
    resolve_admin_from_token,
    resolve_instance_from_token,
)
from app.media import (
    MediaValidationError,
    find_duplicate_media,
    process_uploaded_media,
)
from app.schemas import (
    MediaUploadResponse,
    SubmissionCreateRequest,
    SubmissionCreateResponse,
)
from app.security import (
    parse_iso_utc,
    salted_hmac,
    sha256_hex,
    verify_collector_hmac,
    verify_turnstile,
)
from app.services import validate_and_normalize_blocks
from app.storage import StorageBackend
from app.ulid import generate_prefixed_id, generate_ulid

router = APIRouter(prefix="/api/v1/submissions", tags=["submissions"])


async def process_and_store_submission_media(
    *,
    file: UploadFile,
    settings: Settings,
    db: Database,
    storage: StorageBackend,
) -> MediaUploadResponse:
    max_raw = max(settings.max_image_bytes, settings.max_gif_bytes) + 1024 * 1024
    raw_bytes = await file.read(max_raw + 1)
    if len(raw_bytes) > max_raw:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds maximum allowed size",
        )

    try:
        processed = process_uploaded_media(raw_bytes, settings)
    except MediaValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    with db.write() as conn:
        dup = find_duplicate_media(
            conn,
            sha256_hex=processed.sha256,
            phash_hex=processed.phash,
            threshold=settings.phash_similar_threshold,
        )
        if dup is not None:
            # Privacy rule: ONLY return existing_item_id when approved; pending MUST be null
            existing_id = (
                dup["existing_item_id"] if dup["existing_status"] == "approved" else None
            )
            return MediaUploadResponse(
                media_id=None,
                mime=processed.mime_type,
                w=processed.width,
                h=processed.height,
                size_bytes=processed.size_bytes,
                preview_url=None,
                duplicate=True,
                match_type=dup["match_type"],
                existing_status=dup["existing_status"],
                existing_item_id=existing_id,
            )

        media_id = generate_prefixed_id("med")
        private_key = f"private/{media_id}.{processed.ext}"
        storage.put_bytes(
            private_key,
            processed.data,
            content_type=processed.mime_type,
            immutable=False,
        )
        preview_url = f"/api/v1/admin/media/{media_id}"
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

    return MediaUploadResponse(
        media_id=media_id,
        mime=processed.mime_type,
        w=processed.width,
        h=processed.height,
        size_bytes=processed.size_bytes,
        preview_url=preview_url,
        duplicate=False,
        match_type=None,
        existing_status=None,
        existing_item_id=None,
    )


@router.post("/media", response_model=MediaUploadResponse)
async def upload_submission_media(
    request: Request,
    file: UploadFile = File(...),
    turnstile_token: str | None = Form(default=None),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> MediaUploadResponse:
    # Auth modes: Collector HMAC | Bearer instance_token | Bearer admin_jwt | Web Turnstile
    col_ts = request.headers.get("x-collector-timestamp")
    col_sig = request.headers.get("x-collector-signature")
    bearer = extract_bearer_token(request)

    if col_ts or col_sig:
        raw_body = (
            request.scope["collector_raw_body"]
            if "collector_raw_body" in request.scope
            else await request.body()
        )
        if not verify_collector_hmac(
            col_ts, col_sig, request.method, request.url.path, raw_body, settings
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid collector HMAC signature",
            )
    elif bearer:
        inst = resolve_instance_from_token(bearer, db)
        admin = None if inst else resolve_admin_from_token(bearer, settings, db)
        if inst is None and admin is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Bearer token",
            )
    else:
        ts_token = turnstile_token or request.headers.get("x-turnstile-token")
        client_ip = request.client.host if request.client else None
        if not await verify_turnstile(ts_token, settings, remote_ip=client_ip):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Turnstile verification failed",
            )

    return await process_and_store_submission_media(
        file=file,
        settings=settings,
        db=db,
        storage=storage,
    )


@router.post(
    "",
    response_model=SubmissionCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_submission(
    request: Request,
    body: SubmissionCreateRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> SubmissionCreateResponse:
    bearer = extract_bearer_token(request)
    user_token = (request.headers.get("x-user-token") or "").strip() or None
    client_ip = request.client.host if request.client else "unknown"

    source_type = "web"
    identity_raw = f"ip:{client_ip}"

    if bearer:
        inst = resolve_instance_from_token(bearer, db)
        if inst is not None:
            source_type = "plugin"
            identity_raw = f"inst:{inst.instance_id}"
        else:
            admin = resolve_admin_from_token(bearer, settings, db)
            if admin is not None:
                source_type = "web"
                identity_raw = f"admin:{admin.id}"
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid Bearer token",
                )
    else:
        ts_token = body.turnstile_token or request.headers.get("x-turnstile-token")
        if not await verify_turnstile(ts_token, settings, remote_ip=client_ip):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Turnstile verification failed",
            )

    user_token_hash = sha256_hex(user_token) if user_token else None
    if user_token_hash:
        submitter_hash = salted_hmac(user_token_hash, settings, namespace="user_token")
    else:
        submitter_hash = salted_hmac(identity_raw, settings, namespace="submitter")

    now = utc_now_iso()
    now_dt = datetime.now(timezone.utc)
    submission_id = generate_ulid()
    clean_tags = [t.strip()[:32] for t in body.tags if t.strip()][:12]
    attribution = (
        body.attribution.model_dump()
        if body.attribution
        else {
            "submitter_name": "匿名网友",
            "origin_label": "插件投稿" if source_type == "plugin" else "Web 投稿",
        }
    )

    with db.write() as conn:
        # Check submitter reputation / mute & upsert trusted user token channel
        rep = conn.execute(
            "SELECT * FROM submitter_reputation WHERE submitter_hash = ?",
            (submitter_hash,),
        ).fetchone()
        if rep is not None and rep["muted_until"]:
            if parse_iso_utc(rep["muted_until"]) > now_dt:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Submitter is temporarily muted",
                )

        if rep is None:
            initial_trust = 1 if user_token_hash else 0
            conn.execute(
                """
                INSERT INTO submitter_reputation (
                    submitter_hash, user_token_hash, trust_level, approved_cnt, rejected_cnt, muted_until
                )
                VALUES (?, ?, ?, 0, 0, NULL)
                """,
                (submitter_hash, user_token_hash, initial_trust),
            )
        elif user_token_hash and int(rep["trust_level"]) < 1:
            conn.execute(
                "UPDATE submitter_reputation SET trust_level = 1 WHERE submitter_hash = ?",
                (submitter_hash,),
            )

        norm_blocks, media_ids, content_type, content_hash = validate_and_normalize_blocks(
            body.blocks,
            conn,
            settings,
        )

        payload_json = json.dumps(
            {"blocks": norm_blocks, "attribution": attribution},
            ensure_ascii=False,
        )
        tags_json = json.dumps(clean_tags, ensure_ascii=False)

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
                0, ?, ?, ?,
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
                source_type,
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

    return SubmissionCreateResponse(submission_id=submission_id, status="pending")
