from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cli_admin import register_user_token
from app.config import Settings
from app.main import create_app
from app.media import MediaValidationError, _strip_gif_comments
from app.storage import LocalStorageBackend
from tests.conftest import (
    login_admin,
    make_pattern_image_bytes,
    register_bot_instance,
)


def test_x_user_token_cannot_self_escalate_or_bypass_mute(client: TestClient, test_settings: Settings):
    admin_tok = login_admin(client)
    admin_hdrs = {"Authorization": f"Bearer {admin_tok}"}

    # Submit normal item first
    r1 = client.post(
        "/api/v1/submissions",
        json={"title": "normal-first", "blocks": [{"type": "text", "text": "normal text 1"}]},
    )
    assert r1.status_code == 201
    id1 = r1.json()["submission_id"]

    # Unregistered arbitrary X-User-Token must NOT elevate trust_level above normal (FIFO order preserved)
    r2 = client.post(
        "/api/v1/submissions",
        headers={"X-User-Token": "fake-random-self-vip-token"},
        json={"title": "fake-vip", "blocks": [{"type": "text", "text": "fake vip text"}]},
    )
    assert r2.status_code == 201
    id2 = r2.json()["submission_id"]

    q = client.get("/api/v1/admin/queue?status=pending", headers=admin_hdrs).json()
    ids = [it["id"] for it in q["items"]]
    assert ids.index(id1) < ids.index(id2)

    # Mute the underlying IP submitter_hash -> rotating X-User-Token MUST NOT bypass mute!
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with client.app.state.db.write() as conn:
        conn.execute("UPDATE submitter_reputation SET muted_until = ?", (future,))

    register_user_token(client.app.state.db, test_settings, "real-registered-vip-token", trust_level=1)
    r_muted = client.post(
        "/api/v1/submissions",
        headers={"X-User-Token": "real-registered-vip-token"},
        json={"title": "bypass attempt", "blocks": [{"type": "text", "text": "trying to bypass mute"}]},
    )
    assert r_muted.status_code == 403


def test_media_ownership_unattached_cover_url_prefix_and_quota(tmp_path: Path):
    cfg = Settings(
        database_path=str(tmp_path / "own.db"),
        storage_backend="local",
        local_storage_dir=str(tmp_path / "media"),
        public_media_base_url="https://media.example.org",
        storage_limit_bytes=200_000,
        max_images_per_submission=2,
    )
    app = create_app(cfg)
    with TestClient(app) as c:
        inst1 = register_bot_instance(c)
        inst2 = register_bot_instance(c)
        h1 = {"Authorization": f"Bearer {inst1['instance_token']}"}
        h2 = {"Authorization": f"Bearer {inst2['instance_token']}"}

        img1 = make_pattern_image_bytes(seed=401)
        m1 = c.post("/api/v1/submissions/media", headers=h1, files={"file": ("a.png", img1, "image/png")}).json()["media_id"]

        # Another submitter cannot hijack inst1's unattached media_id (403)
        r_hijack = c.post(
            "/api/v1/submissions",
            headers=h2,
            json={"blocks": [{"type": "image", "media_id": m1}]},
        )
        assert r_hijack.status_code == 403

        # Legitimate submitter attaches m1
        r_ok = c.post(
            "/api/v1/submissions",
            headers=h1,
            json={"blocks": [{"type": "image", "media_id": m1}]},
        )
        assert r_ok.status_code == 201

        # Reusing already-attached m1 in another submission is rejected (422)
        r_reuse = c.post(
            "/api/v1/submissions",
            headers=h1,
            json={"title": "reuse", "blocks": [{"type": "text", "text": "extra"}, {"type": "image", "media_id": m1}]},
        )
        assert r_reuse.status_code == 422

        # Prefix-spoofed & path-traversal cover_url MUST be rejected (422)
        for bad_cover in [
            "https://media.example.org.evil.tld/api/v1/admin/media/med_1.webp",
            "https://media.example.org/../../api/v1/admin/media/med_1.webp",
            "https://media.example.org/%2e%2e/api/v1/admin/media/med_1.webp",
            "https://user:pass@media.example.org/m/med_1.webp",
            "https://media.example.org/m/med_1.webp?foo=1",
        ]:
            r_spoof = c.post(
                "/api/v1/submissions",
                headers=h1,
                json={
                    "blocks": [
                        {
                            "type": "video_link",
                            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                            "cover_url": bad_cover,
                        }
                    ]
                },
            )
            assert r_spoof.status_code == 422, f"Expected 422 for {bad_cover}"

        # Collector-uploaded unattached media cannot be hijacked by web/plugin submitter (403)
        import httpx
        from tests.conftest import collector_headers
        img_col = make_pattern_image_bytes(seed=409)
        req_col = httpx.Request("POST", "http://testserver/api/v1/internal/collector/media", files={"file": ("c.png", img_col, "image/png")})
        raw_col = req_col.read()
        col_mid = c.post(
            "/api/v1/internal/collector/media",
            content=raw_col,
            headers={
                "Content-Type": req_col.headers["content-type"],
                **collector_headers(cfg.collector_hmac_secret, "POST", "/api/v1/internal/collector/media", raw_col, nonce="col-own-1"),
            },
        ).json()["media_id"]
        assert c.post("/api/v1/submissions", headers=h1, json={"blocks": [{"type": "image", "media_id": col_mid}]}).status_code == 403

        # Duplicate text content_hash submission returns 409
        r_txt1 = c.post("/api/v1/submissions", headers=h1, json={"blocks": [{"type": "text", "text": "unique meme body"}]})
        assert r_txt1.status_code == 201
        r_txt2 = c.post("/api/v1/submissions", headers=h1, json={"blocks": [{"type": "text", "text": "unique meme body"}]})
        assert r_txt2.status_code == 409


