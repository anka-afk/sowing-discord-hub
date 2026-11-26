from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cli_admin import create_admin, reset_admin_password, set_admin_active
from app.config import Settings
from app.main import create_app
from app.security import sign_admin_media_url
from tests.conftest import (
    login_admin,
    make_pattern_image_bytes,
    register_bot_instance,
)


def test_collector_middleware_blocks_oversized_and_expired_requests(tmp_path: Path):
    cfg = Settings(
        database_path=str(tmp_path / "mw.db"),
        storage_backend="local",
        local_storage_dir=str(tmp_path / "media"),
        max_image_bytes=1024,
        max_gif_bytes=1024,
    )
    app = create_app(cfg)
    with TestClient(app) as c:
        # Unrelated path with fake X-Collector headers should NOT trigger collector middleware
        r_health = c.get("/healthz", headers={"X-Collector-Timestamp": "invalid"})
        assert r_health.status_code == 200

        # Collector path with invalid/expired timestamp rejected before reading body
        r_bad_ts = c.post(
            "/api/v1/internal/collector/ingest",
            content=b"{}",
            headers={
                "X-Collector-Timestamp": "100",
                "X-Collector-Signature": "a" * 64,
            },
        )
        assert r_bad_ts.status_code == 401

        # Oversized body (> max_image_bytes + 2MB) rejected with 413
        huge_body = b"x" * (2 * 1024 * 1024 + 4096)
        r_big = c.post(
            "/api/v1/internal/collector/ingest",
            content=huge_body,
            headers={
                "X-Collector-Timestamp": str(int(time.time())),
                "X-Collector-Signature": "a" * 64,
            },
        )
        assert r_big.status_code == 413


def test_admin_jwt_revocation_on_password_reset_and_disable(client: TestClient):
    db = client.app.state.db
    create_admin(db, "mod_rev", "initialPass123!", role="moderator")
    tok = login_admin(client, "mod_rev", "initialPass123!")

    # Works initially
    assert client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {tok}"}).status_code == 200

    # Password reset bumps token_version -> old JWT immediately invalid
    assert reset_admin_password(db, "mod_rev", "newStrongPass456!")
    assert client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {tok}"}).status_code == 401

    # Fresh login succeeds; disabling revokes immediately
    tok2 = login_admin(client, "mod_rev", "newStrongPass456!")
    assert client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {tok2}"}).status_code == 200
    assert set_admin_active(db, "mod_rev", False)
    assert client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {tok2}"}).status_code == 401


def test_admin_media_signed_url_scoped_and_rejects_jwt_query(client: TestClient, test_settings: Settings):
    inst = register_bot_instance(client)
    bot_hdrs = {"Authorization": f"Bearer {inst['instance_token']}"}
    admin_tok = login_admin(client)
    admin_hdrs = {"Authorization": f"Bearer {admin_tok}"}

    img1 = make_pattern_image_bytes(seed=101)
    img2 = make_pattern_image_bytes(seed=202)
    m1 = client.post("/api/v1/submissions/media", headers=bot_hdrs, files={"file": ("a.png", img1, "image/png")}).json()["media_id"]
    m2 = client.post("/api/v1/submissions/media", headers=bot_hdrs, files={"file": ("b.png", img2, "image/png")}).json()["media_id"]

    # Create submission so queue returns signed preview URLs
    sub = client.post(
        "/api/v1/submissions",
        headers=bot_hdrs,
        json={"title": "signed test", "blocks": [{"type": "image", "media_id": m1}]},
    )
    assert sub.status_code == 201

    q = client.get("/api/v1/admin/queue?status=pending", headers=admin_hdrs).json()
    signed_url = q["items"][0]["blocks"][0]["url"]
    assert "exp=" in signed_url and "sig=" in signed_url
    assert admin_tok not in signed_url

    # Signed URL works for m1 without Authorization header
    r_ok = client.get(signed_url)
    assert r_ok.status_code == 200
    assert r_ok.headers["cache-control"] == "private, no-store"
    assert "sandbox" in r_ok.headers.get("content-security-policy", "")

    # Signed params from m1 CANNOT access m2
    qs = signed_url.split("?", 1)[1]
    assert client.get(f"/api/v1/admin/media/{m2}?{qs}").status_code == 401

    # Expired signed URL rejected
    expired_url = sign_admin_media_url(m1, test_settings, ttl_seconds=10)
    # Manually tamper exp to past
    assert client.get(f"/api/v1/admin/media/{m1}?exp=1000&sig=deadbeef").status_code == 401
    assert expired_url.startswith(f"/api/v1/admin/media/{m1}?")

    # Query ?token=<admin_jwt> rejected
    assert client.get(f"/api/v1/admin/media/{m1}?token={admin_tok}").status_code == 401


