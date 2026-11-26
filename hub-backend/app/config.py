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
    app_env: str = "development"  # development | test | production
    web_base_url: str = "https://banshi.ankaanka.org"
    api_base_url: str = "http://localhost:8000"

    # Database
    database_path: str = "./data/hub.db"

    # Storage: "local" or "r2"
    storage_backend: str = "local"
    local_storage_dir: str = "./data/media"
    public_media_base_url: str = "http://localhost:8000/media/public"
    storage_limit_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GB

    # Cloudflare R2 (S3-compatible) — supports split private/public buckets
    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "sowing-discord-media"
    r2_private_bucket_name: str = ""
    r2_public_bucket_name: str = ""
    r2_region: str = "auto"

    # Optional Cloudflare Cache Purge
    cf_zone_id: str = ""
    cf_api_token: str = ""

    # Security & Auth
    jwt_secret: str = "dev-jwt-secret-change-in-production-32bytes"
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 86400
    admin_media_url_ttl_seconds: int = 300
    hmac_salt: str = "dev-hmac-salt-change-in-production"
    collector_hmac_secret: str = "dev-collector-hmac-secret"
    collector_max_skew_seconds: int = 300
    rate_limit_enabled: bool = True
    max_json_body_bytes: int = 512 * 1024  # 512 KB

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
    max_images_per_submission: int = 20
    max_chat_nodes: int = 50
    max_chat_depth: int = 3
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

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("production", "prod")

    @property
    def effective_r2_private_bucket(self) -> str:
        return self.r2_private_bucket_name.strip() or self.r2_bucket_name

    @property
    def effective_r2_public_bucket(self) -> str:
        return self.r2_public_bucket_name.strip() or self.r2_bucket_name

    def validate_production_security(self) -> None:
        """Fail fast at startup if insecure default secrets are active in production."""
        if not self.is_production:
            return
        insecure: list[str] = []
        if (
            self.jwt_secret == "dev-jwt-secret-change-in-production-32bytes"
            or len(self.jwt_secret) < 32
        ):
            insecure.append("JWT_SECRET (>=32 random chars required)")
        if (
            self.hmac_salt == "dev-hmac-salt-change-in-production"
            or len(self.hmac_salt) < 16
        ):
            insecure.append("HMAC_SALT (>=16 random chars required)")
        if (
            self.collector_hmac_secret == "dev-collector-hmac-secret"
            or len(self.collector_hmac_secret) < 16
        ):
            insecure.append("COLLECTOR_HMAC_SECRET (>=16 random chars required)")
        if self.admin_password != "" and (
            self.admin_password == "admin123456" or len(self.admin_password) < 12
        ):
            insecure.append("ADMIN_PASSWORD (must not be default and >=12 chars, or empty to disable auto-seed)")
        if insecure:
            raise RuntimeError(
                "Refusing to start in APP_ENV=production with insecure settings: "
                + ", ".join(insecure)
            )

    def ensure_dirs(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        if self.storage_backend == "local":
            base = Path(self.local_storage_dir)
            (base / "private").mkdir(parents=True, exist_ok=True)
            (base / "public").mkdir(parents=True, exist_ok=True)
