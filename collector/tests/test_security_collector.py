from __future__ import annotations

import asyncio
import io
import json
import socket

import httpx
import pytest
from PIL import Image

from sowing_collector.config import TriggerConfig
from sowing_collector.hub_client import HubCollectorClient
from sowing_collector.media import (
    MediaValidationError,
    _SafePublicNetworkBackend,
    detect_magic_mime,
    download_media,
    validate_media_bytes,
    validate_public_media_url,
)
from sowing_collector.onebot_ws import OneBotWSClient, _redact_ws_url
from sowing_collector.pipeline import _redact_url_for_log
from sowing_collector.triggers import TriggerEngine


def _make_small_png() -> bytes:
    img = Image.new("RGB", (16, 16), (120, 80, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_collector_ssrf_blocks_private_loopback_transition_and_malformed_urls():
    for bad in [
        "file:///etc/passwd",
        "http://127.0.0.1/secret.png",
        "http://127.1/secret.png",
        "http://0177.0.0.1/secret.png",
        "http://2130706433/secret.png",
        "http://0x7f000001/secret.png",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/a.png",
        "http://[::1%25lo]/a.png",
        "http://[::127.0.0.1]/a.png",
        "http://[::ffff:127.0.0.1]/a.png",
        "http://[::ffff:0:127.0.0.1]/a.png",
        "http://[64:ff9b::127.0.0.1]/a.png",
        "http://[64:ff9b:1::8.8.8.8]/a.png",
        "http://[64:ff9b:1:7f00:1::8.8.8.8]/a.png",
        "http://[2002:7f00:1::1]/a.png",
        "http://[2001:0000:4136:e378::1]/a.png",
        "http://localhost/a.png",
        "http://service.internal/a.png",
        "http://127.0.0.1\\@93.184.216.34/a.png",
        "http://user:pass@93.184.216.34/a.png",
        "http://93.184.216.34:0/a.png",
        "http://93.184.216.34/a\nb.png",
        "http://93.184.216.34/a\tb.png",
    ]:
        with pytest.raises(MediaValidationError):
            await validate_public_media_url(bad)


@pytest.mark.asyncio
async def test_safe_public_network_backend_pins_resolved_ip_and_blocks_rebinding(monkeypatch):
    dialed_hosts: list[tuple[str, int]] = []

    class FakeInnerBackend:
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            dialed_hosts.append((host, port))
            return object()

        async def sleep(self, seconds: float):
            await asyncio.sleep(seconds)

    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo_public(host, port, **kwargs):
        assert host == "cdn.example.com"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo_public)
    backend = _SafePublicNetworkBackend(inner=FakeInnerBackend())
    await backend.connect_tcp("cdn.example.com", 443)
    # Must dial the validated IP literal directly (never the raw hostname)
    assert dialed_hosts == [("93.184.216.34", 443)]

    async def fake_getaddrinfo_rebind(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo_rebind)
    with pytest.raises(MediaValidationError) as exc_info:
        await backend.connect_tcp("rebind.example.com", 443)
    assert exc_info.value.code == "ssrf_blocked"


@pytest.mark.asyncio
async def test_download_media_blocks_unsafe_redirects_and_streaming_overflow():
    def redirect_to_loopback(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret.png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect_to_loopback)) as client:
        with pytest.raises(MediaValidationError) as exc_info:
            await download_media("https://93.184.216.34/start.png", client=client)
        assert exc_info.value.code == "ssrf_blocked"

    def redirect_loop(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://93.184.216.34/next.png"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect_loop)) as client:
        with pytest.raises(MediaValidationError) as exc_info:
            await download_media("https://93.184.216.34/start.png", max_redirects=2, client=client)
        assert exc_info.value.code == "redirect_blocked"

    def spoofed_chunked_large(request: httpx.Request) -> httpx.Response:
        # Omit Content-Length to test streaming byte cap
        return httpx.Response(200, content=b"x" * 2048)

    async with httpx.AsyncClient(transport=httpx.MockTransport(spoofed_chunked_large)) as client:
        with pytest.raises(MediaValidationError) as exc_info:
            await download_media("https://93.184.216.34/big.png", max_bytes=1024, client=client)
        assert exc_info.value.code == "download_too_large"


def test_bmp_rejected_and_filename_hint_sanitized():
    assert detect_magic_mime(b"BM" + b"\x00" * 32) is None
    png = _make_small_png()
    validated = validate_media_bytes(png, filename_hint="../../etc/passwd\r\n.png")
    assert validated.mime == "image/png"
    assert validated.filename == f"{validated.sha256[:16]}.png"
    assert "/" not in validated.filename and "\\" not in validated.filename


@pytest.mark.asyncio
async def test_hub_collector_client_rejects_redirects_and_sends_nonce():
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
        return httpx.Response(200, json={"submission_id": "sub_1", "status": "pending"})

    async with httpx.AsyncClient(base_url="https://hub.example", transport=httpx.MockTransport(handler)) as http_client:
        client = HubCollectorClient("https://hub.example", "strong-secret-0123456789", client=http_client)
        await client.ingest_submission({"blocks": []})
        assert "x-collector-nonce" in seen_headers[0]
        assert len(seen_headers[0]["x-collector-nonce"]) == 32

        client_redir = HubCollectorClient(
            "https://hub.example",
            "strong-secret-0123456789",
            client=http_client,
            ingest_endpoint="/redirect",
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client_redir.ingest_submission({"blocks": []})


def test_trigger_engine_fails_closed_on_empty_or_invalid_group_and_bounds_emoji_map():
    engine = TriggerEngine(TriggerConfig(emoji_like_threshold=1), watch_groups=[], max_tracked_messages=100)
    assert (
        engine.process_event(
            {
                "post_type": "notice",
                "notice_type": "essence",
                "sub_type": "add",
                "group_id": 1001,
                "message_id": 1,
            }
        )
        is None
    )
    # Non-numeric group_id must fail closed instead of raising ValueError
    watched = TriggerEngine(TriggerConfig(emoji_like_threshold=1), watch_groups=[1001], max_tracked_messages=100)
    assert (
        watched.process_event(
            {
                "post_type": "notice",
                "notice_type": "essence",
                "sub_type": "add",
                "group_id": "not-a-number",
                "message_id": 1,
            }
        )
        is None
    )

    # Bounded LRU emoji count map
    bounded = TriggerEngine(TriggerConfig(emoji_like_threshold=999), watch_groups=[1001], max_tracked_messages=100)
    for i in range(150):
        bounded.process_event(
            {
                "post_type": "notice",
                "notice_type": "group_msg_emoji_like",
                "group_id": 1001,
                "message_id": i,
                "likes": [{"emoji_id": "1", "count": 1}],
            }
        )
    assert len(bounded._emoji_counts) <= 100


def test_onebot_ws_client_bounds_pending_event_queue():
    client = OneBotWSClient("ws://user:pass@127.0.0.1:3001/?access_token=tok", max_pending_events=2)

    async def noop(ev):
        return None

    for i in range(5):
        client._handle_incoming_payload(json.dumps({"post_type": "message", "id": i}), noop)
    assert client._event_queue.qsize() == 2


def test_redact_urls_strip_userinfo_and_query_tokens():
    redacted_http = _redact_url_for_log("https://user:secret@multimedia.nt.qq.com.cn:8443/download?appid=1407&rkey=SECRET123#frag")
    assert "SECRET123" not in redacted_http
    assert "user" not in redacted_http and "secret" not in redacted_http
    assert redacted_http == "https://multimedia.nt.qq.com.cn:8443/download"

    redacted_ws = _redact_ws_url("ws://admin:pw@127.0.0.1:3001/ws?access_token=TOK999")
    assert "TOK999" not in redacted_ws
    assert "admin" not in redacted_ws and "pw" not in redacted_ws
    assert redacted_ws == "ws://127.0.0.1:3001/ws"
