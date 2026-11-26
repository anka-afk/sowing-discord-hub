from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#([^\s#]{1,20})")
_CQ_TAG_RE = re.compile(r"\[CQ:([a-zA-Z0-9_-]+)(?:,([^\]]*))?\]")

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v")


class BlockedContentError(ValueError):
    """Raised when incoming message contains disallowed content such as local video."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class PendingImageRef:
    url: str
    file_hint: str | None = None
    is_gif_hint: bool = False


@dataclass
class DesensitizedMessage:
    """Intermediate desensitized payload before media upload replaces PendingImageRef with media_id."""

    title: str | None = None
    tags: list[str] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    pending_images: list[tuple[dict[str, Any], PendingImageRef]] = field(default_factory=list)
    plain_texts: list[str] = field(default_factory=list)
    submitter_hash: str = ""
    origin_label: str = "官方投稿群"

    def combined_text(self) -> str:
        return "\n".join(t for t in self.plain_texts if t).strip()


def compute_submitter_hash(salt: str, group_id: int | str, user_id: int | str) -> str:
    msg = f"{group_id}:{user_id}".encode("utf-8")
    return hmac.new(salt.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _index_to_letters(idx: int) -> str:
    """0 -> A, 1 -> B, ..., 25 -> Z, 26 -> AA."""
    chars: list[str] = []
    n = idx
    while True:
        chars.append(chr(ord("A") + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "".join(reversed(chars))


class AliasMapper:
    """Maps real QQ user IDs / nicknames to deterministic anonymous labels: 群友A, 群友B, ..."""

    def __init__(self) -> None:
        self._map: dict[str, tuple[str, int]] = {}

    def get(self, raw_key: str | int | None) -> tuple[str, int]:
        key = str(raw_key) if raw_key not in (None, "") else f"anon_{len(self._map)}"
        if key not in self._map:
            idx = len(self._map)
            alias = f"群友{_index_to_letters(idx)}"
            color_idx = idx % 8
            self._map[key] = (alias, color_idx)
        return self._map[key]


def parse_cq_string(raw: str) -> list[dict[str, Any]]:
    """Convert raw CQ-code string into OneBot v11 array-of-segments format."""
    segments: list[dict[str, Any]] = []
    pos = 0
    for match in _CQ_TAG_RE.finditer(raw):
        if match.start() > pos:
            segments.append({"type": "text", "data": {"text": raw[pos : match.start()]}})
        seg_type = match.group(1)
        params_raw = match.group(2) or ""
        data: dict[str, str] = {}
        if params_raw:
            for item in params_raw.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    data[k.strip()] = v.strip()
        segments.append({"type": seg_type, "data": data})
        pos = match.end()
    if pos < len(raw):
        segments.append({"type": "text", "data": {"text": raw[pos:]}})
    return segments


def normalize_segments(message: Any) -> list[dict[str, Any]]:
    if isinstance(message, list):
        return [s for s in message if isinstance(s, dict)]
    if isinstance(message, str):
        return parse_cq_string(message)
    return []


def detect_platform(host: str) -> str:
    h = host.lower()
    if "bilibili.com" in h or "b23.tv" in h:
        return "bilibili"
    if "youtube.com" in h or "youtu.be" in h:
        return "youtube"
    if "douyin.com" in h:
        return "douyin"
    if "tiktok.com" in h:
        return "tiktok"
    if "x.com" in h or "twitter.com" in h:
        return "x"
    return "other"


def extract_whitelisted_video_links(text: str, whitelist: list[str]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,!?;:)]}>")
        if url in seen:
            continue
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if any(host == domain or host.endswith(f".{domain}") for domain in whitelist):
            seen.add(url)
            links.append(
                {
                    "type": "video_link",
                    "url": url,
                    "platform": detect_platform(host),
                    "title": "",
                }
            )
    return links


def strip_hashtag_prefix(
    segments: list[dict[str, Any]],
    prefixes: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Strip leading #投稿 prefix and extract optional inline #tags from leading text."""
    extracted_tags: list[str] = []
    cleaned: list[dict[str, Any]] = []
    stripped_prefix = False

    for seg in segments:
        if not stripped_prefix and seg.get("type") == "text":
            text = str(seg.get("data", {}).get("text", ""))
            ltext = text.lstrip()
            for prefix in prefixes:
                if ltext.startswith(prefix):
                    rest = ltext[len(prefix) :].lstrip()
                    # Pull leading #tag tokens (excluding the trigger prefix itself)
                    while rest.startswith("#"):
                        m = _HASHTAG_RE.match(rest)
                        if not m:
                            break
                        tag = m.group(1)
                        if tag and tag != prefix.lstrip("#") and tag not in extracted_tags:
                            extracted_tags.append(tag)
                        rest = rest[m.end() :].lstrip()
                    stripped_prefix = True
                    if rest:
                        cleaned.append({"type": "text", "data": {"text": rest}})
                    break
            else:
                cleaned.append(seg)
        else:
            cleaned.append(seg)
    return cleaned, extracted_tags


