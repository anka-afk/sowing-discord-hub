from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import login_admin, register_bot_instance


def _create_and_approve_text_item(client, admin_headers, title: str, text: str, tags: list[str], nsfw: int = 0) -> dict:
    sub = client.post(
        "/api/v1/submissions",
        headers=admin_headers,
        json={"title": title, "tags": tags, "blocks": [{"type": "text", "text": text}]},
    )
    assert sub.status_code == 201
    item_id = sub.json()["submission_id"]
    rev = client.post(
        f"/api/v1/admin/items/{item_id}/review",
        headers=admin_headers,
        json={"action": "approve", "tags": tags, "nsfw_level": nsfw},
    )
    assert rev.status_code == 200
    return rev.json()


def test_feed_cursor_filters_and_etag_304(client):
    admin_token = login_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    i1 = _create_and_approve_text_item(client, admin_headers, "一", "内容1", ["沙雕"], nsfw=0)
    i2 = _create_and_approve_text_item(client, admin_headers, "二", "内容2", ["地狱笑话"], nsfw=1)
    i3 = _create_and_approve_text_item(client, admin_headers, "三", "内容3", ["抽象"], nsfw=2)

    # Default max_nsfw=1 excludes nsfw=2
    f1 = client.get("/api/v1/feed?after_seq=0&limit=10")
    assert f1.status_code == 200
    etag = f1.headers.get("etag")
    assert etag
    data = f1.json()
    assert [it["id"] for it in data["items"]] == [i1["item_id"], i2["item_id"]]
    assert data["has_more"] is False

    # Conditional ETag returns 304 Not Modified
    f304 = client.get("/api/v1/feed?after_seq=0&limit=10", headers={"If-None-Match": etag})
    assert f304.status_code == 304

    # max_nsfw=2 with exclude_tags
    f2 = client.get("/api/v1/feed?after_seq=0&max_nsfw=2&exclude_tags=地狱笑话").json()
    assert [it["id"] for it in f2["items"]] == [i1["item_id"], i3["item_id"]]

    # tags filter
    f3 = client.get("/api/v1/feed?after_seq=0&max_nsfw=2&tags=抽象").json()
    assert [it["id"] for it in f3["items"]] == [i3["item_id"]]

    # Browse mode with before_seq pagination
    fb = client.get(f"/api/v1/feed?max_nsfw=2&before_seq={i3['seq']}&limit=1").json()
    assert [it["id"] for it in fb["items"]] == [i2["item_id"]]
    assert fb["has_more"] is True


def test_random_filters(client):
    admin_token = login_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    old_item = _create_and_approve_text_item(client, admin_headers, "老史", "十天前的史", ["搬史经典"], nsfw=0)
    new_item = _create_and_approve_text_item(client, admin_headers, "新史", "今天的史", ["沙雕"], nsfw=0)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with client.app.state.db.write() as conn:
        conn.execute(
            "UPDATE items SET published_at = ?, score = 75.0 WHERE id = ?",
            (old_ts, old_item["item_id"]),
        )
        conn.execute(
            "UPDATE items SET score = 10.0 WHERE id = ?",
            (new_item["item_id"],),
        )

    r_old = client.get("/api/v1/random?older_than_days=5").json()
    assert [it["id"] for it in r_old["items"]] == [old_item["item_id"]]

    r_new = client.get("/api/v1/random?within_days=3").json()
    assert [it["id"] for it in r_new["items"]] == [new_item["item_id"]]

    r_score = client.get("/api/v1/random?min_score=50").json()
    assert [it["id"] for it in r_score["items"]] == [old_item["item_id"]]


