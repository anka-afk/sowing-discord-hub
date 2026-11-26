from __future__ import annotations

import hashlib
import io
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.config import Settings
from app.main import create_app
from app.security import compute_collector_signature


@pytest.fixture()
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=str(tmp_path / "test_hub.db"),
        storage_backend="local",
        local_storage_dir=str(tmp_path / "media"),
        public_media_base_url="http://testserver/media/public",
        web_base_url="https://hub.example.org",
        turnstile_enabled=False,
        jwt_secret="test-jwt-secret-key-32-bytes-minimum!",
        hmac_salt="test-hmac-salt",
        collector_hmac_secret="test-collector-secret",
        admin_username="admin",
        admin_password="testpassword123",
        pow_difficulty_bits=16,
        feedback_max_group_contribution=3,
    )


@pytest.fixture()
def client(test_settings: Settings):
    app = create_app(test_settings)
    with TestClient(app) as c:
        yield c


def solve_pow(nonce_prefix: str, difficulty_bits: int = 16) -> str:
    shift = 256 - difficulty_bits
    counter = 0
    while True:
        sol = str(counter)
        digest = hashlib.sha256(f"{nonce_prefix}:{sol}".encode("utf-8")).digest()
        if (int.from_bytes(digest, "big") >> shift) == 0:
            return sol
        counter += 1


def register_bot_instance(client: TestClient, client_version: str = "2.0.0") -> dict[str, str]:
    chl = client.get("/api/v1/instances/challenge")
    assert chl.status_code == 200
    data = chl.json()
    sol = solve_pow(data["nonce_prefix"], data["difficulty_bits"])
    reg = client.post(
        "/api/v1/instances/register",
        json={
            "challenge_id": data["challenge_id"],
            "solution": sol,
            "client_version": client_version,
        },
    )
    assert reg.status_code == 200, reg.text
    return reg.json()


def login_admin(client: TestClient, username: str = "admin", password: str = "testpassword123") -> str:
    resp = client.post(
        "/api/v1/admin/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def make_pattern_image_bytes(
    width: int = 320,
    height: int = 240,
    seed: int = 1,
    fmt: str = "PNG",
    with_exif: bool = False,
) -> bytes:
    """Create a structured image with distinct features so pHash is non-degenerate."""
    img = Image.new("RGB", (width, height), (20 + (seed * 37) % 180, 40 + (seed * 61) % 180, 90))
    draw = ImageDraw.Draw(img)
    # Draw distinct geometric patterns per seed so different seeds have distinct pHashes
    for i in range(6):
        x0 = ((seed * 53 + i * 47) % max(1, width - 40))
        y0 = ((seed * 29 + i * 31) % max(1, height - 40))
        color = (
            (seed * 71 + i * 40) % 255,
            (seed * 23 + i * 80) % 255,
            (seed * 97 + i * 25) % 255,
        )
        if (seed + i) % 2 == 0:
            draw.rectangle([x0, y0, x0 + 50, y0 + 50], fill=color)
        else:
            draw.ellipse([x0, y0, x0 + 60, y0 + 40], fill=color)

    buf = io.BytesIO()
    if fmt.upper() == "JPEG" and with_exif:
        exif = Image.Exif()
        exif[271] = "TestCameraMaker"  # Make
        exif[272] = "SecretModelGPS"  # Model
        img.save(buf, format="JPEG", quality=92, exif=exif.tobytes())
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


def make_animated_gif_bytes(
    width: int = 120,
    height: int = 120,
    frames: int = 3,
    comment: bytes | None = b"secret-gif-comment-gps",
) -> bytes:
    imgs = []
    for idx in range(frames):
        im = Image.new("RGB", (width, height), (idx * 60 % 255, 100, 200))
        draw = ImageDraw.Draw(im)
        draw.rectangle([10 + idx * 15, 10, 50 + idx * 15, 80], fill=(255, 220, 40))
        imgs.append(im)
    buf = io.BytesIO()
    save_kwargs = {
        "format": "GIF",
        "save_all": True,
        "append_images": imgs[1:],
        "duration": 100,
        "loop": 0,
    }
    if comment is not None:
        save_kwargs["comment"] = comment
    imgs[0].save(buf, **save_kwargs)
    return buf.getvalue()


def collector_headers(
    secret: str,
    method: str,
    path: str,
    body: bytes,
    ts: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    timestamp = str(int(time.time()) if ts is None else ts)
    sig = compute_collector_signature(secret, timestamp, method, path, body, nonce=nonce)
    hdrs = {
        "X-Collector-Timestamp": timestamp,
        "X-Collector-Signature": sig,
    }
    if nonce:
        hdrs["X-Collector-Nonce"] = nonce
    return hdrs