def _format_time_label(ts: Any) -> str:
    if isinstance(ts, (int, float)) and ts > 0:
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%H:%M")
        except Exception:
            return ""
    if isinstance(ts, str) and len(ts) <= 16:
        return ts
    return ""


def flatten_forward_nodes(
    nodes: list[dict[str, Any]],
    *,
    alias_mapper: AliasMapper,
    pending_images: list[tuple[dict[str, Any], PendingImageRef]],
    plain_texts: list[str],
    max_depth: int = 1,
    max_nodes: int = 20,
    current_depth: int = 0,
) -> list[dict[str, Any]]:
    """Flatten OneBot v11 forward message nodes with hard max_depth=1 and max_nodes=20 limits."""
    messages: list[dict[str, Any]] = []

    for node in nodes:
        if len(messages) >= max_nodes:
            break
        if not isinstance(node, dict):
            continue
        data = node.get("data", node)
        if not isinstance(data, dict):
            continue

        sender_info = data.get("sender") if isinstance(data.get("sender"), dict) else {}
        raw_sender = (
            data.get("user_id")
            or data.get("uin")
            or sender_info.get("user_id")
            or data.get("nickname")
            or data.get("name")
            or sender_info.get("nickname")
        )
        alias, color_idx = alias_mapper.get(raw_sender)
        time_label = _format_time_label(data.get("time"))

        raw_content = data.get("content") if "content" in data else data.get("message", [])
        segs = normalize_segments(raw_content)

        content_parts: list[dict[str, Any]] = []
        for seg in segs:
            stype = seg.get("type", "")
            sdata = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}

            if stype in ("video", "short_video"):
                raise BlockedContentError("video_file_not_allowed", "Video segment found inside forward chat record.")

            if stype == "text":
                txt = str(sdata.get("text", "")).strip()
                if txt:
                    content_parts.append({"type": "text", "text": txt})
                    plain_texts.append(txt)
            elif stype == "at":
                qq = sdata.get("qq")
                if qq and str(qq) != "all":
                    at_alias, _ = alias_mapper.get(qq)
                    content_parts.append({"type": "text", "text": f"@{at_alias}"})
                else:
                    content_parts.append({"type": "text", "text": "@全体成员"})
            elif stype == "image":
                url = str(sdata.get("url") or sdata.get("file") or "").strip()
                if any(url.lower().endswith(ext) for ext in _VIDEO_EXTENSIONS):
                    raise BlockedContentError("video_file_not_allowed", "Video URL disguised as image segment.")
                if url.startswith("http://") or url.startswith("https://"):
                    img_block: dict[str, Any] = {"type": "image"}
                    content_parts.append(img_block)
                    pending_images.append(
                        (
                            img_block,
                            PendingImageRef(
                                url=url,
                                file_hint=str(sdata.get("file") or ""),
                                is_gif_hint=".gif" in url.lower() or ".gif" in str(sdata.get("file") or "").lower(),
                            ),
                        )
                    )
            elif stype == "forward":
                nested = sdata.get("content") or sdata.get("messages")
                if current_depth < max_depth and isinstance(nested, list):
                    remaining = max_nodes - len(messages)
                    if remaining > 0:
                        flattened_children = flatten_forward_nodes(
                            nested,
                            alias_mapper=alias_mapper,
                            pending_images=pending_images,
                            plain_texts=plain_texts,
                            max_depth=max_depth,
                            max_nodes=remaining,
                            current_depth=current_depth + 1,
                        )
                        if content_parts:
                            messages.append(
                                {
                                    "sender_alias": alias,
                                    "avatar_color_idx": color_idx,
                                    "time_label": time_label,
                                    "content": content_parts,
                                }
                            )
                            content_parts = []
                        for child in flattened_children:
                            if len(messages) >= max_nodes:
                                break
                            messages.append(child)
                else:
                    content_parts.append({"type": "text", "text": "[嵌套聊天记录已折叠]"})

        if content_parts and len(messages) < max_nodes:
            messages.append(
                {
                    "sender_alias": alias,
                    "avatar_color_idx": color_idx,
                    "time_label": time_label,
                    "content": content_parts,
                }
            )

    return messages


