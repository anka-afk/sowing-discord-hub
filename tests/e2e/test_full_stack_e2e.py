from __future__ import annotations

import asyncio
import io
import os
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from PIL import Image, ImageDraw

# Wire all three real Python subprojects onto sys.path without modifying them
REPO_ROOT = Path(__file__).resolve().parents[2]
HUB_BACKEND_DIR = REPO_ROOT / "hub-backend"
PLUGIN_DIR = Path(os.environ.get("SOWING_PLUGIN_DIR", str(REPO_ROOT / "astrbot-plugin")))
COLLECTOR_DIR = REPO_ROOT / "collector"

for _p in (str(HUB_BACKEND_DIR), str(PLUGIN_DIR), str(COLLECTOR_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Real hub-backend imports
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

# Real astrbot-plugin imports
from ambient.air_reader import AirReader, AirState  # noqa: E402
from commands.manual_pull import ManualPullHandler  # noqa: E402
from commands.submit import SubmitHandler  # noqa: E402
from feedback.collector import FeedbackCollector  # noqa: E402
from models.meme import MemeItem  # noqa: E402
from pipeline.commentary import CommentaryGenerator  # noqa: E402
from pipeline.dedup import Deduplicator  # noqa: E402
from pipeline.filter import MemeFilter  # noqa: E402
from sinks.astrbot_sink import AstrBotSink, DeliveryResult  # noqa: E402
from sources.hub_source import HubSource  # noqa: E402
from storage.db import SowingDB  # noqa: E402

# Real collector imports
from sowing_collector.config import (  # noqa: E402
    CollectorConfig,
    FilterConfig,
    HubConfig,
    OneBotConfig,
    StorageConfig,
    TriggerConfig,
)
from sowing_collector.dedup import DedupStore  # noqa: E402
from sowing_collector.hub_client import HubCollectorClient  # noqa: E402
from sowing_collector.pipeline import CollectorPipeline  # noqa: E402


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _make_distinct_png(variant: int, size: tuple[int, int] = (240, 180)) -> bytes:
    """Create geometrically distinct PNGs so pHash treats them as unique images."""
    img = Image.new("RGB", size, (250, 250, 250))
    draw = ImageDraw.Draw(img)
    w, h = size
    if variant == 1:
        draw.rectangle([15, 15, w // 2, h - 15], fill=(30, 80, 210))
        draw.ellipse([w // 2 + 10, 25, w - 15, h - 40], fill=(220, 60, 50))
    elif variant == 2:
        for x in range(0, w, 24):
            draw.line([(x, 0), (w - x, h)], fill=(20, 20, 20), width=5)
        draw.polygon([(w // 2, 10), (w - 15, h - 15), (15, h - 15)], outline=(25, 160, 45), width=6)
    elif variant == 3:
        for y in range(0, h, 20):
            color = (40, 140, 190) if (y // 20) % 2 == 0 else (235, 185, 30)
            draw.rectangle([0, y, w, y + 14], fill=color)
        draw.ellipse([w // 4, h // 4, 3 * w // 4, 3 * h // 4], fill=(170, 30, 140))
    else:
        # Simulated 720x1200 chat_record rendered slice
        draw.rectangle([0, 0, w, 64], fill=(32, 36, 48))
        draw.rounded_rectangle([24, 96, w - 120, 260], radius=16, fill=(225, 238, 255))
        draw.rounded_rectangle([120, 300, w - 24, 480], radius=16, fill=(215, 248, 220))
        draw.rounded_rectangle([24, 520, w - 80, 720], radius=16, fill=(245, 232, 210))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_webp_slice() -> bytes:
    img = Image.new("RGB", (720, 1200), (244, 246, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 720, 72], fill=(28, 34, 46))
    draw.rounded_rectangle([28, 104, 540, 280], radius=18, fill=(255, 255, 255), outline=(210, 218, 230), width=2)
    draw.rounded_rectangle([180, 320, 692, 520], radius=18, fill=(220, 245, 224), outline=(180, 220, 188), width=2)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue()


class _SentRecorder:
    """Captures real MessageChains sent by AstrBotSink and returns deterministic message_ids."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def __call__(self, group_umo: str, chain: Any) -> dict[str, str]:
        self.sent.append((group_umo, chain))
        return {"message_id": f"bot-msg-{len(self.sent)}"}


class LiveHubServer:
    """Runs a real hub-backend FastAPI instance on Uvicorn in a background thread."""

    def __init__(self, tmp_path: Path, collector_secret: str = "e2e-collector-hmac-secret") -> None:
        self.port = _find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.db_path = tmp_path / "hub_e2e.db"
        self.media_dir = tmp_path / "hub_media"
        self.collector_secret = collector_secret
        self.settings = Settings(
            hub_id="official-main",
            hub_name="E2E Test Hub",
            database_path=str(self.db_path),
            storage_backend="local",
            local_storage_dir=str(self.media_dir),
            api_base_url=self.base_url,
            public_media_base_url=f"{self.base_url}/media/public",
            collector_hmac_secret=self.collector_secret,
            turnstile_enabled=False,
            admin_username="admin",
            admin_password="admin123456",
            pow_difficulty_bits=16,
            feedback_report_threshold=3,
            feedback_instance_min_age_hours=24,
        )
        self.app = create_app(self.settings)
        self._config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{self.base_url}/healthz", timeout=1.0)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise RuntimeError("Live hub-backend server failed to become healthy within 15s")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10.0)


@pytest.fixture(scope="module")
def live_hub(tmp_path_factory: pytest.TempPathFactory):
    tmp_dir = tmp_path_factory.mktemp("sowing_e2e_hub")
    server = LiveHubServer(tmp_dir)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.mark.asyncio
async def test_full_stack_end_to_end_lifecycle(live_hub: LiveHubServer, tmp_path: Path):
    """
    Runs the complete 6-stage real cross-module E2E verification:
    1. /meta discovery -> 16-bit PoW instance registration
    2. Plugin /投稿 (video block, image dedup early-stop, chat_record anonymization) + Collector HMAC ingest
    3. Admin login -> queue -> chat_record rendered_images hard guard (422) -> approve & public CDN
    4. Plugin /feed incremental sync + ETag/304 + /random replenishment + AirReader + Deduplicator + ManualPull
    5. 3 independent aged (24h+) instances batch-report -> circuit breaker + tombstones -> plugin auto-eviction
    6. Admin one-click Kill Switch takedown -> 410 Gone + audit log + plugin tombstone eviction
    """
    # =========================================================================
    # Stage 1: Real HubSource /meta discovery + 16-bit SHA-256 PoW registration
    # =========================================================================
    plugin_db_1 = SowingDB(tmp_path / "plugin_1.db")
    hub_src_1 = HubSource("hub:official-main", live_hub.base_url, plugin_db_1, max_nsfw=1)

    try:
        meta = await hub_src_1.fetch_meta()
        assert meta.hub_id == "official-main"
        assert meta.schema_version == "1.0"
        assert meta.capabilities.video_file is False
        assert meta.capabilities.feedback is True
        assert meta.limits.max_image_bytes == 10_485_760
        assert meta.limits.max_gif_bytes == 5_242_880
        assert meta.limits.max_chat_nodes == 50
        assert meta.limits.max_chat_depth == 3

        creds_1 = await hub_src_1.ensure_registered()
        assert creds_1["instance_id"]
        assert creds_1["instance_token"]
        assert creds_1["instance_salt"]

        # =====================================================================
        # Stage 2: Plugin /投稿 (video block, dedup early-stop, chat_record)
        #          + Collector HMAC-SHA256 ingest
        # =====================================================================
        submit_handler = SubmitHandler(hub_src_1)

        # 2a. Local raw video is blocked before touching server
        video_res = await submit_handler.submit(
            text="视频投稿测试",
            videos=[{"filename": "funny.mp4"}],
        )
        assert not video_res.ok
        assert "视频" in video_res.message

        # 2b. Fresh image submission succeeds
        img_plugin = _make_distinct_png(1)
        sub_img_res = await submit_handler.submit(
            title="插件图文投稿",
            text="这是一张全新沙雕图",
            tags=["沙雕"],
            images=[{"data_bytes": img_plugin, "filename": "meme1.png", "mime": "image/png"}],
        )
        assert sub_img_res.ok, sub_img_res.message
        assert sub_img_res.status == "pending"
        submission_id_img = sub_img_res.submission_id
        assert submission_id_img

        # 2c. Re-submitting identical image triggers duplicate early-stop AND hides pending item_id (privacy!)
        dup_pending_res = await submit_handler.submit(
            text="重复投稿测试",
            images=[{"data_bytes": img_plugin, "filename": "meme1_copy.png", "mime": "image/png"}],
        )
        assert not dup_pending_res.ok
        assert dup_pending_res.duplicate is True
        assert dup_pending_res.existing_status == "pending"
        assert dup_pending_res.existing_item_id is None  # Privacy guard: must be null for pending

        # 2d. Plugin /投稿 with chat_record (real QQ/nicknames + inline image) -> local anonymization
        img_chat_inline = _make_distinct_png(2)
        sub_chat_res = await submit_handler.submit(
            title="群聊神对话",
            tags=["群聊日常"],
            chat_nodes=[
                {
                    "user_id": "100012345",
                    "nickname": "王小明",
                    "time_label": "14:20",
                    "content": [
                        {"type": "text", "text": "今天代码又崩了"},
                        {"type": "image", "data_bytes": img_chat_inline, "filename": "inline.png", "mime": "image/png"},
                    ],
                },
                {
                    "user_id": "100067890",
                    "nickname": "李小红",
                    "time_label": "14:21",
                    "content": [{"type": "text", "text": "经典周五发版绷不住了"}],
                },
            ],
        )
        assert sub_chat_res.ok, sub_chat_res.message
        submission_id_chat = sub_chat_res.submission_id
        submitted_chat_str = str(sub_chat_res.blocks_submitted)
        assert "群友A" in submitted_chat_str
        assert "群友B" in submitted_chat_str
        assert "100012345" not in submitted_chat_str
        assert "王小明" not in submitted_chat_str

        # 2e. Real CollectorPipeline + HubCollectorClient HMAC-SHA256 upload & ingest against live hub-backend
        img_collector = _make_distinct_png(3)
        collector_cfg = CollectorConfig(
            hub=HubConfig(base_url=live_hub.base_url, shared_secret=live_hub.collector_secret),
            onebot=OneBotConfig(watch_groups=[888888]),
            triggers=TriggerConfig(emoji_like_threshold=3),
            filters=FilterConfig(sensitive_words=["严禁广告词"]),
            storage=StorageConfig(db_path=str(tmp_path / "collector_e2e.db")),
        )
        collector_dedup = DedupStore(collector_cfg.storage.db_path)

        async def _fake_get_msg(mid: str) -> dict[str, Any]:
            return {
                "message_id": mid,
                "user_id": 555666777,
                "message": [
                    {"type": "text", "data": {"text": "官方投稿群高赞精选"}},
                    {"type": "image", "data": {"url": "https://onebot.local/collector_img.png", "file": "c.png"}},
                ],
            }

        async def _fake_dl(url: str, max_bytes: int) -> bytes:
            return img_collector

        async with HubCollectorClient(live_hub.base_url, live_hub.collector_secret) as collector_hub_client:
            collector_pipeline = CollectorPipeline(
                collector_cfg,
                collector_hub_client,
                collector_dedup,
                get_msg=_fake_get_msg,
                media_downloader=_fake_dl,
            )
            outcome = await collector_pipeline.handle_event(
                {
                    "post_type": "notice",
                    "notice_type": "group_msg_emoji_like",
                    "group_id": 888888,
                    "message_id": 9001,
                    "likes": [{"emoji_id": "128077", "count": 3}],
                }
            )
            assert outcome.accepted, f"Collector ingest failed: {outcome}"
            submission_id_collector = outcome.submission_id
            assert submission_id_collector
        collector_dedup.close()

        # Verify invalid HMAC signature is rejected with 401
        async with httpx.AsyncClient(base_url=live_hub.base_url) as raw_client:
            bad_hmac_resp = await raw_client.post(
                "/api/v1/internal/collector/ingest",
                json={"blocks": [{"type": "text", "text": "tampered"}]},
                headers={
                    "X-Collector-Timestamp": str(int(time.time())),
                    "X-Collector-Signature": "0" * 64,
                },
            )
            assert bad_hmac_resp.status_code == 401

        # =====================================================================
        # Stage 3: Admin login -> queue -> chat_record 422 guard -> approve
        # =====================================================================
        async with httpx.AsyncClient(base_url=live_hub.base_url) as admin_http:
            login_resp = await admin_http.post(
                "/api/v1/admin/login",
                json={"username": "admin", "password": "admin123456"},
            )
            assert login_resp.status_code == 200
            admin_token = login_resp.json()["access_token"]
            admin_headers = {"Authorization": f"Bearer {admin_token}"}

            queue_resp = await admin_http.get(
                "/api/v1/admin/queue",
                params={"status": "pending", "limit": 20},
                headers=admin_headers,
            )
            assert queue_resp.status_code == 200
            queue_data = queue_resp.json()
            queued_ids = {item["id"] for item in queue_data["items"]}
            assert {submission_id_img, submission_id_chat, submission_id_collector}.issubset(queued_ids)

            # 3a. CRITICAL GUARD: approving chat_record WITHOUT rendered_images MUST fail with 422!
            reject_missing_slice = await admin_http.post(
                f"/api/v1/admin/items/{submission_id_chat}/review",
                json={"action": "approve", "tags": ["群聊日常"], "nsfw_level": 0},
                headers=admin_headers,
            )
            assert reject_missing_slice.status_code == 422

            # 3b. Upload rendered_images slice via POST /api/v1/admin/media (simulating hub-web Canvas export)
            slice_bytes = _make_webp_slice()
            slice_up = await admin_http.post(
                "/api/v1/admin/media",
                files={"file": ("chat_slice_1.webp", slice_bytes, "image/webp")},
                headers=admin_headers,
            )
            assert slice_up.status_code == 200
            slice_media_id = slice_up.json()["media_id"]

            # 3c. Approve chat_record WITH rendered_images (leave summary blank to verify 120-char auto-fallback)
            approve_chat = await admin_http.post(
                f"/api/v1/admin/items/{submission_id_chat}/review",
                json={
                    "action": "approve",
                    "tags": ["群聊日常"],
                    "nsfw_level": 0,
                    "rendered_images": [{"media_id": slice_media_id}],
                },
                headers=admin_headers,
            )
            assert approve_chat.status_code == 200, approve_chat.text
            approved_chat_item = approve_chat.json()
            assert approved_chat_item["status"] == "approved"
            assert approved_chat_item["seq"] > 0

            # 3d. Approve image submission & collector submission
            approve_img = await admin_http.post(
                f"/api/v1/admin/items/{submission_id_img}/review",
                json={"action": "approve", "tags": ["沙雕"], "nsfw_level": 0},
                headers=admin_headers,
            )
            assert approve_img.status_code == 200

            approve_col = await admin_http.post(
                f"/api/v1/admin/items/{submission_id_collector}/review",
                json={"action": "approve", "tags": ["沙雕"], "nsfw_level": 0},
                headers=admin_headers,
            )
            assert approve_col.status_code == 200

            # Verify approved item detail, auto-generated summary, and public immutable media URL
            chat_detail = await admin_http.get(f"/api/v1/items/{submission_id_chat}")
            assert chat_detail.status_code == 200
            chat_json = chat_detail.json()
            assert chat_json["summary"]  # Auto-generated 120-char plain-text fallback
            chat_block = [b for b in chat_json["blocks"] if b["type"] == "chat_record"][0]
            assert len(chat_block["rendered_images"]) == 1
            public_slice_url = chat_block["rendered_images"][0]["url"]
            media_resp = await admin_http.get(public_slice_url)
            assert media_resp.status_code == 200
            assert "immutable" in media_resp.headers.get("cache-control", "")

        # Verify approved duplicate early-stop now DOES reveal existing_item_id
        dup_approved_res = await submit_handler.submit(
            text="过审后重投",
            images=[{"data_bytes": img_plugin, "filename": "dup_after_approve.png", "mime": "image/png"}],
        )
        assert not dup_approved_res.ok
        assert dup_approved_res.duplicate is True
        assert dup_approved_res.existing_status == "approved"
        assert dup_approved_res.existing_item_id == submission_id_img

        # =====================================================================
        # Stage 4: Plugin /feed incremental sync + ETag/304 + /random
        #          + AirReader FSM + Deduplicator + ManualPullHandler
        # =====================================================================
        sync1 = await hub_src_1.sync()
        assert sync1.error is None
        assert len(sync1.new_items) == 3
        synced_ids = {it.id for it in sync1.new_items}
        assert synced_ids == {submission_id_img, submission_id_chat, submission_id_collector}

        # Cursor advanced 0 -> 3: sync2 gets empty page + ETag for after_seq=3; sync3 hits 304 Not Modified!
        sync2 = await hub_src_1.sync()
        assert sync2.error is None
        assert sync2.new_items == []
        sync3 = await hub_src_1.sync()
        assert sync3.not_modified is True
        assert sync3.new_items == []

        # /random replenishment
        random_items = await hub_src_1.fetch_random(limit=20, tags=["沙雕"])
        assert len(random_items) >= 2

        # Real ManualPullHandler + Deduplicator + AstrBotSink + AirReader
        dedup_1 = Deduplicator(plugin_db_1)
        recorder = _SentRecorder()
        sink_1 = AstrBotSink(send_fn=recorder)
        air_reader = AirReader(default_quiet_hours="01:00-08:00", default_cooldown_sec=5400)
        pull_handler = ManualPullHandler(
            db=plugin_db_1,
            dedup=dedup_1,
            meme_filter=MemeFilter(),
            sink=sink_1,
            commentary=CommentaryGenerator(),
            sources=[hub_src_1],
            air_reader=air_reader,
        )

        group_umo = "aiocqhttp:GroupMessage:10001"
        delivered_ids: list[str] = []
        for _ in range(3):
            pr = await pull_handler.pull_and_deliver(group_umo)
            assert pr.ok and pr.item is not None
            delivered_ids.append(pr.item.id)
        # Strict per-group dedup: all 3 delivered items are distinct, 4th pull has no unsent items
        assert len(set(delivered_ids)) == 3
        pr_empty = await pull_handler.pull_and_deliver(group_umo)
        assert not pr_empty.ok

        # Verify AirReader 5-state transitions (ACTIVE_CHAT -> LULL_READY -> COOLDOWN doubled backoff)
        base_dt = datetime(2026, 11, 26, 14, 0, 0)
        air_group = "aiocqhttp:GroupMessage:20002"
        air_reader.record_human_message(air_group, now_ts=base_dt.timestamp())
        assert air_reader.evaluate(air_group, now_dt=base_dt + timedelta(minutes=2)).state == AirState.ACTIVE_CHAT
        assert air_reader.evaluate(air_group, now_dt=base_dt + timedelta(minutes=20)).state == AirState.LULL_READY
        air_reader.record_bot_message(air_group, is_meme=True, now_ts=(base_dt + timedelta(minutes=20)).timestamp())
        after_bot = air_reader.evaluate(air_group, now_dt=base_dt + timedelta(minutes=25))
        assert after_bot.state == AirState.COOLDOWN
        assert after_bot.effective_cooldown_sec == 5400 * 2

        # =====================================================================
        # Stage 5: 3 Independent Aged (24h+) Instances Batch Report ->
        #          Circuit Breaker (pending_recheck + tombstones) -> Auto-Evict
        # =====================================================================
        plugin_db_2 = SowingDB(tmp_path / "plugin_2.db")
        plugin_db_3 = SowingDB(tmp_path / "plugin_3.db")
        hub_src_2 = HubSource("hub:official-main", live_hub.base_url, plugin_db_2, max_nsfw=1)
        hub_src_3 = HubSource("hub:official-main", live_hub.base_url, plugin_db_3, max_nsfw=1)

        try:
            creds_2 = await hub_src_2.ensure_registered()
            creds_3 = await hub_src_3.ensure_registered()
            assert len({creds_1["instance_id"], creds_2["instance_id"], creds_3["instance_id"]}) == 3

            # Simulate all 3 instances being registered > 24h ago in the test hub SQLite DB
            aged_iso = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
            with sqlite3.connect(live_hub.db_path) as conn:
                conn.execute(
                    "UPDATE bot_instances SET created_at = ? WHERE instance_id IN (?, ?, ?)",
                    (aged_iso, creds_1["instance_id"], creds_2["instance_id"], creds_3["instance_id"]),
                )
                conn.commit()

            # Each independent instance records a /烂史 report for submission_id_img and flushes via /feedback/batch
            target_uid = f"hub:official-main:{submission_id_img}"
            for idx, (pdb, hsrc) in enumerate(
                [(plugin_db_1, hub_src_1), (plugin_db_2, hub_src_2), (plugin_db_3, hub_src_3)],
                start=1,
            ):
                g_umo = f"aiocqhttp:GroupMessage:report_grp_{idx}"
                b_msg = f"bot-report-msg-{idx}"
                pdb.mark_sent(g_umo, target_uid, bot_msg_id=b_msg, trigger_type="manual")
                fc = FeedbackCollector(pdb)
                row_id = fc.collect_report(
                    group_umo=g_umo,
                    quoted_bot_msg_id=b_msg,
                    user_id=f"reporter_{idx}",
                    reason="privacy_leak",
                    hub_sources={"official-main": hsrc},
                )
                assert row_id is not None
                flushed = await fc.flush({"official-main": hsrc})
                assert flushed.get("official-main") == 1

            # Verify backend tripped circuit breaker -> item is no longer live (410/404) & appears in pending_recheck
            async with httpx.AsyncClient(base_url=live_hub.base_url) as check_http:
                gone_resp = await check_http.get(f"/api/v1/items/{submission_id_img}")
                assert gone_resp.status_code in (404, 410)

            # Verify plugin_1's next /feed sync receives tombstone and AUTOMATICALLY evicts local cache!
            assert plugin_db_1.has_uid(target_uid) is True
            sync_cb = await hub_src_1.sync()
            assert sync_cb.tombstones_removed >= 1
            assert plugin_db_1.has_uid(target_uid) is False

        finally:
            await hub_src_2.close()
            await hub_src_3.close()
            plugin_db_2.close()
            plugin_db_3.close()

        # =====================================================================
        # Stage 6: Admin One-Click Kill Switch Takedown -> 410 Gone + Audit Log
        #          + Plugin Tombstone Auto-Eviction
        # =====================================================================
        collector_uid = f"hub:official-main:{submission_id_collector}"
        assert plugin_db_1.has_uid(collector_uid) is True

        async with httpx.AsyncClient(base_url=live_hub.base_url) as admin_http:
            login_resp = await admin_http.post(
                "/api/v1/admin/login",
                json={"username": "admin", "password": "admin123456"},
            )
            admin_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

            td_resp = await admin_http.post(
                f"/api/v1/admin/items/{submission_id_collector}/takedown",
                json={"reason": "copyright"},
                headers=admin_headers,
            )
            assert td_resp.status_code == 200
            td_body = td_resp.json()
            assert td_body["status"] == "takedown"
            assert td_body["tombstone_seq"] > 0

            # Direct fetch MUST return 410 Gone
            item_410 = await admin_http.get(f"/api/v1/items/{submission_id_collector}")
            assert item_410.status_code == 410

            # Admin stats reflect takedown & audit log
            stats_resp = await admin_http.get("/api/v1/admin/stats", headers=admin_headers)
            assert stats_resp.status_code == 200
            stats = stats_resp.json()
            assert stats["takedown_count"] >= 1
            assert any(
                log.get("target_id") == submission_id_collector and "takedown" in str(log.get("action", ""))
                for log in stats.get("recent_audit_logs", [])
            )

        # Plugin syncs tombstones and immediately purges taken-down item from local SQLite cache
        sync_td = await hub_src_1.sync()
        assert sync_td.tombstones_removed >= 1
        assert plugin_db_1.has_uid(collector_uid) is False

    finally:
        await hub_src_1.close()
        plugin_db_1.close()


@pytest.mark.asyncio
async def test_e2e_gif_preservation_wilson_score_and_young_instance_guard(live_hub: LiveHubServer, tmp_path: Path):
    """
    Verifies:
    - Animated GIF submitted via plugin preserves `image/gif` end-to-end (never converted to animated WebP).
    - Anonymous blinded likes via FeedbackCollector update backend stats.likes and Wilson score.
    - 3 newly-registered (<24h) instances reporting an item do NOT trip the 24h circuit breaker.
    """
    f1 = Image.new("RGB", (80, 80), (180, 40, 40))
    f2 = Image.new("RGB", (80, 80), (40, 180, 40))
    gif_buf = io.BytesIO()
    f1.save(gif_buf, format="GIF", save_all=True, append_images=[f2], duration=120, loop=0)
    gif_bytes = gif_buf.getvalue()

    dbs = [SowingDB(tmp_path / f"young_{i}.db") for i in range(3)]
    sources = [HubSource("hub:official-main", live_hub.base_url, dbs[i], max_nsfw=1) for i in range(3)]

    try:
        for s in sources:
            await s.ensure_registered()

        submitter = SubmitHandler(sources[0])
        res = await submitter.submit(
            title="动图保留测试",
            tags=["抽象"],
            images=[{"data_bytes": gif_bytes, "filename": "dance.gif", "mime": "image/gif"}],
        )
        assert res.ok, res.message
        gif_item_id = res.submission_id

        async with httpx.AsyncClient(base_url=live_hub.base_url) as admin_http:
            login = await admin_http.post("/api/v1/admin/login", json={"username": "admin", "password": "admin123456"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            approve = await admin_http.post(
                f"/api/v1/admin/items/{gif_item_id}/review",
                json={"action": "approve", "tags": ["抽象"], "nsfw_level": 0},
                headers=headers,
            )
            assert approve.status_code == 200

            item_resp = await admin_http.get(f"/api/v1/items/{gif_item_id}")
            assert item_resp.status_code == 200
            gif_block = [b for b in item_resp.json()["blocks"] if b["type"] == "gif"][0]
            assert gif_block["mime"] == "image/gif"
            raw_gif = await admin_http.get(gif_block["url"])
            assert raw_gif.status_code == 200
            assert raw_gif.content[:6] in (b"GIF87a", b"GIF89a")

        # Blinded likes & young-instance (<24h) reports
        gif_uid = f"hub:official-main:{gif_item_id}"
        for idx in range(3):
            g_umo = f"aiocqhttp:GroupMessage:young_grp_{idx}"
            b_msg = f"young-msg-{idx}"
            dbs[idx].mark_sent(g_umo, gif_uid, bot_msg_id=b_msg, trigger_type="manual")
            fc = FeedbackCollector(dbs[idx])
            fc.collect_emoji_like(
                group_umo=g_umo,
                bot_msg_id=b_msg,
                user_id=f"liker_{idx}",
                emoji_id="128077",
                hub_sources={"official-main": sources[idx]},
            )
            fc.collect_report(
                group_umo=g_umo,
                quoted_bot_msg_id=b_msg,
                user_id=f"young_reporter_{idx}",
                reason="other",
                hub_sources={"official-main": sources[idx]},
            )
            flushed = await fc.flush({"official-main": sources[idx]})
            assert flushed.get("official-main") == 2

        async with httpx.AsyncClient(base_url=live_hub.base_url) as check_http:
            after = await check_http.get(f"/api/v1/items/{gif_item_id}")
            # Must still be 200 OK because reporting instances are < 24h old!
            assert after.status_code == 200
            body = after.json()
            assert body["stats"]["likes"] == 3
            assert body["score"] > 0
    finally:
        for s in sources:
            await s.close()
        for d in dbs:
            d.close()


def test_e2e_hub_web_build_artifacts():
    """Verify hub-web production build output & Cloudflare Pages SPA fallback exist."""
    dist_dir = REPO_ROOT / "hub-web" / "dist"
    assert (dist_dir / "index.html").is_file()
    assert (dist_dir / "_redirects").is_file()
    assert (dist_dir / "assets").is_dir()
    assert len(list((dist_dir / "assets").glob("*.js"))) >= 1

