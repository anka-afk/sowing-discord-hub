from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


def build_hmac_headers(
    secret: str,
    method: str,
    path: str,
    raw_body: bytes,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Compute X-Collector-Timestamp, optional X-Collector-Nonce & X-Collector-Signature.

    Canonical string:
      - with nonce:    f"{timestamp}.{nonce}.{METHOD}.{path}.{sha256_hex(raw_body)}"
      - without nonce: f"{timestamp}.{METHOD}.{path}.{sha256_hex(raw_body)}"
    """
    ts = str(int(time.time()) if timestamp is None else int(timestamp))
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    if nonce:
        canonical = f"{ts}.{nonce}.{method.upper()}.{path}.{body_sha256}"
    else:
        canonical = f"{ts}.{method.upper()}.{path}.{body_sha256}"
    sig = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    hdrs = {
        "X-Collector-Timestamp": ts,
        "X-Collector-Signature": sig,
    }
    if nonce:
        hdrs["X-Collector-Nonce"] = nonce
    return hdrs


@dataclass
class MediaUploadResponse:
    media_id: str
    mime: str
    w: int
    h: int
    size_bytes: int
    duplicate: bool = False
    match_type: str | None = None
    existing_status: str | None = None
    existing_item_id: str | None = None


class HubCollectorClient:
    """Async HTTP client signing collector requests with HMAC-SHA256."""

    def __init__(
        self,
        base_url: str,
        shared_secret: str,
        *,
        timeout_sec: float = 20.0,
        client: httpx.AsyncClient | None = None,
        media_endpoint: str = "/api/v1/submissions/media",
        ingest_endpoint: str = "/api/v1/internal/collector/ingest",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.shared_secret = shared_secret
        self.media_endpoint = media_endpoint
        self.ingest_endpoint = ingest_endpoint
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_sec,
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HubCollectorClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def _send_signed(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        req = self._client.build_request(
            method=method.upper(),
            url=path,
            content=content,
            files=files,
            headers=headers,
        )
        # Materialize exact wire bytes (critical for multipart/form-data HMAC alignment)
        raw_body = await req.aread()
        url_path = urlparse(str(req.url)).path
        nonce = secrets.token_hex(16)
        sig_headers = build_hmac_headers(
            self.shared_secret,
            method.upper(),
            url_path,
            raw_body,
            nonce=nonce,
        )
        req.headers.update(sig_headers)
        resp = await self._client.send(req, follow_redirects=False)
        if 300 <= resp.status_code < 400:
            raise httpx.HTTPStatusError(
                f"Unexpected redirect ({resp.status_code}) from Hub",
                request=req,
                response=resp,
            )
        resp.raise_for_status()
        return resp

    async def upload_media(
        self,
        data: bytes,
        filename: str,
        mime: str,
    ) -> MediaUploadResponse:
        resp = await self._send_signed(
            "POST",
            self.media_endpoint,
            files={"file": (filename, data, mime)},
        )
        body = resp.json()
        return MediaUploadResponse(
            media_id=str(body.get("media_id") or ""),
            mime=str(body.get("mime", mime)),
            w=int(body.get("w", 0)),
            h=int(body.get("h", 0)),
            size_bytes=int(body.get("size_bytes", len(data))),
            duplicate=bool(body.get("duplicate", False)),
            match_type=body.get("match_type"),
            existing_status=body.get("existing_status"),
            existing_item_id=body.get("existing_item_id"),
        )

    async def ingest_submission(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        resp = await self._send_signed(
            "POST",
            self.ingest_endpoint,
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )
        return resp.json()
