from __future__ import annotations

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_TAGS = [
    "沙雕",
    "群聊日常",
    "地狱笑话",
    "二次元",
    "抽象",
    "游戏",
    "程序员",
    "猫猫狗狗",
    "影视",
    "生活",
    "搬史经典",
]

DEFAULT_VIDEO_LINK_DOMAINS = [
    "bilibili.com",
    "www.bilibili.com",
    "m.bilibili.com",
    "b23.tv",
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "douyin.com",
    "www.douyin.com",
    "v.douyin.com",
    "twitter.com",
    "x.com",
    "acfun.cn",
    "www.acfun.cn",
    "nicovideo.jp",
    "www.nicovideo.jp",
    "tiktok.com",
    "www.tiktok.com",
    "vimeo.com",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hub_id: str = "official-main"
    hub_name: str = "Sowing Discord Hub"
    schema_version: str = "1.0"
    web_base_url: str = "https://banshi.ankaanka.org"
    api_base_url: str = "http://localhost:8000"

    # Database
    database_path: str = "./data/hub.db"

    # Storage: "local" or "r2"
    storage_backend: str = "local"
    local_storage_dir: str = "./data/media"
    public_media_base_url: str = "http://localhost:8000/media/public"
    storage_limit_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GB

    # Cloudflare R2 (S3-compatible)
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "sowing-discord-media"
    r2_region: str = "auto"

    # Optional Cloudflare Cache Purge
    cf_zone_id: str = ""
    cf_api_token: str = ""

    # Security & Auth
    jwt_secret: str = "dev-jwt-secret-change-in-production-32bytes"
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 86400
    hmac_salt: str = "dev-hmac-salt-change-in-production"
    collector_hmac_secret: str = "dev-collector-hmac-secret"
    collector_max_skew_seconds: int = 300

    # Turnstile
    turnstile_enabled: bool = False
    turnstile_secret_key: str = ""

    # Admin seed
    admin_username: str = "admin"
    admin_password: str = "admin123456"

    # PoW
    pow_difficulty_bits: int = 16
    pow_ttl_seconds: int = 300

    # Limits (Schema v1.0)
    max_image_bytes: int = 10485760  # 10MB
    max_gif_bytes: int = 5242880  # 5MB
    max_image_dimension: int = 1920
    max_image_pixels: int = 40_000_000  # anti decompression bomb
    max_gif_frames: int = 300
    max_images_per_submission: int = 9
    max_chat_nodes: int = 20
    max_chat_depth: int = 1
    phash_similar_threshold: int = 8

    # Feedback & circuit breaker
    feedback_batch_max: int = 50
    feedback_max_group_contribution: int = 10
    feedback_report_threshold: int = 3
    feedback_instance_min_age_hours: int = 24

    # Cleanup policy
    rejected_media_retention_days: int = 7
    stale_pending_days: int = 14

    # CORS
    cors_origins: str = "*"

    # Capabilities
    capability_video_file: bool = False
    capability_feedback: bool = True

    # Tags & whitelist
    default_tags: list[str] = Field(default_factory=lambda: list(DEFAULT_TAGS))
    video_link_domains: list[str] = Field(
        default_factory=lambda: list(DEFAULT_VIDEO_LINK_DOMAINS)
    )

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        if self.storage_backend == "local":
            base = Path(self.local_storage_dir)
            (base / "private").mkdir(parents=True, exist_ok=True)
            (base / "public").mkdir(parents=True, exist_ok=True)
