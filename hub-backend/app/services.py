from __future__ import annotations

import json
import math
import posixpath
import re
import sqlite3
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import HTTPException, status

from app.config import Settings
from app.security import is_allowed_video_link, sha256_hex, sign_admin_media_url


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


_TRUSTED_MEDIA_SUFFIX_RE = re.compile(r"^[A-Za-z0-9/._-]+\.(?:webp|gif)$", re.IGNORECASE)


def _is_trusted_public_media_url(raw_url: str, public_media_base_url: str) -> bool:
    """Strictly verify URL matches (scheme, netloc) and normalized safe path under public_media_base_url."""
    if not raw_url or "\\" in raw_url or "\x00" in raw_url:
        return False
    try:
        parsed = urlparse(raw_url)
        base = urlparse(public_media_base_url.rstrip("/"))
    except Exception:
        return False
    if parsed.scheme not in ("http", "https") or parsed.scheme != base.scheme:
        return False
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False
    if not parsed.netloc or parsed.netloc.lower() != base.netloc.lower():
        return False
    if parsed.params or parsed.query or parsed.fragment:
        return False
    lower_path = parsed.path.lower()
    if "%2e" in lower_path or "%2f" in lower_path or "%5c" in lower_path or "%00" in lower_path:
        return False
    decoded_path = unquote(parsed.path)
    if "\\" in decoded_path or "\x00" in decoded_path:
        return False
    segments = decoded_path.split("/")
    if any(seg in (".", "..") for seg in segments):
        return False
    norm_path = posixpath.normpath(decoded_path)
    base_path = base.path.rstrip("/")
    prefix = f"{base_path}/" if base_path else "/"
    if not norm_path.startswith(prefix):
        return False
    rel = norm_path[len(prefix) :]
    if not rel or not _TRUSTED_MEDIA_SUFFIX_RE.fullmatch(rel):
        return False
    return True


def _fetch_media_row(
    conn: sqlite3.Connection,
    media_id: str,
    *,
    require_unattached: bool = False,
    uploader_hash: str | None = None,
) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT id, item_id, sha256, phash, mime_type, r2_key, url, w, h, size_bytes,
               visibility, uploader_hash, is_deleted
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
    if require_unattached and row["item_id"] is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Media asset {media_id} is already attached to another item",
        )
    if uploader_hash is not None and row["uploader_hash"] != uploader_hash:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Media asset {media_id} does not belong to this submitter",
        )
    return row


def validate_and_normalize_blocks(
    raw_blocks: list[dict[str, Any]],
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    require_unattached: bool = True,
    uploader_hash: str | None = None,
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
    chat_node_counter = [0]
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
            mrow = _fetch_media_row(
                conn,
                media_id,
                require_unattached=require_unattached,
                uploader_hash=uploader_hash,
            )
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
            img_counter = [image_count]
            norm_msgs = _normalize_chat_messages(
                raw_msgs,
                conn,
                settings,
                depth=0,
                node_counter=chat_node_counter,
                img_counter=img_counter,
                media_ids=media_ids,
                hash_parts=hash_parts,
                require_unattached=require_unattached,
                uploader_hash=uploader_hash,
            )
            image_count = img_counter[0]
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
                image_count += 1
                if image_count > settings.max_images_per_submission:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Too many images: maximum {settings.max_images_per_submission} per submission",
                    )
                mrow = _fetch_media_row(
                    conn,
                    str(cover_media_id),
                    require_unattached=require_unattached,
                    uploader_hash=uploader_hash,
                )
                media_ids.append(str(cover_media_id))
                cover_url = mrow["url"] or f"/api/v1/admin/media/{cover_media_id}"
            elif blk.get("cover_url"):
                # Strictly match exact (scheme, netloc) and path boundary of public_media_base_url
                raw_cover = str(blk.get("cover_url")).strip()
                if _is_trusted_public_media_url(raw_cover, settings.public_media_base_url):
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


