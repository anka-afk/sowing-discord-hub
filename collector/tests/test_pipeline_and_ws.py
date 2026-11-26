from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json

import httpx
import pytest
import websockets
from PIL import Image, ImageDraw

from sowing_collector.config import CollectorConfig, FilterConfig, HubConfig, OneBotConfig, StorageConfig, TriggerConfig
from sowing_collector.dedup import DedupStore
from sowing_collector.hub_client import HubCollectorClient, build_hmac_headers
from sowing_collector.onebot_ws import OneBotWSClient
from sowing_collector.pipeline import CollectorPipeline


def _make_png(variant: int = 0) -> bytes:
    img = Image.new("RGB", (96, 96), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    if variant == 0:
        draw.rectangle([10, 10, 50, 86], fill=(20, 40, 200))
        draw.ellipse([55, 20, 90, 65], fill=(220, 60, 40))
    else:
        for i in range(0, 96, 16):
            draw.line([0, i, 96, 96 - i], fill=(10, 10, 10), width=4)
        draw.polygon([(48, 4), (92, 90), (4, 90)], outline=(30, 150, 30), width=5)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _verify_hmac(secret: str, request: httpx.Request) -> bool:
    ts = request.headers.get("X-Collector-Timestamp")
    sig = request.headers.get("X-Collector-Signature")
    nonce = request.headers.get("X-Collector-Nonce")
    if not ts or not sig:
        return False
    body_sha = hashlib.sha256(request.content).hexdigest()
    if nonce:
        canonical = f"{ts}.{nonce}.{request.method.upper()}.{request.url.path}.{body_sha}"
    else:
        canonical = f"{ts}.{request.method.upper()}.{request.url.path}.{body_sha}"
    expected = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


@pytest.mark.asyncio
async def test_pipeline_hmac_upload_and_ingest_and_dedup():
    secret = "super-secret-hmac-key"
    captured_requests: list[tuple[str, dict]] = []
    media_counter = {"n": 0}
    force_hub_duplicate = {"flag": False}

    def handler(request: httpx.Request) -> httpx.Response:
        assert _verify_hmac(secret, request), f"Invalid HMAC on {request.url.path}"
        if request.url.path == "/api/v1/submissions/media":
            media_counter["n"] += 1
            if force_hub_duplicate["flag"]:
                return httpx.Response(
                    200,
                    json={
                        "media_id": "m-dup",
                        "mime": "image/webp",
                        "w": 96,
                        "h": 96,
                        "size_bytes": 1000,
                        "duplicate": True,
                        "match_type": "exact",
                        "existing_status": "pending",
                        "existing_item_id": None,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "media_id": f"media-{media_counter['n']}",
                    "mime": "image/webp",
                    "w": 96,
                    "h": 96,
                    "size_bytes": 1234,
                    "duplicate": False,
                },
            )
        if request.url.path == "/api/v1/internal/collector/ingest":
            body = json.loads(request.content.decode("utf-8"))
            captured_requests.append((request.url.path, body))
            return httpx.Response(200, json={"submission_id": "sub-001", "status": "pending"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://hub.test", transport=transport) as http_client:
        hub_client = HubCollectorClient("http://hub.test", secret, client=http_client)
        cfg = CollectorConfig(
            hub=HubConfig(base_url="http://hub.test", shared_secret=secret),
            onebot=OneBotConfig(watch_groups=[1001]),
            triggers=TriggerConfig(emoji_like_threshold=2),
            filters=FilterConfig(sensitive_words=["违禁广告"]),
            storage=StorageConfig(db_path=":memory:"),
        )

        img1 = _make_png(0)
        img2 = _make_png(1)
        images_by_url = {
            "https://img.test/1.png": img1,
            "https://img.test/2.png": img2,
        }

        async def fake_download(url: str, max_bytes: int) -> bytes:
            return images_by_url[url]

        async def fake_get_msg(mid: str) -> dict:
            return {
                "message_id": mid,
                "user_id": 666666,
                "message": [
                    {"type": "text", "data": {"text": "群里刚发的神图"}},
                    {"type": "image", "data": {"url": "https://img.test/1.png", "file": "1.png"}},
                ],
            }

        pipeline = CollectorPipeline(
            cfg,
            hub_client,
            DedupStore(":memory:"),
            get_msg=fake_get_msg,
            media_downloader=fake_download,
        )

        # 1. Emoji like reaches threshold -> fetches msg -> uploads image -> ingests
        outcome1 = await pipeline.handle_event(
            {
                "post_type": "notice",
                "notice_type": "group_msg_emoji_like",
                "group_id": 1001,
                "message_id": 500,
                "likes": [{"emoji_id": "128077", "count": 2}],
            }
        )
        assert outcome1.accepted
        assert outcome1.submission_id == "sub-001"
        assert len(captured_requests) == 1
        ingested = captured_requests[0][1]
        assert ingested["blocks"][0] == {"type": "text", "text": "群里刚发的神图"}
        assert ingested["blocks"][1]["type"] == "image"
        assert ingested["blocks"][1]["media_id"] == "media-1"
        assert ingested["source_msg_id"].startswith("msg_")
        assert "1001" not in ingested["source_msg_id"]

        # 2. Same image submitted again in another message -> blocked locally before hitting Hub!
        outcome2 = await pipeline.handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 1001,
                "message_id": 501,
                "user_id": 777777,
                "message": [
                    {"type": "text", "data": {"text": "#投稿 再发一次"}},
                    {"type": "image", "data": {"url": "https://img.test/1.png"}},
                ],
            }
        )
        assert not outcome2.accepted
        assert outcome2.duplicate_early_stop
        assert outcome2.reason.startswith("local_duplicate_media:")
        assert media_counter["n"] == 1  # Never hit Hub

        # 3. Sensitive word rejected locally before media download
        outcome3 = await pipeline.handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 1001,
                "message_id": 502,
                "user_id": 777777,
                "message": [{"type": "text", "data": {"text": "#投稿 这里有违禁广告请过滤"}}],
            }
        )
        assert not outcome3.accepted
        assert outcome3.reason == "sensitive_word:违禁广告"

        # 4. Hub-side duplicate early stop (new local image, Hub says duplicate=True)
        force_hub_duplicate["flag"] = True
        outcome4 = await pipeline.handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 1001,
                "message_id": 503,
                "user_id": 777777,
                "message": [
                    {"type": "text", "data": {"text": "#投稿 新图但后端已存在"}},
                    {"type": "image", "data": {"url": "https://img.test/2.png"}},
                ],
            }
        )
        assert not outcome4.accepted
        assert outcome4.duplicate_early_stop
        assert outcome4.reason == "hub_duplicate_media:exact"
        assert len(captured_requests) == 1  # Ingest NOT called!


