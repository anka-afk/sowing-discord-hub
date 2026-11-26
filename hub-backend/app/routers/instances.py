from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.config import Settings
from app.db import Database, utc_now_iso
from app.deps import get_db, get_settings
from app.ratelimit import enforce_rate_limit, get_client_ip
from app.schemas import (
    ChallengeResponse,
    InstanceRegisterRequest,
    InstanceRegisterResponse,
)
from app.security import (
    generate_token,
    iso_after_seconds,
    parse_iso_utc,
    sha256_hex,
    verify_pow,
)
from app.ulid import generate_prefixed_id

router = APIRouter(prefix="/api/v1/instances", tags=["instances"])


@router.get("/challenge", response_model=ChallengeResponse)
def get_challenge(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> ChallengeResponse:
    client_ip = get_client_ip(request)
    enforce_rate_limit(
        request,
        settings,
        scope="instances_challenge",
        identity=f"ip:{client_ip}",
        limit=30,
        window_seconds=60,
    )

    challenge_id = generate_prefixed_id("chl")
    nonce_prefix = secrets.token_hex(8)
    expires_at = iso_after_seconds(settings.pow_ttl_seconds)
    now = utc_now_iso()

    with db.write() as conn:
        # Prune expired/consumed challenges to prevent unbounded table growth
        conn.execute(
            "DELETE FROM pow_challenges WHERE used = 1 OR expires_at < ?",
            (now,),
        )
        conn.execute(
            """
            INSERT INTO pow_challenges (challenge_id, nonce_prefix, difficulty_bits, expires_at, used, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (challenge_id, nonce_prefix, settings.pow_difficulty_bits, expires_at, now),
        )

    return ChallengeResponse(
        challenge_id=challenge_id,
        nonce_prefix=nonce_prefix,
        difficulty_bits=settings.pow_difficulty_bits,
        expires_at=expires_at,
    )


@router.post("/register", response_model=InstanceRegisterResponse)
def register_instance(
    request: Request,
    body: InstanceRegisterRequest,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> InstanceRegisterResponse:
    client_ip = get_client_ip(request)
    enforce_rate_limit(
        request,
        settings,
        scope="instances_register",
        identity=f"ip:{client_ip}",
        limit=15,
        window_seconds=300,
    )

    now = utc_now_iso()
    now_dt = datetime.now(timezone.utc)

    with db.write() as conn:
        row = conn.execute(
            """
            SELECT challenge_id, nonce_prefix, difficulty_bits, expires_at, used
            FROM pow_challenges
            WHERE challenge_id = ?
            """,
            (body.challenge_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or unknown challenge_id",
            )
        if int(row["used"]) == 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Challenge already consumed",
            )
        if parse_iso_utc(row["expires_at"]) < now_dt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Challenge expired",
            )
        if not verify_pow(
            row["nonce_prefix"],
            body.solution,
            int(row["difficulty_bits"]),
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid PoW solution",
            )

        conn.execute(
            "UPDATE pow_challenges SET used = 1 WHERE challenge_id = ?",
            (body.challenge_id,),
        )

        instance_id = generate_prefixed_id("inst")
        instance_token = generate_token(32)
        token_hash = sha256_hex(instance_token)

        conn.execute(
            """
            INSERT INTO bot_instances (instance_id, token_hash, client_version, created_at, last_seen_at, is_banned)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (instance_id, token_hash, body.client_version.strip()[:64], now, now),
        )

    return InstanceRegisterResponse(
        instance_id=instance_id,
        instance_token=instance_token,
    )