def _normalize_chat_messages(
    raw_msgs: list[Any],
    conn: sqlite3.Connection,
    settings: Settings,
    *,
    depth: int,
    node_counter: list[int],
    img_counter: list[int],
    media_ids: list[str],
    hash_parts: list[str],
    require_unattached: bool = False,
    uploader_hash: str | None = None,
) -> list[dict[str, Any]]:
    norm_msgs: list[dict[str, Any]] = []
    for midx, msg in enumerate(raw_msgs):
        node_counter[0] += 1
        if node_counter[0] > settings.max_chat_nodes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"chat_record exceeds max_chat_nodes ({settings.max_chat_nodes})",
            )
        if not isinstance(msg, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"chat_record message #{midx} must be an object",
            )
        alias = str(msg.get("sender_alias", "")).strip() or f"群友{chr(65 + (midx % 26))}"
        alias = alias[:64]
        raw_color = msg.get("avatar_color_idx", midx % 8)
        if isinstance(raw_color, bool):
            color_idx = midx % 8
        else:
            try:
                color_idx = int(raw_color)
            except (TypeError, ValueError):
                color_idx = midx % 8
        if color_idx < 0:
            color_idx = 0
        time_label = str(msg.get("time_label", "") or "")[:32]
        raw_content = msg.get("content")
        if not isinstance(raw_content, list) or not raw_content:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"chat_record message #{midx} requires non-empty content list",
            )
        if len(raw_content) > 20:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"chat_record message #{midx} exceeds maximum 20 content segments",
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
                    detail=f"Nested chat_record forbidden; use forward_quote (max_chat_depth={settings.max_chat_depth})",
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
                        detail="chat_record inline image requires media_id (no external URLs)",
                    )
                img_counter[0] += 1
                if img_counter[0] > settings.max_images_per_submission:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Too many images: maximum {settings.max_images_per_submission} per submission",
                    )
                mrow = _fetch_media_row(
                    conn,
                    cmid,
                    require_unattached=require_unattached,
                    uploader_hash=uploader_hash,
                )
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
            elif stype == "forward_quote":
                nested_msgs = seg.get("messages")
                if not isinstance(nested_msgs, list) or not nested_msgs:
                    continue
                if depth + 1 >= settings.max_chat_depth:
                    norm_content.append({"type": "text", "text": "[嵌套聊天记录已达上限]"})
                    continue
                qtitle = str(seg.get("title") or "聊天记录").strip()[:80]
                norm_nested = _normalize_chat_messages(
                    nested_msgs,
                    conn,
                    settings,
                    depth=depth + 1,
                    node_counter=node_counter,
                    img_counter=img_counter,
                    media_ids=media_ids,
                    hash_parts=hash_parts,
                    require_unattached=require_unattached,
                    uploader_hash=uploader_hash,
                )
                if norm_nested:
                    norm_content.append(
                        {
                            "type": "forward_quote",
                            "title": qtitle,
                            "messages": norm_nested,
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
    return norm_msgs


def _extract_chat_plain_texts(messages: list[dict[str, Any]], parts: list[str]) -> None:
    for msg in messages:
        alias = str(msg.get("sender_alias") or "").strip()
        msg_texts: list[str] = []
        nested_quotes: list[list[dict[str, Any]]] = []
        for seg in msg.get("content") or []:
            if seg.get("type") == "text" and seg.get("text"):
                msg_texts.append(str(seg.get("text", "")).strip())
            elif seg.get("type") == "forward_quote":
                qtitle = str(seg.get("title") or "").strip()
                if qtitle:
                    parts.append(qtitle)
                if isinstance(seg.get("messages"), list):
                    nested_quotes.append(seg["messages"])
        if msg_texts:
            joined = " ".join(msg_texts)
            parts.append(f"{alias}: {joined}" if alias else joined)
        elif alias and not nested_quotes:
            parts.append(alias)
        for nq in nested_quotes:
            _extract_chat_plain_texts(nq, parts)


def extract_plain_texts(
    title: str,
    blocks: list[dict[str, Any]],
    attribution: dict[str, Any] | None = None,
) -> list[str]:
    parts: list[str] = []
    if title:
        parts.append(title.strip())
    if attribution:
        sname = str(attribution.get("submitter_name") or "").strip()
        if sname and sname not in ("匿名网友", "投稿群精选"):
            parts.append(sname)
    for blk in blocks:
        btype = blk.get("type")
        if btype == "text" and blk.get("text"):
            parts.append(str(blk["text"]).strip())
        elif btype == "video_link" and blk.get("title"):
            parts.append(str(blk["title"]).strip())
        elif btype == "chat_record":
            _extract_chat_plain_texts(blk.get("messages") or [], parts)
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


def _sign_admin_url_if_private(url: str | None, media_id: str | None, settings: Settings) -> str | None:
    if media_id and (not url or url.startswith("/api/v1/admin/media/")):
        return sign_admin_media_url(media_id, settings)
    return url


def _sign_chat_admin_media_urls(messages: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for m in messages:
        mcopy = dict(m)
        new_content: list[dict[str, Any]] = []
        for seg in m.get("content") or []:
            scopy = dict(seg)
            if scopy.get("type") in ("image", "gif") and scopy.get("media_id"):
                scopy["url"] = _sign_admin_url_if_private(scopy.get("url"), scopy.get("media_id"), settings)
            elif scopy.get("type") == "forward_quote" and isinstance(scopy.get("messages"), list):
                scopy["messages"] = _sign_chat_admin_media_urls(scopy["messages"], settings)
            new_content.append(scopy)
        mcopy["content"] = new_content
        msgs.append(mcopy)
    return msgs


def _strip_chat_media_ids(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    for m in messages:
        mcopy = dict(m)
        new_content: list[dict[str, Any]] = []
        for seg in m.get("content") or []:
            scopy = dict(seg)
            scopy.pop("media_id", None)
            if scopy.get("type") == "forward_quote" and isinstance(scopy.get("messages"), list):
                scopy["messages"] = _strip_chat_media_ids(scopy["messages"])
            new_content.append(scopy)
        mcopy["content"] = new_content
        msgs.append(mcopy)
    return msgs


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
                b["messages"] = _strip_chat_media_ids(b.get("messages") or [])
        else:
            # Sign private /api/v1/admin/media/{id} URLs with short-lived per-resource HMAC
            if btype in ("image", "gif") and b.get("media_id"):
                b["url"] = _sign_admin_url_if_private(b.get("url"), b.get("media_id"), settings)
            elif btype == "video_link" and b.get("cover_media_id"):
                b["cover_url"] = _sign_admin_url_if_private(
                    b.get("cover_url"), b.get("cover_media_id"), settings
                )
            elif btype == "chat_record":
                rendered = []
                for ri in b.get("rendered_images") or []:
                    rcopy = dict(ri)
                    if rcopy.get("media_id"):
                        rcopy["url"] = _sign_admin_url_if_private(
                            rcopy.get("url"), rcopy.get("media_id"), settings
                        )
                    rendered.append(rcopy)
                b["rendered_images"] = rendered
                b["messages"] = _sign_chat_admin_media_urls(b.get("messages") or [], settings)
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