def test_storage_quota_enforced(tmp_path: Path):
    cfg = Settings(
        database_path=str(tmp_path / "quota.db"),
        storage_backend="local",
        local_storage_dir=str(tmp_path / "media"),
        storage_limit_bytes=100,  # tiny 100-byte quota
    )
    app = create_app(cfg)
    with TestClient(app) as c:
        inst = register_bot_instance(c)
        img = make_pattern_image_bytes(seed=501)
        r = c.post(
            "/api/v1/submissions/media",
            headers={"Authorization": f"Bearer {inst['instance_token']}"},
            files={"file": ("a.png", img, "image/png")},
        )
        assert r.status_code == 507


def test_feedback_instance_cap_and_recheck_epoch_prevents_retrigger(client: TestClient):
    admin_tok = login_admin(client)
    admin_hdrs = {"Authorization": f"Bearer {admin_tok}"}

    insts = [register_bot_instance(client) for _ in range(3)]
    # Age all 3 instances > 24h
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with client.app.state.db.write() as conn:
        conn.execute("UPDATE bot_instances SET created_at = ?", (old_ts,))

    # Create & approve an item
    sub = client.post(
        "/api/v1/submissions",
        headers={"Authorization": f"Bearer {insts[0]['instance_token']}"},
        json={"title": "epoch item", "blocks": [{"type": "text", "text": "epoch test body"}]},
    )
    item_id = sub.json()["submission_id"]
    assert client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers=admin_hdrs,
        json={"action": "approve"},
    ).status_code == 200

    # Single instance rotating different group_hashes is capped at feedback_max_group_contribution (3)
    events = [
        {
            "item_id": item_id,
            "action": "like",
            "voter_hash": f"v_{i}",
            "group_hash": f"fake_grp_{i}",
            "occurred_at": "2026-11-26T10:00:00Z",
        }
        for i in range(6)
    ]
    fb = client.post(
        "/api/v1/feedback/batch",
        headers={"Authorization": f"Bearer {insts[0]['instance_token']}"},
        json={"events": events},
    ).json()
    assert fb["accepted"] == 3
    assert fb["capped"] == 3

    # 3 mature instances report -> triggers circuit breaker (pending_recheck)
    for idx, inst in enumerate(insts):
        res = client.post(
            "/api/v1/feedback/batch",
            headers={"Authorization": f"Bearer {inst['instance_token']}"},
            json={
                "events": [
                    {
                        "item_id": item_id,
                        "action": "report",
                        "reason": "other",
                        "voter_hash": f"rep_{idx}",
                        "group_hash": f"rg_{idx}",
                        "occurred_at": "2026-11-26T10:01:00Z",
                    }
                ]
            },
        ).json()
        if idx == 2:
            assert item_id in res["circuit_broken_items"]

    # Admin re-approves pending_recheck item IN THE EXACT SAME SECOND (monotonic review_epoch!)
    assert client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers=admin_hdrs,
        json={"action": "approve"},
    ).status_code == 200

    # Subsequent like MUST NOT re-trigger circuit breaker from old reports!
    fb_after = client.post(
        "/api/v1/feedback/batch",
        headers={"Authorization": f"Bearer {insts[1]['instance_token']}"},
        json={
            "events": [
                {
                    "item_id": item_id,
                    "action": "like",
                    "voter_hash": "after_reapprove",
                    "group_hash": "after_grp",
                    "occurred_at": "2026-11-26T10:05:00Z",
                }
            ]
        },
    ).json()
    assert fb_after["accepted"] == 1
    assert fb_after["circuit_broken_items"] == []

    # Re-approving pending_recheck must NOT double-increment submitter approved_cnt
    with client.app.state.db.read() as conn:
        rep_row = conn.execute(
            "SELECT approved_cnt FROM submitter_reputation sr JOIN items i ON i.submitter_hash = sr.submitter_hash WHERE i.id = ?",
            (item_id,),
        ).fetchone()
        assert int(rep_row["approved_cnt"]) == 1


def test_rate_limiting_login_and_gif_fail_closed_and_safe_path(client: TestClient, tmp_path: Path):
    # Login rate limit triggers 429 on 11th request within 60s
    for i in range(10):
        r = client.post("/api/v1/admin/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
    r11 = client.post("/api/v1/admin/login", json={"username": "admin", "password": "wrong"})
    assert r11.status_code == 429
    assert "retry-after" in r11.headers

    # Malformed GIF & trailing polyglot bytes fail closed instead of returning raw bytes
    with pytest.raises(MediaValidationError):
        _strip_gif_comments(b"GIF89a\x01\x00\x01\x00\x00\x00\x00\xffmalformed")

    from tests.conftest import make_animated_gif_bytes
    valid_gif = make_animated_gif_bytes()
    with pytest.raises(MediaValidationError, match="Trailing data"):
        _strip_gif_comments(valid_gif + b"<script>alert(1)</script>")

    # LocalStorageBackend._safe_path rejects sibling directory prefix escape
    st = LocalStorageBackend(str(tmp_path / "media"), "http://testserver/media/public")
    with pytest.raises(ValueError):
        st._safe_path("../media_sibling/secret.txt")
