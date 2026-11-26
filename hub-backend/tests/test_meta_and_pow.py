from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests.conftest import register_bot_instance, solve_pow


def test_healthz_and_meta(client):
    hz = client.get("/healthz")
    assert hz.status_code == 200
    assert hz.json()["status"] == "ok"

    resp = client.get("/api/v1/meta")
    assert resp.status_code == 200
    assert "max-age=300" in resp.headers.get("cache-control", "")
    data = resp.json()
    assert data["schema_version"] == "1.0"
    assert data["capabilities"]["video_file"] is False
    assert data["capabilities"]["feedback"] is True
    assert data["limits"] == {
        "max_image_bytes": 10485760,
        "max_gif_bytes": 5242880,
        "max_images_per_submission": 20,
        "max_chat_nodes": 50,
        "max_chat_depth": 3,
    }
    assert len(data["tags"]) > 0


def test_pow_registration_and_replay_protection(client):
    # Happy path
    chl = client.get("/api/v1/instances/challenge").json()
    assert chl["difficulty_bits"] == 16
    sol = solve_pow(chl["nonce_prefix"], chl["difficulty_bits"])

    # Invalid solution must fail
    bad = client.post(
        "/api/v1/instances/register",
        json={"challenge_id": chl["challenge_id"], "solution": "definitely-wrong-xyz", "client_version": "2.0.0"},
    )
    assert bad.status_code == 400

    # Valid solution succeeds
    ok = client.post(
        "/api/v1/instances/register",
        json={"challenge_id": chl["challenge_id"], "solution": sol, "client_version": "2.0.0"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["instance_id"].startswith("inst_")
    assert len(body["instance_token"]) >= 32

    # Replay same challenge must fail
    replay = client.post(
        "/api/v1/instances/register",
        json={"challenge_id": chl["challenge_id"], "solution": sol, "client_version": "2.0.0"},
    )
    assert replay.status_code == 400


def test_expired_pow_challenge_rejected(client):
    chl = client.get("/api/v1/instances/challenge").json()
    sol = solve_pow(chl["nonce_prefix"], chl["difficulty_bits"])

    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with client.app.state.db.write() as conn:
        conn.execute(
            "UPDATE pow_challenges SET expires_at = ? WHERE challenge_id = ?",
            (past, chl["challenge_id"]),
        )

    resp = client.post(
        "/api/v1/instances/register",
        json={"challenge_id": chl["challenge_id"], "solution": sol, "client_version": "2.0.0"},
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


def test_register_helper(client):
    inst = register_bot_instance(client)
    assert inst["instance_id"].startswith("inst_")
    assert inst["instance_token"]
