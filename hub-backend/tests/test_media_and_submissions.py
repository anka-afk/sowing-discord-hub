from __future__ import annotations

import io

from PIL import Image

from tests.conftest import (
    login_admin,
    make_animated_gif_bytes,
    make_pattern_image_bytes,
    register_bot_instance,
)


def test_static_image_exif_strip_resize_and_webp_conversion(client):
    inst = register_bot_instance(client)
    headers = {"Authorization": f"Bearer {inst['instance_token']}"}
    admin_token = login_admin(client)

    big_jpeg = make_pattern_image_bytes(width=2400, height=1200, seed=11, fmt="JPEG", with_exif=True)
    resp = client.post(
        "/api/v1/submissions/media",
        headers=headers,
        files={"file": ("photo.jpg", big_jpeg, "image/jpeg")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["duplicate"] is False
    assert data["mime"] == "image/webp"
    assert data["w"] == 1920
    assert data["h"] == 960
    assert data["media_id"].startswith("med_")

    # Fetch stored image via admin preview and verify EXIF stripped & WebP format
    prev = client.get(
        f"/api/v1/admin/media/{data['media_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert prev.status_code == 200
    assert prev.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(prev.content)) as out_img:
        assert out_img.format == "WEBP"
        assert out_img.size == (1920, 960)
        assert len(out_img.getexif()) == 0
        assert b"SecretModelGPS" not in prev.content


def test_gif_preserved_and_comments_stripped(client):
    inst = register_bot_instance(client)
    headers = {"Authorization": f"Bearer {inst['instance_token']}"}
    admin_token = login_admin(client)

    gif_bytes = make_animated_gif_bytes(width=140, height=140, frames=3, comment=b"my-secret-comment")
    assert b"my-secret-comment" in gif_bytes

    resp = client.post(
        "/api/v1/submissions/media",
        headers=headers,
        files={"file": ("anim.gif", gif_bytes, "image/gif")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["mime"] == "image/gif"
    assert data["duplicate"] is False

    # Query-string full JWT (?token=) must be rejected
    prev_qs = client.get(f"/api/v1/admin/media/{data['media_id']}?token={admin_token}")
    assert prev_qs.status_code == 401

    # Bearer header succeeds with private no-store cache headers
    prev = client.get(
        f"/api/v1/admin/media/{data['media_id']}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert prev.status_code == 200
    assert prev.headers["cache-control"] == "private, no-store"
    assert prev.headers["content-type"] == "image/gif"
    assert b"my-secret-comment" not in prev.content
    with Image.open(io.BytesIO(prev.content)) as out_gif:
        assert out_gif.format == "GIF"
        assert getattr(out_gif, "n_frames", 1) == 3


def test_magic_bytes_validation_rejects_fake_image(client):
    resp = client.post(
        "/api/v1/submissions/media",
        files={"file": ("fake.png", b"not actually a png file at all!!", "image/png")},
    )
    assert resp.status_code == 422


def test_duplicate_privacy_pending_vs_approved_and_phash_similar(client):
    inst = register_bot_instance(client)
    headers = {"Authorization": f"Bearer {inst['instance_token']}"}
    admin_token = login_admin(client)

    img_bytes = make_pattern_image_bytes(width=400, height=300, seed=42, fmt="PNG")
    up1 = client.post(
        "/api/v1/submissions/media",
        headers=headers,
        files={"file": ("meme.png", img_bytes, "image/png")},
    )
    assert up1.status_code == 200
    media_id = up1.json()["media_id"]

    # Attach to a pending submission
    sub = client.post(
        "/api/v1/submissions",
        headers=headers,
        json={
            "title": "经典老图",
            "tags": ["沙雕"],
            "blocks": [{"type": "image", "media_id": media_id}],
        },
    )
    assert sub.status_code == 201
    item_id = sub.json()["submission_id"]

    # Re-upload exact same image while PENDING -> existing_item_id MUST be null!
    up_dup_pending = client.post(
        "/api/v1/submissions/media",
        headers=headers,
        files={"file": ("meme_again.png", img_bytes, "image/png")},
    )
    assert up_dup_pending.status_code == 200
    d1 = up_dup_pending.json()
    assert d1["duplicate"] is True
    assert d1["match_type"] == "exact"
    assert d1["existing_status"] == "pending"
    assert d1["existing_item_id"] is None

    # Slightly re-encoded JPEG (different SHA-256, near-identical pHash) while PENDING -> existing_item_id MUST be null!
    with Image.open(io.BytesIO(img_bytes)) as im:
        im_resized = im.resize((380, 285))
        buf = io.BytesIO()
        im_resized.save(buf, format="JPEG", quality=85)
        similar_bytes = buf.getvalue()

    up_sim_pending = client.post(
        "/api/v1/submissions/media",
        headers=headers,
        files={"file": ("meme_similar.jpg", similar_bytes, "image/jpeg")},
    )
    assert up_sim_pending.status_code == 200
    d2 = up_sim_pending.json()
    assert d2["duplicate"] is True
    assert d2["match_type"] == "similar"
    assert d2["existing_status"] == "pending"
    assert d2["existing_item_id"] is None

    # Approve the item
    rev = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"action": "approve", "tags": ["沙雕"], "nsfw_level": 0},
    )
    assert rev.status_code == 200

    # Now exact & similar uploads MUST return existing_status="approved" and existing_item_id=item_id
    up_dup_approved = client.post(
        "/api/v1/submissions/media",
        headers=headers,
        files={"file": ("meme_again.png", img_bytes, "image/png")},
    )
    assert up_dup_approved.status_code == 200
    d3 = up_dup_approved.json()
    assert d3["duplicate"] is True
    assert d3["existing_status"] == "approved"
    assert d3["existing_item_id"] == item_id

    up_sim_approved = client.post(
        "/api/v1/submissions/media",
        headers=headers,
        files={"file": ("meme_similar.jpg", similar_bytes, "image/jpeg")},
    )
    assert up_sim_approved.status_code == 200
    d4 = up_sim_approved.json()
    assert d4["duplicate"] is True
    assert d4["match_type"] == "similar"
    assert d4["existing_status"] == "approved"
    assert d4["existing_item_id"] == item_id


def test_submission_block_guards_and_user_token_priority(client):
    admin_token = login_admin(client)

    # 1. video_file forbidden in MVP
    r_vf = client.post(
        "/api/v1/submissions",
        json={"blocks": [{"type": "video_file", "url": "https://cdn.example.com/a.mp4"}]},
    )
    assert r_vf.status_code == 422

    # 2. video_link non-whitelisted domain forbidden
    r_vl_bad = client.post(
        "/api/v1/submissions",
        json={"blocks": [{"type": "video_link", "url": "https://evil.invalid/video/1"}]},
    )
    assert r_vl_bad.status_code == 422

    # 3. video_link whitelisted domain allowed
    r_vl_ok = client.post(
        "/api/v1/submissions",
        json={
            "title": "B站好活",
            "blocks": [
                {
                    "type": "video_link",
                    "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                    "title": "逆天整活",
                    "duration_sec": 42,
                }
            ],
        },
    )
    assert r_vl_ok.status_code == 201
    normal_id = r_vl_ok.json()["submission_id"]

    # 4. External image URL without media_id forbidden (anti-SSRF)
    r_ssrf = client.post(
        "/api/v1/submissions",
        json={"blocks": [{"type": "image", "url": "http://169.254.169.254/latest/meta-data"}]},
    )
    assert r_ssrf.status_code == 422

    # 5. Nested chat_record forbidden
    r_nested = client.post(
        "/api/v1/submissions",
        json={
            "blocks": [
                {
                    "type": "chat_record",
                    "messages": [
                        {
                            "sender_alias": "群友A",
                            "content": [{"type": "chat_record", "messages": []}],
                        }
                    ],
                }
            ]
        },
    )
    assert r_nested.status_code == 422

    # 6. Pre-registered X-User-Token gives higher trust_level so item sorts ahead in admin queue;
    #    unregistered arbitrary X-User-Token must NOT auto-elevate trust_level!
    from app.cli_admin import register_user_token

    register_user_token(client.app.state.db, client.app.state.settings, "trusted-contributor-token-999", trust_level=1)
    r_vip = client.post(
        "/api/v1/submissions",
        headers={"X-User-Token": "trusted-contributor-token-999"},
        json={"title": "高优通道投稿", "blocks": [{"type": "text", "text": "高优投稿内容"}]},
    )
    assert r_vip.status_code == 201
    vip_id = r_vip.json()["submission_id"]

    q = client.get(
        "/api/v1/admin/queue?status=pending",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert q.status_code == 200
    items = q.json()["items"]
    assert len(items) >= 2
    assert items[0]["id"] == vip_id
    assert items[0]["submitter_stats"]["trust_level"] >= 1
    assert any(it["id"] == normal_id for it in items)
