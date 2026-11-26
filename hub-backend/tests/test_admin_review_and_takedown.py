from __future__ import annotations

from tests.conftest import (
    login_admin,
    make_pattern_image_bytes,
    register_bot_instance,
)


def test_admin_login_and_auth_guards(client):
    bad = client.post("/api/v1/admin/login", json={"username": "admin", "password": "wrong"})
    assert bad.status_code == 401

    no_auth = client.get("/api/v1/admin/queue")
    assert no_auth.status_code == 401

    token = login_admin(client)
    ok = client.get("/api/v1/admin/queue", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert ok.json()["total"] == 0


def test_chat_record_requires_rendered_images_and_auto_summary(client):
    inst = register_bot_instance(client)
    bot_headers = {"Authorization": f"Bearer {inst['instance_token']}"}
    admin_token = login_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Upload an inline image for the chat record
    inline_png = make_pattern_image_bytes(width=300, height=300, seed=7)
    up_inline = client.post(
        "/api/v1/submissions/media",
        headers=bot_headers,
        files={"file": ("inline.png", inline_png, "image/png")},
    )
    assert up_inline.status_code == 200
    inline_mid = up_inline.json()["media_id"]

    # Submit chat_record containing suspected privacy info (phone & QQ)
    long_text = "绷不住了，昨天群里有人发手机号 13812345678 还贴了 QQ 123456789 和邮箱 test@example.com " + ("哈哈" * 60)
    sub = client.post(
        "/api/v1/submissions",
        headers=bot_headers,
        json={
            "title": "群聊名场面",
            "tags": ["群聊日常"],
            "blocks": [
                {
                    "type": "chat_record",
                    "messages": [
                        {
                            "sender_alias": "群友A",
                            "avatar_color_idx": 0,
                            "time_label": "14:20",
                            "content": [
                                {"type": "text", "text": long_text},
                                {"type": "image", "media_id": inline_mid},
                            ],
                        },
                        {
                            "sender_alias": "群友B",
                            "avatar_color_idx": 1,
                            "time_label": "14:21",
                            "content": [{"type": "text", "text": "典中典"}],
                        },
                    ],
                }
            ],
        },
    )
    assert sub.status_code == 201
    item_id = sub.json()["submission_id"]

    # Admin queue must surface privacy warnings (phone, email, qq)
    queue = client.get("/api/v1/admin/queue?status=pending", headers=admin_headers).json()
    assert queue["total"] == 1
    q_item = queue["items"][0]
    warn_types = {w["type"] for w in q_item["privacy_warnings"]}
    assert {"phone", "email", "qq"}.issubset(warn_types)

    # Attempting to approve chat_record WITHOUT rendered_images MUST fail with 422!
    fail_rev = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers=admin_headers,
        json={"action": "approve", "tags": ["群聊日常"], "nsfw_level": 0},
    )
    assert fail_rev.status_code == 422
    assert "rendered_images" in fail_rev.json()["detail"]

    # Upload browser-rendered 720x1600 slice via POST /api/v1/admin/media
    slice_img = make_pattern_image_bytes(width=720, height=1600, seed=99)
    up_slice = client.post(
        "/api/v1/admin/media",
        headers=admin_headers,
        files={"file": ("slice_1.png", slice_img, "image/png")},
        data={"purpose": "chat_render"},
    )
    assert up_slice.status_code == 200
    slice_mid = up_slice.json()["media_id"]

    # Approve WITH rendered_images and omitted summary (must auto-truncate <= 120 chars)
    ok_rev = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers=admin_headers,
        json={
            "action": "approve",
            "tags": ["群聊日常", "沙雕"],
            "nsfw_level": 0,
            "rendered_images": [{"media_id": slice_mid}],
        },
    )
    assert ok_rev.status_code == 200, ok_rev.text
    rev_data = ok_rev.json()
    assert rev_data["status"] == "approved"
    assert rev_data["seq"] == 1
    assert 0 < len(rev_data["summary"]) <= 120

    # Verify item is now public via GET /api/v1/items/{id} with public media URLs
    pub = client.get(f"/api/v1/items/{item_id}")
    assert pub.status_code == 200
    pub_item = pub.json()
    chat_blk = pub_item["blocks"][0]
    assert chat_blk["type"] == "chat_record"
    assert len(chat_blk["rendered_images"]) == 1
    slice_url = chat_blk["rendered_images"][0]["url"]
    assert "/media/public/m/" in slice_url

    # Public media endpoint serves migrated slice with immutable Cache-Control
    rel_path = slice_url.replace("http://testserver", "")
    media_resp = client.get(rel_path)
    assert media_resp.status_code == 200
    assert media_resp.headers["content-type"] == "image/webp"
    assert "immutable" in media_resp.headers["cache-control"]


def test_kill_switch_takedown_deletes_storage_and_emits_tombstone(client):
    admin_token = login_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    img = make_pattern_image_bytes(width=320, height=240, seed=55)
    up = client.post(
        "/api/v1/submissions/media",
        headers=admin_headers,
        files={"file": ("bad.png", img, "image/png")},
    )
    mid = up.json()["media_id"]

    sub = client.post(
        "/api/v1/submissions",
        headers=admin_headers,
        json={"title": "待下架图", "blocks": [{"type": "image", "media_id": mid}]},
    )
    item_id = sub.json()["submission_id"]

    rev = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )
    assert rev.status_code == 200
    approved_seq = rev.json()["seq"]

    # Fetch public URL before takedown
    detail = client.get(f"/api/v1/items/{item_id}").json()
    pub_rel = detail["blocks"][0]["url"].replace("http://testserver", "")
    assert client.get(pub_rel).status_code == 200

    # Kill Switch takedown
    td = client.post(
        f"/api/v1/admin/items/{item_id}/takedown",
        headers=admin_headers,
        json={"reason": "privacy_leak"},
    )
    assert td.status_code == 200
    td_data = td.json()
    assert td_data["status"] == "takedown"
    assert td_data["tombstone_seq"] > approved_seq

    # Item detail returns 410 Gone
    gone = client.get(f"/api/v1/items/{item_id}")
    assert gone.status_code == 410

    # Physical storage file is deleted
    assert client.get(pub_rel).status_code == 404

    # Feed incremental sync returns tombstone and excludes taken-down item
    feed = client.get(f"/api/v1/feed?after_seq={approved_seq}").json()
    assert item_id in feed["tombstones"]
    assert all(it["id"] != item_id for it in feed["items"])
    assert feed["next_seq"] >= td_data["tombstone_seq"]

    # Admin stats & audit logs reflect takedown
    stats = client.get("/api/v1/admin/stats", headers=admin_headers).json()
    assert stats["takedown_count"] == 1
    assert any(log["action"] == "takedown" and log["target_id"] == item_id for log in stats["recent_audit_logs"])