def desensitize_onebot_message(
    segments: list[dict[str, Any]],
    *,
    group_id: int,
    sender_user_id: int | str,
    submitter_salt: str,
    forward_nodes_map: dict[str, list[dict[str, Any]]] | None = None,
    video_link_whitelist: list[str] | None = None,
    max_chat_depth: int = 1,
    max_chat_nodes: int = 20,
    extra_tags: list[str] | None = None,
    origin_label: str = "官方投稿群",
) -> DesensitizedMessage:
    """Convert raw OneBot v11 message segments into desensitized protocol blocks."""
    result = DesensitizedMessage(
        tags=list(extra_tags or []),
        submitter_hash=compute_submitter_hash(submitter_salt, group_id, sender_user_id),
        origin_label=origin_label,
    )
    alias_mapper = AliasMapper()
    whitelist = video_link_whitelist or []
    forward_nodes_map = forward_nodes_map or {}

    text_chunks: list[str] = []

    def _flush_text() -> None:
        joined = "".join(text_chunks).strip()
        text_chunks.clear()
        if not joined:
            return
        result.plain_texts.append(joined)
        video_links = extract_whitelisted_video_links(joined, whitelist)
        if video_links:
            # Keep surrounding commentary text if any remains besides the URLs
            remainder = joined
            for vl in video_links:
                remainder = remainder.replace(vl["url"], "").strip()
            if remainder:
                result.blocks.append({"type": "text", "text": remainder})
            result.blocks.extend(video_links)
        else:
            result.blocks.append({"type": "text", "text": joined})

    for seg in segments:
        stype = seg.get("type", "")
        sdata = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}

        if stype in ("video", "short_video"):
            raise BlockedContentError("video_file_not_allowed", "Local video submissions are forbidden in MVP.")

        if stype == "text":
            text_chunks.append(str(sdata.get("text", "")))
        elif stype == "at":
            qq = sdata.get("qq")
            if qq and str(qq) != "all":
                alias, _ = alias_mapper.get(qq)
                text_chunks.append(f"@{alias} ")
            else:
                text_chunks.append("@全体成员 ")
        elif stype == "reply":
            # Reply metadata is handled by trigger resolver, never leaked into content
            continue
        elif stype == "image":
            _flush_text()
            url = str(sdata.get("url") or sdata.get("file") or "").strip()
            file_hint = str(sdata.get("file") or "")
            if any(url.lower().endswith(ext) for ext in _VIDEO_EXTENSIONS):
                raise BlockedContentError("video_file_not_allowed", "Video URL is forbidden.")
            if not (url.startswith("http://") or url.startswith("https://")):
                continue
            is_gif = ".gif" in url.lower() or ".gif" in file_hint.lower()
            img_block: dict[str, Any] = {"type": "gif" if is_gif else "image"}
            result.blocks.append(img_block)
            result.pending_images.append(
                (
                    img_block,
                    PendingImageRef(url=url, file_hint=file_hint, is_gif_hint=is_gif),
                )
            )
        elif stype == "forward":
            _flush_text()
            fid = str(sdata.get("id") or "")
            raw_nodes = forward_nodes_map.get(fid) or sdata.get("content") or sdata.get("messages") or []
            if isinstance(raw_nodes, list) and raw_nodes:
                flat_msgs = flatten_forward_nodes(
                    raw_nodes,
                    alias_mapper=alias_mapper,
                    pending_images=result.pending_images,
                    plain_texts=result.plain_texts,
                    max_depth=max_chat_depth,
                    max_nodes=max_chat_nodes,
                )
                if flat_msgs:
                    result.blocks.append({"type": "chat_record", "messages": flat_msgs})

    _flush_text()
    return result
