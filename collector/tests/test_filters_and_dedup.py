from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sowing_collector.ac_filter import AhoCorasickFilter
from sowing_collector.config import CollectorConfig, FilterConfig, load_config
from sowing_collector.dedup import DedupStore, compute_phash, compute_sha256
from sowing_collector.media import MediaValidationError, detect_magic_mime, validate_media_bytes


def _make_pattern_png(seed: int = 0, size: tuple[int, int] = (128, 128), tweak: int = 0) -> bytes:
    img = Image.new("RGB", size, (240, 240, 240))
    draw = ImageDraw.Draw(img)
    if seed == 0:
        draw.rectangle([10, 10, 60, 110], fill=(20 + tweak, 80, 200))
        draw.ellipse([70, 20, 120, 90], fill=(220, 60, 40))
        draw.line([0, 120, 128, 0], fill=(10, 10, 10), width=4)
    else:
        draw.polygon([(64, 5), (120, 120), (8, 120)], fill=(30, 180, 60))
        draw.rectangle([40, 40, 90, 90], fill=(250, 210, 20))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_gif() -> bytes:
    f1 = Image.new("RGB", (64, 64), (200, 30, 30))
    f2 = Image.new("RGB", (64, 64), (30, 30, 200))
    buf = io.BytesIO()
    f1.save(buf, format="GIF", save_all=True, append_images=[f2], duration=100, loop=0)
    return buf.getvalue()


def test_aho_corasick_matches_overlapping_and_case_insensitive():
    ac = AhoCorasickFilter(["违禁词", "禁词测试", "SpamLink", "广告"])
    hits = ac.search("这是一段包含违禁词测试和spamlink的文本")
    assert "违禁词" in hits
    assert "禁词测试" in hits
    assert "SpamLink" in hits
    assert "广告" not in hits
    assert ac.contains_sensitive("hello SPAMLINK world")
    assert not ac.contains_sensitive("正常沙雕群聊日常")


def test_config_loads_yaml_and_sensitive_words_file(tmp_path: Path):
    words_file = tmp_path / "words.txt"
    words_file.write_text("# comment\n敏感词一\n\n敏感词二\n", encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"""
hub:
  base_url: "http://hub.local:9000"
  shared_secret: "secret-123"
onebot:
  watch_groups: [111, 222]
filters:
  sensitive_words: ["内置词"]
  sensitive_words_file: "{words_file.as_posix()}"
""",
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.hub.base_url == "http://hub.local:9000"
    assert cfg.onebot.watch_groups == [111, 222]
    all_words = cfg.load_all_sensitive_words()
    assert set(all_words) == {"内置词", "敏感词一", "敏感词二"}


def test_media_validation_and_video_rejection():
    png_bytes = _make_pattern_png(seed=0)
    info = validate_media_bytes(png_bytes)
    assert info.mime == "image/png"
    assert not info.is_gif
    assert info.width == 128 and info.height == 128
    assert len(info.sha256) == 64
    assert len(info.phash) == 16

    gif_bytes = _make_gif()
    gif_info = validate_media_bytes(gif_bytes)
    assert gif_info.mime == "image/gif"
    assert gif_info.is_gif

    # Fake MP4 magic bytes
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    assert detect_magic_mime(fake_mp4) == "video/mp4"
    with pytest.raises(MediaValidationError) as exc_info:
        validate_media_bytes(fake_mp4)
    assert exc_info.value.code == "video_file_not_allowed"

    # GIF size cap enforcement
    with pytest.raises(MediaValidationError) as exc_gif:
        validate_media_bytes(gif_bytes, max_gif_bytes=10)
    assert exc_gif.value.code == "gif_too_large"


def test_dedup_store_sqlite_sha256_and_phash(tmp_path: Path):
    db_file = tmp_path / "dedup.db"
    img_a = _make_pattern_png(seed=0, tweak=0)
    img_a_near = _make_pattern_png(seed=0, tweak=5)
    img_b = _make_pattern_png(seed=1)

    store = DedupStore(db_path=db_file, phash_threshold=8)
    sha_a, phash_a = compute_sha256(img_a), compute_phash(img_a)
    sha_near, phash_near = compute_sha256(img_a_near), compute_phash(img_a_near)
    sha_b, phash_b = compute_sha256(img_b), compute_phash(img_b)

    assert not store.check_media_duplicate(sha_a, phash_a).duplicate
    store.mark_media_seen(sha_a, phash_a)
    store.mark_message_seen(10001, "999", "hashtag")
    store.close()

    # Re-open from SQLite to confirm persistence
    store2 = DedupStore(db_path=db_file, phash_threshold=8)
    assert store2.is_message_seen(10001, "999")
    exact_res = store2.check_media_duplicate(sha_a, phash_a)
    assert exact_res.duplicate and exact_res.match_type == "exact"

    sim_res = store2.check_media_duplicate(sha_near, phash_near)
    assert sim_res.duplicate and sim_res.match_type == "similar"

    diff_res = store2.check_media_duplicate(sha_b, phash_b)
    assert not diff_res.duplicate
    store2.close()
