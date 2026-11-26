from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import TriggerConfig
from .desensitizer import normalize_segments, strip_hashtag_prefix


@dataclass
class TriggerCandidate:
    """Represents a message nominated for screening & ingestion."""

    trigger_source: str  # "emoji_like" | "essence" | "hashtag"
    group_id: int
    message_id: str
    sender_user_id: int | str = 0
    # Present immediately for inline #投稿 messages; None when get_msg fetch is required
    inline_segments: list[dict[str, Any]] | None = None
    extra_tags: list[str] = field(default_factory=list)


class TriggerEngine:
    """Evaluates OneBot v11 events against emoji_like, essence, and #投稿 triggers."""

    def __init__(self, config: TriggerConfig, watch_groups: list[int] | None = None) -> None:
        self.config = config
        self.watch_groups: set[int] = set(watch_groups or [])
        # (group_id, message_id) -> {emoji_id: count}
        self._emoji_counts: dict[tuple[int, str], dict[str, int]] = {}

    def _is_watched_group(self, group_id: int | None) -> bool:
        if group_id is None:
            return False
        if not self.watch_groups:
            return True
        return int(group_id) in self.watch_groups

    def process_event(self, event: dict[str, Any]) -> TriggerCandidate | None:
        post_type = event.get("post_type")
        if post_type == "notice":
            return self._handle_notice(event)
        if post_type == "message" and event.get("message_type") == "group":
            return self._handle_group_message(event)
        return None

    def _handle_notice(self, event: dict[str, Any]) -> TriggerCandidate | None:
        notice_type = event.get("notice_type")
        group_id = event.get("group_id")
        if not self._is_watched_group(group_id):
            return None
        gid = int(group_id)

        # 1. Essence message event
        if notice_type == "essence" and self.config.enable_essence:
            if event.get("sub_type", "add") != "add":
                return None
            msg_id = event.get("message_id")
            if msg_id is None:
                return None
            return TriggerCandidate(
                trigger_source="essence",
                group_id=gid,
                message_id=str(msg_id),
                sender_user_id=event.get("sender_id") or event.get("operator_id") or 0,
            )

        # 2. Group emoji reaction event (NapCat / LLOneBot / Lagrange)
        if notice_type == "group_msg_emoji_like":
            msg_id = event.get("message_id")
            if msg_id is None:
                return None
            key = (gid, str(msg_id))
            counts = self._emoji_counts.setdefault(key, {})

            is_add = event.get("is_add", True)
            likes = event.get("likes")
            if isinstance(likes, list):
                for item in likes:
                    if not isinstance(item, dict):
                        continue
                    emoji_id = str(item.get("emoji_id", "default"))
                    if "count" in item and isinstance(item["count"], int):
                        # NapCat reports current absolute count for this emoji_id
                        counts[emoji_id] = max(0, int(item["count"]))
                    else:
                        delta = 1 if is_add else -1
                        counts[emoji_id] = max(0, counts.get(emoji_id, 0) + delta)
            else:
                emoji_id = str(event.get("emoji_id", "default"))
                delta = 1 if is_add else -1
                counts[emoji_id] = max(0, counts.get(emoji_id, 0) + delta)

            net_likes = sum(counts.values())
            if net_likes >= self.config.emoji_like_threshold:
                return TriggerCandidate(
                    trigger_source="emoji_like",
                    group_id=gid,
                    message_id=str(msg_id),
                    sender_user_id=event.get("user_id") or 0,
                )

        return None

    def _handle_group_message(self, event: dict[str, Any]) -> TriggerCandidate | None:
        if not self.config.enable_hashtag:
            return None
        group_id = event.get("group_id")
        if not self._is_watched_group(group_id):
            return None
        gid = int(group_id)
        msg_id = event.get("message_id")
        if msg_id is None:
            return None

        segments = normalize_segments(event.get("message") or event.get("raw_message") or [])
        if not segments:
            return None

        # Detect reply segment and first non-reply text segment
        reply_msg_id: str | None = None
        non_reply_segments: list[dict[str, Any]] = []
        for seg in segments:
            if seg.get("type") == "reply":
                rid = seg.get("data", {}).get("id")
                if rid is not None:
                    reply_msg_id = str(rid)
            else:
                non_reply_segments.append(seg)

        # Check if first text segment starts with a configured hashtag prefix (e.g., #投稿)
        first_text = ""
        for seg in non_reply_segments:
            if seg.get("type") == "text":
                first_text = str(seg.get("data", {}).get("text", "")).lstrip()
                break
            elif seg.get("type") == "at":
                continue
            else:
                break

        if not any(first_text.startswith(p) for p in self.config.hashtag_prefixes):
            return None

        cleaned_segments, extra_tags = strip_hashtag_prefix(non_reply_segments, self.config.hashtag_prefixes)
        sender_id = event.get("user_id") or 0

        # If replying to another message with just "#投稿 [#tag]", nominate the replied message
        has_inline_payload = any(
            (s.get("type") == "text" and str(s.get("data", {}).get("text", "")).strip())
            or s.get("type") in ("image", "forward", "video")
            for s in cleaned_segments
        )
        if reply_msg_id and not has_inline_payload:
            return TriggerCandidate(
                trigger_source="hashtag",
                group_id=gid,
                message_id=reply_msg_id,
                sender_user_id=sender_id,
                inline_segments=None,
                extra_tags=extra_tags,
            )

        return TriggerCandidate(
            trigger_source="hashtag",
            group_id=gid,
            message_id=str(msg_id),
            sender_user_id=sender_id,
            inline_segments=cleaned_segments,
            extra_tags=extra_tags,
        )
