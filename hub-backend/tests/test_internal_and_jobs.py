from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from app.jobs import run_media_cleanup
from tests.conftest import (
    collector_headers,
    login_admin,
    make_pattern_image_bytes,
)


def test_collector_hmac_media_and_ingest(client, test_settings):
    secret = test_settings.collector_hmac_secret

    # 1. Invalid HMAC rejected on ingest
    bad_body = json.dumps({"blocks": [{"type": "text", "text": "hi"}]}).encode("utf-8")
    r_bad = client.post(
        "/api/v1/internal/collector/ingest",
        content=bad_body,
        headers={
            "Content-Type": "application/json",
            "X-Collector-Timestamp": str(int(time.time())),
            "X-Collector-Signature": "0" * 64,
        },
    )
    assert r_bad.status_code == 401

    # 2. Expired timestamp rejected
    old_ts = int(time.time()) - 1000
    r_exp = client.post(
        "/api/v1/internal/collector/ingest",
        content=bad_body,
        headers={
            "Content-Type": "application/json",
            **collector_headers(secret, "POST", "/api/v1/internal/collector/ingest", bad_body, ts=old_ts),
        },
    )
    assert r_exp.status_code == 401

    # 3. Upload media via /api/v1/internal/collector/media with exact multipart body HMAC
    img_bytes = make_pattern_image_bytes(width=300, height=200, seed=77)
    req = httpx.Request(
        "POST",
        "http://testserver/api/v1/internal/collector/media",
        files={"file": ("col.png", img_bytes, "image/png")},
    )
    raw_multipart = req.read()
    hdrs = {
        "Content-Type": req.headers["content-type"],
        **collector_headers(secret, "POST", "/api/v1/internal/collector/media", raw_multipart),
    }
    up = client.post("/api/v1/internal/collector/media", content=raw_multipart, headers=hdrs)
    assert up.status_code == 200, up.text
    media_id = up.json()["media_id"]
    assert media_id.startswith("med_")

    # 4. Ingest into pending queue
    ingest_payload = json.dumps(
        {
            "title": "投稿群精选图",
            "tags": ["沙雕"],
            "source_msg_id": "qq_msg_1001",
            "blocks": [{"type": "image", "media_id": media_id}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    ing_hdrs = {
        "Content-Type": "application/json",
        **collector_headers(secret, "POST", "/api/v1/internal/collector/ingest", ingest_payload),
    }
    ing = client.post("/api/v1/internal/collector/ingest", content=ingest_payload, headers=ing_hdrs)
    assert ing.status_code == 201, ing.text
    ing_data = ing.json()
    assert ing_data["status"] == "pending"
    assert ing_data["duplicate"] is False

    # 5. Re-ingesting exact same payload deduplicates cleanly
    ing2 = client.post("/api/v1/internal/collector/ingest", content=ingest_payload, headers=ing_hdrs)
    assert ing2.status_code == 201
    assert ing2.json()["duplicate"] is True
    assert ing2.json()["submission_id"] == ing_data["submission_id"]


def test_7_day_rejected_media_cleanup_preserves_hashes(client, test_settings):
    admin_token = login_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Upload 2 distinct images and reject both; backdate item 1 to 8 days ago
    img1 = make_pattern_image_bytes(width=250, height=250, seed=101)
    img2 = make_pattern_image_bytes(width=250, height=250, seed=202)

    m1 = client.post(
        "/api/v1/submissions/media",
        headers=admin_headers,
        files={"file": ("old_rej.png", img1, "image/png")},
    ).json()["media_id"]
    m2 = client.post(
        "/api/v1/submissions/media",
        headers=admin_headers,
        files={"file": ("new_rej.png", img2, "image/png")},
    ).json()["media_id"]

    s1 = client.post(
        "/api/v1/submissions",
        headers=admin_headers,
        json={"blocks": [{"type": "image", "media_id": m1}]},
    ).json()["submission_id"]
    s2 = client.post(
        "/api/v1/submissions",
        headers=admin_headers,
        json={"blocks": [{"type": "image", "media_id": m2}]},
    ).json()["submission_id"]

    client.post(
        f"/api/v1/admin/items/{s1}/review",
        headers=admin_headers,
        json={"action": "reject", "reject_reason": "low_quality"},
    )
    client.post(
        f"/api/v1/admin/items/{s2}/review",
        headers=admin_headers,
        json={"action": "reject", "reject_reason": "low_quality"},
    )

    eight_days_ago = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with client.app.state.db.write() as conn:
        conn.execute("UPDATE items SET updated_at = ? WHERE id = ?", (eight_days_ago, s1))

    summary = run_media_cleanup(client.app.state.db, client.app.state.storage, test_settings)
    assert summary["deleted_rejected_media"] == 1

    # Old rejected media blob is gone, but sha256/phash row remains
    with client.app.state.db.read() as conn:
        r1 = conn.execute("SELECT is_deleted, sha256, phash, r2_key FROM media_assets WHERE id = ?", (m1,)).fetchone()
        r2 = conn.execute("SELECT is_deleted, r2_key FROM media_assets WHERE id = ?", (m2,)).fetchone()
    assert int(r1["is_deleted"]) == 1
    assert len(r1["sha256"]) == 64
    assert len(r1["phash"]) > 0
    assert client.app.state.storage.get_bytes(r1["r2_key"]) is None

    # Recent rejected media is still intact
    assert int(r2["is_deleted"]) == 0
    assert client.app.state.storage.get_bytes(r2["r2_key"]) is not None


def test_collector_hmac_also_works_on_submissions_media_endpoint(client, test_settings):
    secret = test_settings.collector_hmac_secret
    img_bytes = make_pattern_image_bytes(width=260, height=200, seed=333)
    req = httpx.Request(
        "POST",
        "http://testserver/api/v1/submissions/media",
        files={"file": ("via_sub.png", img_bytes, "image/png")},
    )
    raw_multipart = req.read()
    hdrs = {
        "Content-Type": req.headers["content-type"],
        **collector_headers(secret, "POST", "/api/v1/submissions/media", raw_multipart),
    }
    resp = client.post("/api/v1/submissions/media", content=raw_multipart, headers=hdrs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["media_id"].startswith("med_")


def test_turnstile_required_when_enabled(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    cfg = Settings(
        database_path=str(tmp_path / "ts.db"),
        storage_backend="local",
        local_storage_dir=str(tmp_path / "media"),
        turnstile_enabled=True,
        turnstile_secret_key="dummy-secret",
    )
    with TestClient(create_app(cfg)) as c:
        r = c.post(
            "/api/v1/submissions",
            json={"blocks": [{"type": "text", "text": "no turnstile token"}]},
        )
        assert r.status_code == 403