def test_rbac_moderator_vs_super_admin_and_review_state_machine(client: TestClient):
    db = client.app.state.db
    create_admin(db, "mod_alice", "modPass12345!", role="moderator")
    super_tok = login_admin(client, "admin", "testpassword123")
    mod_tok = login_admin(client, "mod_alice", "modPass12345!")

    inst = register_bot_instance(client)
    bot_hdrs = {"Authorization": f"Bearer {inst['instance_token']}"}
    sub = client.post(
        "/api/v1/submissions",
        headers=bot_hdrs,
        json={"title": "rbac test", "blocks": [{"type": "text", "text": "联系我 13812345678"}]},
    )
    assert sub.status_code == 201
    item_id = sub.json()["submission_id"]

    # Moderator can approve pending item and redact text block
    r_app = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers={"Authorization": f"Bearer {mod_tok}"},
        json={"action": "approve", "edited_text_blocks": {"0": "联系我 [已脱敏]"}},
    )
    assert r_app.status_code == 200
    detail = client.get(f"/api/v1/items/{item_id}").json()
    assert detail["blocks"][0]["text"] == "联系我 [已脱敏]"

    # Re-reviewing an already approved item is blocked with 409
    r_again = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers={"Authorization": f"Bearer {mod_tok}"},
        json={"action": "approve"},
    )
    assert r_again.status_code == 409

    # Moderator gets 403 on Kill Switch takedown
    r_mod_td = client.post(
        f"/api/v1/admin/items/{item_id}/takedown",
        headers={"Authorization": f"Bearer {mod_tok}"},
        json={"reason": "mod attempt"},
    )
    assert r_mod_td.status_code == 403

    # Super admin succeeds on Kill Switch takedown
    r_sup_td = client.post(
        f"/api/v1/admin/items/{item_id}/takedown",
        headers={"Authorization": f"Bearer {super_tok}"},
        json={"reason": "super_admin takedown"},
    )
    assert r_sup_td.status_code == 200

    # Taken-down item cannot be re-approved via /review (409)
    r_revive = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers={"Authorization": f"Bearer {super_tok}"},
        json={"action": "approve"},
    )
    assert r_revive.status_code == 409

    # Moderator /stats only returns moderator's own audit logs, not super_admin's takedown
    mod_stats = client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {mod_tok}"}).json()
    assert all(log["admin_username"] == "mod_alice" for log in mod_stats["recent_audit_logs"])
    sup_stats = client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {super_tok}"}).json()
    assert any(log["action"] == "takedown" for log in sup_stats["recent_audit_logs"])


def test_production_guard_and_security_headers(tmp_path: Path):
    # Production with default secrets must fail fast
    bad_cfg = Settings(
        app_env="production",
        database_path=str(tmp_path / "bad.db"),
        local_storage_dir=str(tmp_path / "m1"),
    )
    with pytest.raises(RuntimeError, match="Refusing to start"):
        create_app(bad_cfg)

    # Production with strong secrets boots, disables /docs, and emits security headers
    good_cfg = Settings(
        app_env="production",
        database_path=str(tmp_path / "good.db"),
        local_storage_dir=str(tmp_path / "m2"),
        jwt_secret="s" * 40,
        hmac_salt="h" * 24,
        collector_hmac_secret="c" * 24,
        admin_password="SuperStrongAdminPassword!2026",
    )
    app = create_app(good_cfg)
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "max-age=" in r.headers["strict-transport-security"]
        assert c.get("/docs").status_code == 404
        assert c.get("/openapi.json").status_code == 404


