from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.config import Settings
from app.security import is_allowed_video_link, sha256_hex


def compute_wilson_score(likes: int, dislikes: int, z: float = 1.96) -> float:
    """Compute Wilson score lower bound scaled to [0, 100] for ranking."""
    n = likes + dislikes
    if n <= 0 or likes <= 0:
        return 0.0
    p = likes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    lower = max(0.0, (center - margin) / denom)
    return round(lower * 100.0, 2)


def _infer_video_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "bilibili" in host or "b23.tv" in host:
        return "bilibili"
    if "youtube" in host or "youtu.be" in host:
        return "youtube"
    if "douyin" in host:
        return "douyin"
    if "twitter" in host or host == "x.com" or host.endswith(".x.com"):
        return "x"
    if "acfun" in host:
        return "acfun"
    if "nicovideo" in host:
        return "niconico"
    if "tiktok" in host:
        return "tiktok"
    if "vimeo" in host:
        return "vimeo"
    return host or "other"


def _fetch_media_row(conn: sqlite3.Connection, media_id: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, item_id, sha256, phash, mime_type, r2_key, url, w, h, size_bytes, visibility, is_deleted
        FROM media_assets
        WHERE id = ?
        """,
        (media_id,),
    ).fetchone()
    if row is None or int(row["is_deleted"]) == 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Referenced media_id not found or deleted: {media_id}",
        )
    return row


def validate_and_normalize_blocks(
    raw_blocks: list[dict[str, Any]],
    conn: sqlite3.Connection,
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[str], str, str]:
    """Validate submission blocks against Schema v1.0 & security rules.

    Returns: (normalized_blocks, referenced_media_ids, content_type, content_hash)
    """
    if not raw_blocks:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="blocks must not be empty",
        )

    normalized: list[dict[str, Any]] = []
    media_ids: list[str] = []
    hash_parts: list[str] = []
    image_count = 0
    block_types: list[str] = []

    for idx, blk in enumerate(raw_blocks):
        if not isinstance(blk, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Block #{idx} must be an object",
            )
        btype = blk.get("type")
        if btype == "video_file":
            if not settings.capability_video_file:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="video_file blocks are disabled in MVP (capabilities.video_file=false)",
                )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="video_file not supported yet",
            )

        if btype == "text":
            text = str(blk.get("text", "")).strip()
            if not text:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Block #{idx} text must not be empty",
                )
            if len(text) > 5000:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Block #{idx} text exceeds 5000 chars",
                )
            normalized.append({"type": "text", "text": text})
            hash_parts.append(f"text:{text}")
            block_types.append("text")

        elif btype in ("image", "gif"):
            media_id = blk.get("media_id")
            if not media_id or not isinstance(media_id, str):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Block #{idx} ({btype}) requires media_id (external URLs are forbidden to prevent SSRF)",
                )
            image_count += 1
            if image_count > settings.max_images_per_submission:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Too many images: maximum {settings.max_images_per_submission} per submission",
                )
            mrow = _fetch_media_row(conn, media_id)
            mime = mrow["mime_type"]
            if btype == "gif" and mime != "image/gif":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Block #{idx} declared gif but media {media_id} is {mime}",
                )
            actual_type = "gif" if mime == "image/gif" else "image"
            media_ids.append(media_id)
            hash_parts.append(f"{actual_type}:{mrow['sha256']}")
            normalized.append(
                {
                    "type": actual_type,
                    "media_id": media_id,
                    "url": mrow["url"] or f"/api/v1/admin/media/{media_id}",
                    "mime": mime,
                    "w": int(mrow["w"]),
                    "h": int(mrow["h"]),
                    "size_bytes": int(mrow["size_bytes"]),
                }
            )
            block_types.append(actual_type)

        elif btype == "chat_record":
            raw_msgs = blk.get("messages")
            if not isinstance(raw_msgs, list) or not raw_msgs:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Block #{idx} chat_record requires non-empty messages list",
                )
            if len(raw_msgs) > settings.max_chat_nodes:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"chat_record exceeds max_chat_nodes ({settings.max_chat_nodes})",
                )
            norm_msgs: list[dict[str, Any]] = []
            for midx, msg in enumerate(raw_msgs):
                if not isinstance(msg, dict):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"chat_record message #{midx} must be an object",
                    )
                alias = str(msg.get("sender_alias", "")).strip() or f"群友{chr(65 + (midx % 26))}"
                alias = alias[:64]
                color_idx = int(msg.get("avatar_color_idx", midx % 8))
                if color_idx < 0:
                    color_idx = 0
                time_label = str(msg.get("time_label", "") or "")[:32]
                raw_content = msg.get("content")
                if not isinstance(raw_content, list) or not raw_content:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"chat_record message #{midx} requires non-empty content list",
                    )
                norm_content: list[dict[str, Any]] = []
                for seg in raw_content:
                    if not isinstance(seg, dict):
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Invalid segment in chat_record message #{midx}",
                        )
                    stype = seg.get("type")
                    if stype == "chat_record":
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Nested chat_record forbidden (max_chat_depth={settings.max_chat_depth})",
                        )
                    if stype == "text":
                        stext = str(seg.get("text", "")).strip()
                        if stext:
                            norm_content.append({"type": "text", "text": stext[:2000]})
                            hash_parts.append(f"chat:{alias}:{stext}")
                    elif stype in ("image", "gif"):
                        cmid = seg.get("media_id")
                        if not cmid or not isinstance(cmid, str):
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"chat_record inline image requires media_id (no external URLs)",
                            )
                        image_count += 1
                        if image_count > settings.max_images_per_submission:
                            raise HTTPException(
                                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Too many images: maximum {settings.max_images_per_submission} per submission",
                            )
                        mrow = _fetch_media_row(conn, cmid)
                        media_ids.append(cmid)
                        hash_parts.append(f"chat_img:{mrow['sha256']}")
                        norm_content.append(
                            {
                                "type": "gif" if mrow["mime_type"] == "image/gif" else "image",
                                "media_id": cmid,
                                "url": mrow["url"] or f"/api/v1/admin/media/{cmid}",
                                "mime": mrow["mime_type"],
                                "w": int(mrow["w"]),
                                "h": int(mrow["h"]),
                                "size_bytes": int(mrow["size_bytes"]),
                            }
                        )
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Unsupported chat_record content segment type: {stype}",
                        )
                if not norm_content:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"chat_record message #{midx} has no valid content",
                    )
                norm_msgs.append(
                    {
                        "sender_alias": alias,
                        "avatar_color_idx": color_idx,
                        "time_label": time_label,
                        "content": norm_content,
                    }
                )
            normalized.append(
                {
                    "type": "chat_record",
                    "rendered_images": [],
                    "messages": norm_msgs,
                }
            )
            block_types.append("chat_record")

        elif btype == "video_link":
            vurl = str(blk.get("url", "")).strip()
            if not vurl or not is_allowed_video_link(vurl, settings):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Block #{idx} video_link URL is not in the allowed domain whitelist",
                )
            platform = str(blk.get("platform") or "").strip() or _infer_video_platform(vurl)
            vtitle = str(blk.get("title") or "").strip()[:200]
            duration_sec = blk.get("duration_sec")
            if duration_sec is not None:
                try:
                    duration_sec = max(0, int(duration_sec))
                except (TypeError, ValueError):
                    duration_sec = None

            cover_media_id = blk.get("cover_media_id")
            cover_url = ""
            if cover_media_id:
                mrow = _fetch_media_row(conn, str(cover_media_id))
                media_ids.append(str(cover_media_id))
                cover_url = mrow["url"] or f"/api/v1/admin/media/{cover_media_id}"
            elif blk.get("cover_url"):
                # Only allow cover_url already hosted by our public media base (no SSRF/hotlinking)
                raw_cover = str(blk.get("cover_url")).strip()
                if raw_cover.startswith(settings.public_media_base_url.rstrip("/")):
                    cover_url = raw_cover
                else:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="External cover_url is forbidden; upload cover via /submissions/media and pass cover_media_id",
                    )

            vblock: dict[str, Any] = {
                "type": "video_link",
                "url": vurl,
                "platform": platform,
                "title": vtitle,
                "cover_url": cover_url,
            }
            if cover_media_id:
                vblock["cover_media_id"] = str(cover_media_id)
            if duration_sec is not None:
                vblock["duration_sec"] = duration_sec
            normalized.append(vblock)
            hash_parts.append(f"video_link:{vurl}")
            block_types.append("video_link")

        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported block type: {btype}",
            )

    if "chat_record" in block_types:
        content_type = "chat_record"
    elif "video_link" in block_types and len(set(block_types)) == 1:
        content_type = "video_link"
    elif len(set(block_types)) == 1:
        content_type = block_types[0]
    else:
        content_type = "mixed"

    content_hash = sha256_hex("|".join(hash_parts))
    return normalized, list(dict.fromkeys(media_ids)), content_type, content_hash


def extract_plain_texts(title: str, blocks: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    if title:
        parts.append(title.strip())
    for blk in blocks:
        btype = blk.get("type")
        if btype == "text" and blk.get("text"):
            parts.append(str(blk["text"]).strip())
        elif btype == "video_link" and blk.get("title"):
            parts.append(str(blk["title"]).strip())
        elif btype == "chat_record":
            for msg in blk.get("messages") or []:
                alias = str(msg.get("sender_alias") or "").strip()
                msg_texts = [
                    str(seg.get("text", "")).strip()
                    for seg in (msg.get("content") or [])
                    if seg.get("type") == "text" and seg.get("text")
                ]
                if msg_texts:
                    joined = " ".join(msg_texts)
                    parts.append(f"{alias}: {joined}" if alias else joined)
                elif alias:
                    parts.append(alias)
    return [p for p in parts if p]


def build_auto_summary(title: str, blocks: list[dict[str, Any]], max_len: int = 120) -> str:
    texts = extract_plain_texts(title, blocks)
    if not texts:
        # Fallback label by block type
        types = {b.get("type") for b in blocks}
        if "gif" in types:
            return "[动图投稿]"
        if "image" in types:
            return "[图片投稿]"
        if "chat_record" in types:
            return "[聊天记录]"
        if "video_link" in types:
            return "[视频分享]"
        return ""
    combined = re.sub(r"\s+", " ", " | ".join(texts)).strip()
    if len(combined) <= max_len:
        return combined
    return combined[:max_len]


def serialize_meme_item(
    row: sqlite3.Row,
    settings: Settings,
    *,
    for_admin: bool = False,
) -> dict[str, Any]:
    payload = json.loads(row["payload_json"])
    raw_blocks = payload.get("blocks", [])
    attribution = payload.get("attribution") or {
        "submitter_name": "匿名网友",
        "origin_label": "群友投稿",
    }
    tags = json.loads(row["tags_json"] or "[]")

    blocks: list[dict[str, Any]] = []
    for blk in raw_blocks:
        b = dict(blk)
        btype = b.get("type")
        if not for_admin:
            # Strip internal media_id keys from public Schema v1.0 output
            b.pop("media_id", None)
            b.pop("cover_media_id", None)
            if btype == "chat_record":
                rendered = []
                for ri in b.get("rendered_images") or []:
                    rcopy = dict(ri)
                    rcopy.pop("media_id", None)
                    rendered.append(rcopy)
                b["rendered_images"] = rendered
                msgs = []
                for m in b.get("messages") or []:
                    mcopy = dict(m)
                    new_content = []
                    for seg in m.get("content") or []:
                        scopy = dict(seg)
                        scopy.pop("media_id", None)
                        new_content.append(scopy)
                    mcopy["content"] = new_content
                    msgs.append(mcopy)
                b["messages"] = msgs
        blocks.append(b)

    web_base = settings.web_base_url.rstrip("/")
    result: dict[str, Any] = {
        "schema_version": settings.schema_version,
        "id": row["id"],
        "seq": row["seq"],
        "hub_id": settings.hub_id,
        "title": row["title"],
        "summary": row["summary"],
        "tags": tags,
        "nsfw_level": int(row["nsfw_level"]),
        "score": float(row["score"]),
        "stats": {
            "likes": int(row["likes"]),
            "dislikes": int(row["dislikes"]),
        },
        "attribution": {
            "submitter_name": attribution.get("submitter_name", "匿名网友"),
            "origin_label": attribution.get("origin_label", "群友投稿"),
        },
        "web_url": f"{web_base}/m/{row['id']}",
        "published_at": row["published_at"],
        "blocks": blocks,
    }
    if for_admin:
        result["status"] = row["status"]
        result["content_type"] = row["content_type"]
        result["source_type"] = row["source_type"]
        result["reports"] = int(row["reports"])
        result["reject_reason"] = row["reject_reason"]
        result["created_at"] = row["created_at"]
    return result


def get_submitter_stats(conn: sqlite3.Connection, submitter_hash: str) -> dict[str, Any]:
    if not submitter_hash:
        return {
            "trust_level": 0,
            "approved_cnt": 0,
            "rejected_cnt": 0,
            "approval_rate": None,
        }
    row = conn.execute(
        """
        SELECT trust_level, approved_cnt, rejected_cnt, muted_until
        FROM submitter_reputation
        WHERE submitter_hash = ?
        """,
        (submitter_hash,),
    ).fetchone()
    if row is None:
        return {
            "trust_level": 0,
            "approved_cnt": 0,
            "rejected_cnt": 0,
            "approval_rate": None,
        }
    approved = int(row["approved_cnt"])
    rejected = int(row["rejected_cnt"])
    total = approved + rejected
    rate = round(approved / total, 4) if total > 0 else None
    return {
        "trust_level": int(row["trust_level"]),
        "approved_cnt": approved,
        "rejected_cnt": rejected,
        "approval_rate": rate,
    }
