from __future__ import annotations

import json
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.db import Database
from app.ratelimit import SlidingWindowRateLimiter
from app.routers import admin, feed, feedback, instances, internal, meta, submissions
from app.security import CollectorReplayCache, hash_password
from app.storage import create_storage

_COLLECTOR_ONLY_PATHS = frozenset(
    {
        "/api/v1/internal/collector/media",
        "/api/v1/internal/collector/ingest",
    }
)
_COLLECTOR_OPTIONAL_PATHS = frozenset({"/api/v1/submissions/media"})
_SAFE_MEDIA_KEY_RE = re.compile(r"^[A-Za-z0-9/._-]+$")


async def _send_json_error(send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-content-type-options", b"nosniff"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _collect_headers_strict(scope_headers: list[tuple[bytes, bytes]]) -> tuple[dict[bytes, bytes], set[bytes]]:
    """Preserve first-header semantics (matching Starlette) and track duplicate header keys."""
    first: dict[bytes, bytes] = {}
    dups: set[bytes] = set()
    for k, v in scope_headers:
        kl = k.lower()
        if kl in first:
            dups.add(kl)
        else:
            first[kl] = v
    return first, dups


class CollectorRawBodyMiddleware:
    """ASGI middleware that safely buffers raw body bytes for Collector HMAC POST endpoints.

    Enforces mandatory HMAC presence on internal collector routes, rejects duplicate sensitive
    headers, pre-validates timestamp skew, and enforces hard per-route streaming byte caps
    before reading into memory.
    """

    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.max_media_body_bytes = max(
            settings.max_image_bytes,
            settings.max_gif_bytes,
        ) + 2 * 1024 * 1024
        self.max_json_body_bytes = settings.max_json_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        if method != "POST":
            await self.app(scope, receive, send)
            return

        is_collector_only = path in _COLLECTOR_ONLY_PATHS
        is_collector_opt = path in _COLLECTOR_OPTIONAL_PATHS
        if not is_collector_only and not is_collector_opt:
            await self.app(scope, receive, send)
            return

        headers, dup_keys = _collect_headers_strict(scope.get("headers", []))
        has_sig = b"x-collector-signature" in headers
        has_ts = b"x-collector-timestamp" in headers

        if is_collector_only:
            if not has_sig or not has_ts:
                await _send_json_error(send, 401, "Valid collector HMAC headers required")
                return
        else:
            # Optional-HMAC endpoint (/api/v1/submissions/media)
            if not has_sig and not has_ts:
                await self.app(scope, receive, send)
                return
            if not has_sig or not has_ts:
                await _send_json_error(send, 401, "Incomplete collector HMAC headers")
                return

        if dup_keys.intersection({b"x-collector-timestamp", b"x-collector-signature", b"content-length"}):
            await _send_json_error(send, 400, "Duplicate collector or content-length headers not allowed")
            return

        # Pre-validate timestamp format & skew window BEFORE buffering body bytes
        raw_ts = headers.get(b"x-collector-timestamp", b"").decode("ascii", errors="ignore").strip()
        try:
            ts_int = int(raw_ts)
        except ValueError:
            await _send_json_error(send, 401, "Invalid or expired collector HMAC timestamp")
            return
        if abs(int(time.time()) - ts_int) > self.settings.collector_max_skew_seconds:
            await _send_json_error(send, 401, "Invalid or expired collector HMAC timestamp")
            return

        max_body = (
            self.max_json_body_bytes
            if path == "/api/v1/internal/collector/ingest"
            else self.max_media_body_bytes
        )

        # Pre-check Content-Length if declared
        raw_cl = headers.get(b"content-length", b"").decode("ascii", errors="ignore").strip()
        if raw_cl:
            try:
                cl_val = int(raw_cl)
            except ValueError:
                await _send_json_error(send, 400, "Invalid Content-Length header")
                return
            if cl_val < 0:
                await _send_json_error(send, 400, "Invalid negative Content-Length header")
                return
            if cl_val > max_body:
                await _send_json_error(send, 413, "Request body exceeds maximum allowed size")
                return

        chunks: list[bytes] = []
        total_read = 0
        more_body = True
        disconnected = False
        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                body_part = message.get("body", b"")
                if body_part:
                    total_read += len(body_part)
                    if total_read > max_body:
                        await _send_json_error(send, 413, "Request body exceeds maximum allowed size")
                        return
                    chunks.append(body_part)
                more_body = message.get("more_body", False)
            elif message["type"] == "http.disconnect":
                disconnected = True
                more_body = False

        if disconnected:
            return

        raw_body = b"".join(chunks)
        scope["collector_raw_body"] = raw_body
        body_sent = False

        async def replay_receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": raw_body, "more_body": False}
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, send)


