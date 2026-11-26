from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings
from app.db import Database
from app.routers import admin, feed, feedback, instances, internal, meta, submissions
from app.security import hash_password
from app.storage import create_storage


class CollectorRawBodyMiddleware:
    """ASGI middleware that buffers raw body bytes for Collector HMAC requests.

    This ensures HMAC verification over raw request bytes works identically for
    both JSON endpoints and multipart/form-data file upload endpoints.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        if b"x-collector-signature" not in headers and b"x-collector-timestamp" not in headers:
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.request":
                body_part = message.get("body", b"")
                if body_part:
                    chunks.append(body_part)
                more_body = message.get("more_body", False)
            elif message["type"] == "http.disconnect":
                more_body = False

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


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or Settings()
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
    )
    application.state.settings = cfg
    application.state.db = db
    application.state.storage = storage

    application.add_middleware(CollectorRawBodyMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=cfg.cors_origin_list != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["ETag"],
    )

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
        if ".." in clean_key or "\\" in clean_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid media key",
            )
        storage_key = f"public/{clean_key}"
        data = storage.get_bytes(storage_key)
        if data is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Media not found",
            )
        if clean_key.lower().endswith(".webp"):
            mime = "image/webp"
        elif clean_key.lower().endswith(".gif"):
            mime = "image/gif"
        else:
            mime = mimetypes.guess_type(clean_key)[0] or "application/octet-stream"
        return Response(
            content=data,
            media_type=mime,
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
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

