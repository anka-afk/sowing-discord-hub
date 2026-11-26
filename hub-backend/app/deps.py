from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query, Request, status

from app.config import Settings
from app.db import Database, utc_now_iso
from app.security import (
    decode_admin_jwt,
    parse_iso_utc,
    sha256_hex,
    verify_admin_media_signature,
)
from app.storage import StorageBackend

_LAST_SEEN_UPDATE_INTERVAL = timedelta(minutes=5)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_storage(request: Request) -> StorageBackend:
    return request.app.state.storage


def extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1].strip()
        return token or None
    return None


@dataclass
class AuthenticatedInstance:
    instance_id: str
    client_version: str
    created_at: str


@dataclass
class AuthenticatedAdmin:
    id: str
    username: str
    role: str


def resolve_instance_from_token(
    token: str | None,
    db: Database,
) -> AuthenticatedInstance | None:
    if not token:
        return None
    token_hash = sha256_hex(token)
    with db.read() as conn:
        row = conn.execute(
            """
            SELECT instance_id, client_version, created_at, last_seen_at, is_banned
            FROM bot_instances
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
    if row is None or int(row["is_banned"]) == 1:
        return None

    # Throttle last_seen_at writes to avoid SQLite write-lock contention on every read request
    should_update = True
    last_seen = row["last_seen_at"]
    if last_seen:
        try:
            age = datetime.now(timezone.utc) - parse_iso_utc(last_seen)
            if age < _LAST_SEEN_UPDATE_INTERVAL:
                should_update = False
        except Exception:
            should_update = True

    if should_update:
        with db.write() as conn:
            conn.execute(
                "UPDATE bot_instances SET last_seen_at = ? WHERE instance_id = ?",
                (utc_now_iso(), row["instance_id"]),
            )

    return AuthenticatedInstance(
        instance_id=row["instance_id"],
        client_version=row["client_version"],
        created_at=row["created_at"],
    )


def require_instance(
    request: Request,
    db: Database = Depends(get_db),
) -> AuthenticatedInstance:
    token = extract_bearer_token(request)
    inst = resolve_instance_from_token(token, db)
    if inst is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid Bearer instance_token required",
        )
    return inst


def resolve_admin_from_token(
    token: str | None,
    settings: Settings,
    db: Database,
) -> AuthenticatedAdmin | None:
    if not token:
        return None
    payload = decode_admin_jwt(token, settings)
    if not payload:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    jwt_ver = payload.get("ver")
    if isinstance(jwt_ver, bool) or not isinstance(jwt_ver, int):
        return None
    with db.read() as conn:
        row = conn.execute(
            "SELECT id, username, role, is_active, token_version FROM admins WHERE id = ?",
            (sub,),
        ).fetchone()
        if row is None or int(row["is_active"]) != 1:
            return None
        db_ver = int(row["token_version"])
        if jwt_ver != db_ver:
            return None
        return AuthenticatedAdmin(
            id=row["id"],
            username=row["username"],
            role=row["role"],
        )


def require_admin(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> AuthenticatedAdmin:
    token = extract_bearer_token(request)
    admin = resolve_admin_from_token(token, settings, db)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid admin JWT required",
        )
    return admin


def require_super_admin(
    admin: AuthenticatedAdmin = Depends(require_admin),
) -> AuthenticatedAdmin:
    if admin.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="super_admin role required for this action",
        )
    return admin


def require_admin_media_access(
    media_id: str,
    request: Request,
    exp: str | None = Query(default=None),
    sig: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> None:
    """Authorize GET /api/v1/admin/media/{media_id} via Bearer JWT OR short-lived per-media HMAC signature.

    Full admin JWTs are intentionally NOT accepted via query string (?token=).
    """
    bearer = extract_bearer_token(request)
    if bearer:
        admin = resolve_admin_from_token(bearer, settings, db)
        if admin is not None:
            return
    if verify_admin_media_signature(media_id, exp, sig, settings):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid admin Bearer JWT or signed media URL required",
    )
