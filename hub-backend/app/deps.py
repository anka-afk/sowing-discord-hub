from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Query, Request, status

from app.config import Settings
from app.db import Database, utc_now_iso
from app.security import decode_admin_jwt, sha256_hex
from app.storage import StorageBackend


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
    with db.write() as conn:
        row = conn.execute(
            """
            SELECT instance_id, client_version, created_at, is_banned
            FROM bot_instances
            WHERE token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None or int(row["is_banned"]) == 1:
            return None
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
    if not payload or not payload.get("sub"):
        return None
    with db.read() as conn:
        row = conn.execute(
            "SELECT id, username, role, is_active FROM admins WHERE id = ?",
            (payload["sub"],),
        ).fetchone()
        if row is None or int(row["is_active"]) != 1:
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


def require_admin_or_query_token(
    request: Request,
    token: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_db),
) -> AuthenticatedAdmin:
    jwt_token = extract_bearer_token(request) or token
    admin = resolve_admin_from_token(jwt_token, settings, db)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid admin JWT required",
        )
    return admin