@pytest.mark.asyncio
async def test_onebot_ws_client_action_and_event_dispatch():
    received_events: list[dict] = []
    ready_event = asyncio.Event()

    async def mock_onebot_server(ws):
        # Wait for client to send get_msg or push an event right away
        await ws.send(
            json.dumps(
                {
                    "post_type": "notice",
                    "notice_type": "essence",
                    "sub_type": "add",
                    "group_id": 1001,
                    "message_id": 42,
                }
            )
        )
        raw = await ws.recv()
        req = json.loads(raw)
        assert req["action"] == "get_msg"
        await ws.send(
            json.dumps(
                {
                    "status": "ok",
                    "retcode": 0,
                    "echo": req["echo"],
                    "data": {"message_id": 42, "message": [{"type": "text", "data": {"text": "精华内容"}}]},
                }
            )
        )
        await asyncio.sleep(0.1)

    async with websockets.serve(mock_onebot_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        client = OneBotWSClient(f"ws://127.0.0.1:{port}", reconnect_interval_sec=0.1)

        async def on_event(ev: dict):
            received_events.append(ev)
            msg = await client.get_msg(ev["message_id"])
            assert msg["message_id"] == 42
            ready_event.set()
            client.stop()

        runner = asyncio.create_task(client.run_forever(on_event))
        await asyncio.wait_for(ready_event.wait(), timeout=5.0)
        await asyncio.wait_for(runner, timeout=5.0)
        assert len(received_events) == 1


def test_build_hmac_headers_deterministic():
    headers = build_hmac_headers("k", "post", "/api/v1/internal/collector/ingest", b"{}", timestamp=1700000000)
    assert headers["X-Collector-Timestamp"] == "1700000000"
    body_sha = hashlib.sha256(b"{}").hexdigest()
    expected = hmac.new(b"k", f"1700000000.POST./api/v1/internal/collector/ingest.{body_sha}".encode(), hashlib.sha256).hexdigest()
    assert headers["X-Collector-Signature"] == expected


@pytest.mark.asyncio
async def test_pipeline_essence_forward_chat_record_with_inline_image_and_gif():
    secret = "hmac-test-key"
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert _verify_hmac(secret, request)
        if request.url.path == "/api/v1/submissions/media":
            return httpx.Response(
                200,
                json={
                    "media_id": f"m-{len(captured) + 1}",
                    "mime": "image/gif",
                    "w": 64,
                    "h": 64,
                    "size_bytes": 512,
                    "duplicate": False,
                },
            )
        if request.url.path == "/api/v1/internal/collector/ingest":
            body = json.loads(request.content.decode("utf-8"))
            captured.append(body)
            return httpx.Response(200, json={"submission_id": "sub-chat-1", "status": "pending"})
        return httpx.Response(404)

    f1 = Image.new("RGB", (64, 64), (10, 200, 10))
    f2 = Image.new("RGB", (64, 64), (200, 10, 10))
    buf = io.BytesIO()
    f1.save(buf, format="GIF", save_all=True, append_images=[f2], duration=100, loop=0)
    gif_bytes = buf.getvalue()

    async def fake_get_msg(mid: str) -> dict:
        return {
            "message_id": mid,
            "user_id": 11111,
            "message": [{"type": "forward", "data": {"id": "fwd-99"}}],
        }

    async def fake_get_forward(fid: str) -> list[dict]:
        assert fid == "fwd-99"
        return [
            {
                "data": {
                    "user_id": 22222,
                    "nickname": "张三",
                    "content": [
                        {"type": "text", "data": {"text": "看这张动图"}},
                        {"type": "image", "data": {"url": "https://img.test/anim.gif", "file": "anim.gif"}},
                    ],
                }
            },
            {
                "data": {
                    "user_id": 33333,
                    "nickname": "李四",
                    "content": [{"type": "text", "data": {"text": "笑死我了"}}],
                }
            },
        ]

    async def fake_dl(url: str, max_bytes: int) -> bytes:
        return gif_bytes

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://hub.test", transport=transport) as http_client:
        hub_client = HubCollectorClient("http://hub.test", secret, client=http_client)
        cfg = CollectorConfig(
            hub=HubConfig(base_url="http://hub.test", shared_secret=secret),
            onebot=OneBotConfig(watch_groups=[1001]),
            storage=StorageConfig(db_path=":memory:"),
        )
        pipeline = CollectorPipeline(
            cfg,
            hub_client,
            DedupStore(":memory:"),
            get_msg=fake_get_msg,
            get_forward_msg=fake_get_forward,
            media_downloader=fake_dl,
        )

        outcome = await pipeline.handle_event(
            {
                "post_type": "notice",
                "notice_type": "essence",
                "sub_type": "add",
                "group_id": 1001,
                "message_id": 888,
            }
        )
        assert outcome.accepted
        assert outcome.submission_id == "sub-chat-1"
        assert len(captured) == 1
        block = captured[0]["blocks"][0]
        assert block["type"] == "chat_record"
        assert block["messages"][0]["sender_alias"] == "群友A"
        assert block["messages"][1]["sender_alias"] == "群友B"
        # Inline image inside chat_record updated with media_id
        inline_img = block["messages"][0]["content"][1]
        assert inline_img["media_id"] == "m-1"