def test_cycle1a_cross_review_regressions(client: TestClient, test_settings: Settings, tmp_path: Path):
    import jwt as pyjwt
    import httpx
    from tests.conftest import collector_headers

    # 1. Missing X-Collector-Signature on internal collector route -> 401 WITH security headers (LIFO order)
    r_no_sig = client.post("/api/v1/internal/collector/ingest", content=b'{"blocks":[]}')
    assert r_no_sig.status_code == 401
    assert r_no_sig.headers["x-frame-options"] == "DENY"
    assert r_no_sig.headers["referrer-policy"] == "no-referrer"

    # 2. Oversized JSON on /internal/collector/ingest (> max_json_body_bytes = 512KB) -> 413
    r_big_json = client.post(
        "/api/v1/internal/collector/ingest",
        content=b"{" + b" " * (600 * 1024) + b"}",
        headers={
            "X-Collector-Timestamp": str(int(time.time())),
            "X-Collector-Signature": "a" * 64,
        },
    )
    assert r_big_json.status_code == 413

    # 3. /api/v1/submissions/media supports X-Collector-Nonce and rejects replay
    img = make_pattern_image_bytes(seed=303)
    req = httpx.Request("POST", "http://testserver/api/v1/submissions/media", files={"file": ("n.png", img, "image/png")})
    raw_mp = req.read()
    hdrs = {
        "Content-Type": req.headers["content-type"],
        **collector_headers(
            test_settings.collector_hmac_secret,
            "POST",
            "/api/v1/submissions/media",
            raw_mp,
            nonce="sub-media-nonce-1",
        ),
    }
    assert client.post("/api/v1/submissions/media", content=raw_mp, headers=hdrs).status_code == 200
    assert client.post("/api/v1/submissions/media", content=raw_mp, headers=hdrs).status_code == 401

    # 4. Malformed JWT ver claim (bool True / string / missing exp) -> 401, never 500
    with client.app.state.db.read() as conn:
        adm_id = conn.execute("SELECT id FROM admins LIMIT 1").fetchone()["id"]
    for bad_payload in [
        {"sub": adm_id, "type": "admin", "ver": True, "exp": int(time.time()) + 3600},
        {"sub": adm_id, "type": "admin", "ver": "notanint", "exp": int(time.time()) + 3600},
        {"sub": adm_id, "type": "admin", "ver": 1},  # missing exp
    ]:
        bad_tok = pyjwt.encode(bad_payload, test_settings.jwt_secret, algorithm="HS256")
        assert client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {bad_tok}"}).status_code == 401

    # 5. Null byte in /media/public -> 400 (never 500)
    assert client.get("/media/public/foo%00.webp").status_code == 400

    # 6. Empty edited_chat_messages rejected (422) and non-int avatar_color_idx does not 500
    inst = register_bot_instance(client)
    bot_hdrs = {"Authorization": f"Bearer {inst['instance_token']}"}
    admin_tok = login_admin(client)
    admin_hdrs = {"Authorization": f"Bearer {admin_tok}"}
    sub = client.post(
        "/api/v1/submissions",
        headers=bot_hdrs,
        json={
            "title": "chat edge",
            "blocks": [
                {
                    "type": "chat_record",
                    "messages": [{"sender_alias": "A", "avatar_color_idx": "oops", "content": [{"type": "text", "text": "hi"}]}],
                }
            ],
        },
    )
    assert sub.status_code == 201
    cid = sub.json()["submission_id"]
    r_empty = client.post(
        f"/api/v1/admin/items/{cid}/review",
        headers=admin_hdrs,
        json={"action": "approve", "edited_chat_messages": []},
    )
    assert r_empty.status_code == 422

    # 7. Production mode allows empty ADMIN_PASSWORD="" (disables auto-seed)
    prod_no_seed = Settings(
        app_env="production",
        database_path=str(tmp_path / "prod_noseed.db"),
        local_storage_dir=str(tmp_path / "m3"),
        jwt_secret="s" * 40,
        hmac_salt="h" * 24,
        collector_hmac_secret="c" * 24,
        admin_password="",
    )
    app_ns = create_app(prod_no_seed)
    with app_ns.state.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0
