from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --- Meta ---
class MetaCapabilities(BaseModel):
    video_file: bool = False
    feedback: bool = True


class MetaLimits(BaseModel):
    max_image_bytes: int
    max_gif_bytes: int
    max_images_per_submission: int
    max_chat_nodes: int
    max_chat_depth: int


class MetaResponse(BaseModel):
    hub_id: str
    hub_name: str
    schema_version: str = "1.0"
    capabilities: MetaCapabilities
    limits: MetaLimits
    tags: list[str]
    report_reasons: list[str] = Field(
        default_factory=lambda: [
            "nsfw_unmarked",
            "privacy_leak",
            "copyright",
            "broken_media",
            "duplicate",
            "other",
        ]
    )


# --- Instances / PoW ---
class ChallengeResponse(BaseModel):
    challenge_id: str
    nonce_prefix: str
    difficulty_bits: int
    expires_at: str


class InstanceRegisterRequest(BaseModel):
    challenge_id: str = Field(..., min_length=1, max_length=128)
    solution: str = Field(..., min_length=1, max_length=128)
    client_version: str = Field(default="unknown", max_length=64)


class InstanceRegisterResponse(BaseModel):
    instance_id: str
    instance_token: str


# --- Submissions ---
class AttributionInput(BaseModel):
    submitter_name: str = Field(default="匿名网友", max_length=64)
    origin_label: str = Field(default="社区投稿", max_length=64)


class MediaUploadResponse(BaseModel):
    media_id: str | None = None
    mime: str
    w: int
    h: int
    size_bytes: int
    preview_url: str | None = None
    duplicate: bool = False
    match_type: Literal["exact", "similar"] | None = None
    existing_status: Literal["approved", "pending"] | None = None
    existing_item_id: str | None = None


class SubmissionCreateRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=12)
    blocks: list[dict[str, Any]] = Field(..., min_length=1, max_length=30)
    turnstile_token: str | None = None
    attribution: AttributionInput | None = None


class SubmissionCreateResponse(BaseModel):
    submission_id: str
    status: Literal["pending"] = "pending"


class CollectorIngestRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=12)
    blocks: list[dict[str, Any]] = Field(..., min_length=1, max_length=30)
    submitter_hash: str | None = Field(default=None, max_length=128)
    source_group_hash: str | None = Field(default=None, max_length=128)
    source_msg_id: str | None = Field(default=None, max_length=128)
    attribution: AttributionInput | None = None


class CollectorIngestResponse(BaseModel):
    submission_id: str
    status: Literal["pending"] = "pending"
    duplicate: bool = False


# --- Feedback ---
VALID_REPORT_REASONS = {
    "nsfw_unmarked",
    "privacy_leak",
    "copyright",
    "broken_media",
    "duplicate",
    "other",
}


class FeedbackEventInput(BaseModel):
    item_id: str = Field(..., min_length=1, max_length=64)
    action: Literal["like", "dislike", "report"]
    reason: str | None = Field(default=None, max_length=64)
    voter_hash: str = Field(..., min_length=1, max_length=128)
    group_hash: str = Field(..., min_length=1, max_length=128)
    occurred_at: str = Field(..., min_length=10, max_length=64)


class FeedbackBatchRequest(BaseModel):
    events: list[FeedbackEventInput] = Field(..., min_length=1, max_length=50)


class FeedbackBatchResponse(BaseModel):
    accepted: int
    deduplicated: int
    capped: int
    ignored: int
    circuit_broken_items: list[str] = Field(default_factory=list)


# --- Admin ---
class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AdminInfo(BaseModel):
    id: str
    username: str
    role: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    admin: AdminInfo


class RenderedImageRef(BaseModel):
    media_id: str = Field(..., min_length=1, max_length=64)


class AdminReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=300)
    tags: list[str] | None = Field(default=None, max_length=12)
    nsfw_level: int | None = Field(default=None, ge=0, le=2)
    reject_reason: str | None = Field(default=None, max_length=200)
    rendered_images: list[RenderedImageRef | str] | None = None
    chat_renders: dict[str, list[str]] | None = None
    edited_chat_messages: list[dict[str, Any]] | None = None
    edited_text_blocks: dict[str, str] | None = None


class AdminReviewResponse(BaseModel):
    item_id: str
    status: str
    seq: int | None = None
    summary: str = ""
    published_at: str | None = None


class AdminTakedownRequest(BaseModel):
    reason: str = Field(default="admin_takedown", min_length=1, max_length=200)


class AdminTakedownResponse(BaseModel):
    item_id: str
    status: Literal["takedown"] = "takedown"
    tombstone_seq: int