def test_feedback_dedup_group_cap_wilson_and_3_instance_24h_circuit_breaker(client):
    admin_token = login_admin(client)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    approved = _create_and_approve_text_item(client, admin_headers, "测试反馈", "一段好笑的话", ["沙雕"])
    item_id = approved["item_id"]

    # Unauthenticated feedback must fail
    no_auth = client.post("/api/v1/feedback/batch", json={"events": []})
    assert no_auth.status_code in (401, 422)

    inst1 = register_bot_instance(client)
    inst2 = register_bot_instance(client)
    inst3 = register_bot_instance(client)

    h1 = {"Authorization": f"Bearer {inst1['instance_token']}"}
    h2 = {"Authorization": f"Bearer {inst2['instance_token']}"}
    h3 = {"Authorization": f"Bearer {inst3['instance_token']}"}
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Test dedup + per-group cap (configured as 3 in test_settings) + Wilson score
    batch1 = client.post(
        "/api/v1/feedback/batch",
        headers=h1,
        json={
            "events": [
                {"item_id": item_id, "action": "like", "voter_hash": "voter_001", "group_hash": "grpA_hash", "occurred_at": now_iso},
                {"item_id": item_id, "action": "like", "voter_hash": "voter_001", "group_hash": "grpA_hash", "occurred_at": now_iso},  # dup
                {"item_id": item_id, "action": "like", "voter_hash": "voter_002", "group_hash": "grpA_hash", "occurred_at": now_iso},
                {"item_id": item_id, "action": "like", "voter_hash": "voter_003", "group_hash": "grpA_hash", "occurred_at": now_iso},
                {"item_id": item_id, "action": "like", "voter_hash": "voter_004", "group_hash": "grpA_hash", "occurred_at": now_iso},  # capped
            ]
        },
    )
    assert batch1.status_code == 200, batch1.text
    b1 = batch1.json()
    assert b1["accepted"] == 3
    assert b1["deduplicated"] == 1
    assert b1["capped"] == 1

    item_detail = client.get(f"/api/v1/items/{item_id}").json()
    assert item_detail["stats"]["likes"] == 3
    assert item_detail["score"] > 0.0

    # Batch > 50 events rejected
    too_many = client.post(
        "/api/v1/feedback/batch",
        headers=h1,
        json={
            "events": [
                {"item_id": item_id, "action": "like", "voter_hash": f"u{i}", "group_hash": f"g{i}", "occurred_at": now_iso}
                for i in range(51)
            ]
        },
    )
    assert too_many.status_code == 422

    # 3 newly registered (<24h) instances report -> MUST NOT trip circuit breaker
    for idx, hdr in enumerate([h1, h2, h3]):
        r = client.post(
            "/api/v1/feedback/batch",
            headers=hdr,
            json={
                "events": [
                    {
                        "item_id": item_id,
                        "action": "report",
                        "reason": "privacy_leak",
                        "voter_hash": f"rep_new_{idx}",
                        "group_hash": f"grp_rep_{idx}",
                        "occurred_at": now_iso,
                    }
                ]
            },
        )
        assert r.status_code == 200
        assert r.json()["circuit_broken_items"] == []

    # Item is still approved because none of the 3 instances is >= 24h old
    assert client.get(f"/api/v1/items/{item_id}").status_code == 200

    # Backdate inst1, inst2, inst3 to 25h ago, and send one more report from inst4 (also >= 24h) or inst3
    old_created = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with client.app.state.db.write() as conn:
        conn.execute(
            "UPDATE bot_instances SET created_at = ? WHERE instance_id IN (?, ?)",
            (old_created, inst1["instance_id"], inst2["instance_id"]),
        )

    # Only 2 mature instances so far -> reporting from a 4th new instance still does NOT trigger
    inst4 = register_bot_instance(client)
    h4 = {"Authorization": f"Bearer {inst4['instance_token']}"}
    r4 = client.post(
        "/api/v1/feedback/batch",
        headers=h4,
        json={
            "events": [
                {
                    "item_id": item_id,
                    "action": "report",
                    "reason": "privacy_leak",
                    "voter_hash": "rep_4",
                    "group_hash": "grp_rep_4",
                    "occurred_at": now_iso,
                }
            ]
        },
    )
    assert r4.status_code == 200
    assert r4.json()["circuit_broken_items"] == []

    # Now make inst3 mature (>=24h) and have inst3 submit a distinct voter report -> 3 mature instances -> CIRCUIT BREAKER!
    with client.app.state.db.write() as conn:
        conn.execute(
            "UPDATE bot_instances SET created_at = ? WHERE instance_id = ?",
            (old_created, inst3["instance_id"]),
        )

    r_trip = client.post(
        "/api/v1/feedback/batch",
        headers=h3,
        json={
            "events": [
                {
                    "item_id": item_id,
                    "action": "report",
                    "reason": "privacy_leak",
                    "voter_hash": "rep_trigger",
                    "group_hash": "grp_trigger",
                    "occurred_at": now_iso,
                }
            ]
        },
    )
    assert r_trip.status_code == 200
    assert r_trip.json()["circuit_broken_items"] == [item_id]

    # Item is now pending_recheck (410 Gone) and emitted as tombstone on /feed
    assert client.get(f"/api/v1/items/{item_id}").status_code == 410
    feed = client.get(f"/api/v1/feed?after_seq={approved['seq']}").json()
    assert item_id in feed["tombstones"]

    # Appears in admin pending_recheck queue
    recheck_q = client.get(
        "/api/v1/admin/queue?status=pending_recheck",
        headers=admin_headers,
    ).json()
    assert any(it["id"] == item_id for it in recheck_q["items"])