class SecurityHeadersMiddleware:
    """Add baseline defensive HTTP security headers to all responses."""

    def __init__(self, app, is_production: bool = False) -> None:
        self.app = app
        self.is_production = is_production

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        is_media = path.startswith("/media/public/") or path.startswith("/api/v1/admin/media/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                defaults: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                ]
                if self.is_production:
                    defaults.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if is_media:
                    defaults.append((b"content-security-policy", b"default-src 'none'; sandbox"))
                    defaults.append((b"content-disposition", b"inline"))
                for k, v in defaults:
                    if k not in existing:
                        headers.append((k, v))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
    cfg.validate_production_security()
    cfg.ensure_dirs()
    db = Database(cfg.database_path)
    storage = create_storage(cfg)
    db.seed_admin(cfg, hash_password)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        db.seed_admin(cfg, hash_password)
        yield

    application = FastAPI(
        title="Sowing Discord Hub API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url=None if cfg.is_production else "/docs",
        redoc_url=None if cfg.is_production else "/redoc",
        openapi_url=None if cfg.is_production else "/openapi.json",
    )
    application.state.settings = cfg
    application.state.db = db
    application.state.storage = storage
    application.state.collector_replay_cache = CollectorReplayCache()
    application.state.rate_limiter = SlidingWindowRateLimiter()

    # Starlette add_middleware is LIFO: last registered is outermost.
    # Runtime stack: SecurityHeadersMiddleware -> CORSMiddleware -> CollectorRawBodyMiddleware -> Router
    application.add_middleware(CollectorRawBodyMiddleware, settings=cfg)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=cfg.cors_origin_list != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )
    application.add_middleware(SecurityHeadersMiddleware, is_production=cfg.is_production)

    application.include_router(meta.router)
    application.include_router(instances.router)
    application.include_router(feed.router)
    application.include_router(submissions.router)
    application.include_router(feedback.router)
    application.include_router(admin.router)
    application.include_router(internal.router)

    @application.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "hub_id": cfg.hub_id}

    @application.get("/media/public/{key:path}", tags=["media"])
    def serve_public_media(key: str) -> Response:
        clean_key = key.lstrip("/")
        if (
            not clean_key
            or "\x00" in clean_key
            or ".." in clean_key
            or "\\" in clean_key
            or not _SAFE_MEDIA_KEY_RE.fullmatch(clean_key)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid media key",
            )
        lower = clean_key.lower()
        if lower.endswith(".webp"):
            mime = "image/webp"
        elif lower.endswith(".gif"):
            mime = "image/gif"
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Media not found",
            )
        storage_key = f"public/{clean_key}"
        try:
            data = storage.get_bytes(storage_key)
        except (ValueError, OSError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid media key",
            )
        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Media not found",
            )
        return Response(
            content=data,
            media_type=mime,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "Content-Disposition": "inline",
            },
        )

    return application


_default_app: FastAPI | None = None


def __getattr__(name: str):
    global _default_app
    if name == "app":
        if _default_app is None:
            _default_app = create_app()
        return _default_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
