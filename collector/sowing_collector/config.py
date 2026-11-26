from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class HubConfig:
    base_url: str = "http://127.0.0.1:8000"
    shared_secret: str = "dev-collector-secret"
    submitter_salt: str = "collector-submitter-salt"
    timeout_sec: float = 20.0
    max_retries: int = 2


@dataclass
class OneBotConfig:
    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    watch_groups: list[int] = field(default_factory=list)
    reconnect_interval_sec: float = 5.0
    action_timeout_sec: float = 15.0


@dataclass
class TriggerConfig:
    emoji_like_threshold: int = 5
    enable_essence: bool = True
    enable_hashtag: bool = True
    hashtag_prefixes: list[str] = field(default_factory=lambda: ["#投稿"])


@dataclass
class FilterConfig:
    sensitive_words: list[str] = field(default_factory=list)
    sensitive_words_file: str = ""
    min_text_length: int = 2
    max_text_length: int = 4000
    max_image_bytes: int = 10_485_760
    max_gif_bytes: int = 5_242_880
    max_images_per_submission: int = 9
    max_chat_nodes: int = 20
    max_chat_depth: int = 1
    phash_hamming_threshold: int = 8
    video_link_whitelist: list[str] = field(
        default_factory=lambda: [
            "bilibili.com",
            "b23.tv",
            "youtube.com",
            "youtu.be",
            "douyin.com",
            "v.douyin.com",
            "tiktok.com",
            "x.com",
            "twitter.com",
        ]
    )


@dataclass
class StorageConfig:
    db_path: str = "./data/collector.db"


@dataclass
class CollectorConfig:
    hub: HubConfig = field(default_factory=HubConfig)
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    triggers: TriggerConfig = field(default_factory=TriggerConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    log_level: str = "INFO"

    def load_all_sensitive_words(self) -> list[str]:
        words = list(self.filters.sensitive_words)
        if self.filters.sensitive_words_file:
            p = Path(self.filters.sensitive_words_file)
            if p.is_file():
                for line in p.read_text(encoding="utf-8").splitlines():
                    cleaned = line.strip()
                    if cleaned and not cleaned.startswith("#"):
                        words.append(cleaned)
        return words


def _merge_dataclass(cls: type, data: dict[str, Any] | None) -> Any:
    if not data:
        return cls()
    valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return cls(**filtered)


def load_config(path: str | Path | None = None) -> CollectorConfig:
    raw: dict[str, Any] = {}
    if path is not None:
        cfg_path = Path(path)
        if cfg_path.is_file():
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw = loaded

    cfg = CollectorConfig(
        hub=_merge_dataclass(HubConfig, raw.get("hub")),
        onebot=_merge_dataclass(OneBotConfig, raw.get("onebot")),
        triggers=_merge_dataclass(TriggerConfig, raw.get("triggers")),
        filters=_merge_dataclass(FilterConfig, raw.get("filters")),
        storage=_merge_dataclass(StorageConfig, raw.get("storage")),
        log_level=str(raw.get("log_level", "INFO")).upper(),
    )

    # Environment variable overrides for container deployment
    if env_hub_url := os.getenv("HUB_BASE_URL"):
        cfg.hub.base_url = env_hub_url
    if env_secret := os.getenv("COLLECTOR_SHARED_SECRET"):
        cfg.hub.shared_secret = env_secret
    if env_salt := os.getenv("COLLECTOR_SUBMITTER_SALT"):
        cfg.hub.submitter_salt = env_salt
    if env_ws := os.getenv("ONEBOT_WS_URL"):
        cfg.onebot.ws_url = env_ws
    if env_token := os.getenv("ONEBOT_ACCESS_TOKEN"):
        cfg.onebot.access_token = env_token
    if env_groups := os.getenv("ONEBOT_WATCH_GROUPS"):
        cfg.onebot.watch_groups = [
            int(g.strip()) for g in env_groups.split(",") if g.strip().isdigit()
        ]
    if env_db := os.getenv("COLLECTOR_DB_PATH"):
        cfg.storage.db_path = env_db
    if env_log := os.getenv("LOG_LEVEL"):
        cfg.log_level = env_log.upper()

    return cfg
