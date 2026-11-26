from __future__ import annotations

import hashlib
import io
import sqlite3
from dataclasses import dataclass

import imagehash
from PIL import Image, ImageOps

from app.config import Settings


class MediaValidationError(ValueError):
    """Raised when uploaded media fails magic bytes, size, or safety checks."""


@dataclass
class ProcessedMedia:
    data: bytes
    mime_type: str
    ext: str
    width: int
    height: int
    size_bytes: int
    sha256: str
    phash: str


def detect_magic_mime(data: bytes) -> str | None:
    """Strictly detect supported image formats via magic bytes."""
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


_ALLOWED_GIF_APP_IDS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")


def _sanitize_gif_and_count_frames(
    raw: bytes,
    max_frames: int | None = None,
) -> tuple[bytes, int, int, int]:
    """Parse GIF in O(file_size) bytes without LZW pixel decompression.

    - Extracts Logical Screen (width, height) and counts 0x2C Image Descriptor frames.
    - Aborts early if frame count exceeds `max_frames`.
    - Allowlists ONLY Graphic Control Extension (0xF9) and looping Application Extensions
      (NETSCAPE2.0 / ANIMEXTS1.0), stripping Comments (0xFE), Plain Text (0x01), and XMP/ICC.
    - Rejects malformed structures and non-zero trailing polyglot bytes fail-closed.
    """
    n = len(raw)
    if n < 13 or raw[:6] not in (b"GIF87a", b"GIF89a"):
        raise MediaValidationError("Invalid GIF header")

    lsd_w = int.from_bytes(raw[6:8], "little")
    lsd_h = int.from_bytes(raw[8:10], "little")
    out = bytearray()
    packed = raw[10]
    pos = 13
    if packed & 0x80:
        gct_size = 3 * (1 << ((packed & 0x07) + 1))
        pos += gct_size
    if pos > n:
        raise MediaValidationError("Truncated GIF global color table")
    out.extend(raw[:pos])

    frame_count = 0
    saw_trailer = False
    while pos < n:
        introducer = raw[pos]
        if introducer == 0x3B:  # Trailer
            out.append(0x3B)
            pos += 1
            saw_trailer = True
            break
        elif introducer == 0x21:  # Extension block
            if pos + 2 > n:
                raise MediaValidationError("Truncated GIF extension header")
            label = raw[pos + 1]
            start = pos
            pos += 2
            while pos < n:
                sub_len = raw[pos]
                pos += 1
                if sub_len == 0:
                    break
                pos += sub_len
            if pos > n:
                raise MediaValidationError("Truncated GIF extension sub-block")
            keep = False
            if label == 0xF9:
                keep = True
            elif label == 0xFF:
                if (
                    pos - start >= 14
                    and raw[start + 2] == 0x0B
                    and raw[start + 3 : start + 14] in _ALLOWED_GIF_APP_IDS
                ):
                    keep = True
            if keep:
                out.extend(raw[start:pos])
        elif introducer == 0x2C:  # Image Descriptor (one frame)
            frame_count += 1
            if max_frames is not None and frame_count > max_frames:
                raise MediaValidationError(
                    f"GIF exceeds maximum frame count ({max_frames})"
                )
            start = pos
            if pos + 10 > n:
                raise MediaValidationError("Truncated GIF image descriptor")
            img_packed = raw[pos + 9]
            pos += 10
            if img_packed & 0x80:
                lct_size = 3 * (1 << ((img_packed & 0x07) + 1))
                pos += lct_size
            if pos >= n:
                raise MediaValidationError("Truncated GIF local color table")
            pos += 1  # LZW minimum code size
            while pos < n:
                sub_len = raw[pos]
                pos += 1
                if sub_len == 0:
                    break
                pos += sub_len
            if pos > n:
                raise MediaValidationError("Truncated GIF image data sub-block")
            out.extend(raw[start:pos])
        elif introducer == 0x00:
            pos += 1
        else:
            raise MediaValidationError(f"Malformed GIF block introducer: 0x{introducer:02x}")

    if not saw_trailer:
        raise MediaValidationError("Malformed GIF missing trailer")
    if raw[pos:].rstrip(b"\x00"):
        raise MediaValidationError("Trailing data after GIF trailer")
    if frame_count <= 0:
        raise MediaValidationError("GIF contains no image frames")
    return bytes(out), frame_count, lsd_w, lsd_h


def _strip_gif_comments(raw: bytes) -> bytes:
    cleaned, _, _, _ = _sanitize_gif_and_count_frames(raw)
    return cleaned


def process_uploaded_media(
    raw_bytes: bytes,
    settings: Settings,
    *,
    is_chat_render: bool = False,
) -> ProcessedMedia:
    """Validate, sanitize, transcode/preserve, and hash uploaded media.

    Rules from IMPLEMENTATION_PLAN.md:
    - Magic bytes validation only (JPEG / PNG / WEBP / GIF).
    - Static images: <= 10MB, anti-bomb pixel cap, strip EXIF/GPS, longest side <= 1920,
      convert to image/webp (quality 80).
    - GIF: MUST remain image/gif (never animated WebP), <= 5MB, anti-bomb pixel/frame cap,
      strip GIF comment blocks, compute pHash from frame 0.
    """
    if not raw_bytes:
        raise MediaValidationError("Empty media upload")

    detected_mime = detect_magic_mime(raw_bytes)
    if detected_mime is None:
        raise MediaValidationError(
            "Unsupported or invalid image format (only JPEG, PNG, WEBP, GIF allowed)"
        )

    Image.MAX_IMAGE_PIXELS = settings.max_image_pixels

    if detected_mime == "image/gif":
        if is_chat_render:
            raise MediaValidationError("Chat render slices must be static images")
        if len(raw_bytes) > settings.max_gif_bytes:
            raise MediaValidationError(
                f"GIF exceeds maximum size of {settings.max_gif_bytes} bytes"
            )
        # Parse structure, strip metadata extensions, and count frames BEFORE Pillow decodes pixels
        cleaned_gif, n_frames, lsd_w, lsd_h = _sanitize_gif_and_count_frames(
            raw_bytes, max_frames=settings.max_gif_frames
        )
        if lsd_w <= 0 or lsd_h <= 0:
            raise MediaValidationError("Invalid GIF dimensions")
        if lsd_w * lsd_h > settings.max_image_pixels:
            raise MediaValidationError("GIF exceeds maximum pixel limit")
        if lsd_w * lsd_h * min(n_frames, 100) > settings.max_image_pixels * 15:
            raise MediaValidationError("GIF triggers decompression bomb guard")

        try:
            with Image.open(io.BytesIO(cleaned_gif)) as img:
                width, height = img.size
                if width <= 0 or height <= 0:
                    raise MediaValidationError("Invalid GIF dimensions")
                if width * height > settings.max_image_pixels:
                    raise MediaValidationError("GIF exceeds maximum pixel limit")
                if width * height * min(n_frames, 100) > settings.max_image_pixels * 15:
                    raise MediaValidationError("GIF triggers decompression bomb guard")
                img.seek(0)
                first_frame = img.convert("RGB")
                phash_hex = str(imagehash.phash(first_frame))
        except MediaValidationError:
            raise
        except Exception as exc:
            raise MediaValidationError(f"Invalid or corrupted GIF: {exc}") from exc

        sha_hex = hashlib.sha256(cleaned_gif).hexdigest()
        return ProcessedMedia(
            data=cleaned_gif,
            mime_type="image/gif",
            ext="gif",
            width=width,
            height=height,
            size_bytes=len(cleaned_gif),
            sha256=sha_hex,
            phash=phash_hex,
        )

    # Static image path (JPEG / PNG / WEBP)
    if len(raw_bytes) > settings.max_image_bytes:
        raise MediaValidationError(
            f"Image exceeds maximum size of {settings.max_image_bytes} bytes"
        )

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            width, height = img.size
            if width <= 0 or height <= 0:
                raise MediaValidationError("Invalid image dimensions")
            if width * height > settings.max_image_pixels:
                raise MediaValidationError("Image exceeds maximum pixel limit")

            # Apply EXIF orientation before stripping EXIF metadata
            transposed = ImageOps.exif_transpose(img)
            if transposed.mode in ("RGBA", "LA") or (
                transposed.mode == "P" and "transparency" in transposed.info
            ):
                clean_img = transposed.convert("RGBA")
            else:
                clean_img = transposed.convert("RGB")

            # Resize longest side <= max_image_dimension (1920)
            max_dim = settings.max_image_dimension
            w, h = clean_img.size
            if max(w, h) > max_dim:
                scale = max_dim / float(max(w, h))
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                clean_img = clean_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # Create a fresh image without EXIF/ICC/comment metadata
            stripped = Image.new(clean_img.mode, clean_img.size)
            stripped.paste(clean_img)

            phash_hex = str(imagehash.phash(stripped.convert("RGB")))

            out_buf = io.BytesIO()
            stripped.save(
                out_buf,
                format="WEBP",
                quality=80,
                method=4,
            )
            webp_bytes = out_buf.getvalue()
            out_w, out_h = stripped.size
    except MediaValidationError:
        raise
    except Exception as exc:
        raise MediaValidationError(f"Invalid or corrupted image: {exc}") from exc

    sha_hex = hashlib.sha256(webp_bytes).hexdigest()
    return ProcessedMedia(
        data=webp_bytes,
        mime_type="image/webp",
        ext="webp",
        width=out_w,
        height=out_h,
        size_bytes=len(webp_bytes),
        sha256=sha_hex,
        phash=phash_hex,
    )


def phash_hamming_distance(h1: str, h2: str) -> int:
    if not h1 or not h2:
        return 64
    try:
        v1 = int(h1, 16)
        v2 = int(h2, 16)
        return (v1 ^ v2).bit_count()
    except ValueError:
        return 64


def phash_similarity(distance: int) -> float:
    return round(max(0.0, 1.0 - distance / 64.0), 4)


def find_duplicate_media(
    conn: sqlite3.Connection,
    sha256_hex: str,
    phash_hex: str,
    threshold: int = 8,
    exclude_item_id: str | None = None,
) -> dict | None:
    """Check exact SHA-256 and perceptual hash against existing items.

    Priority: approved > pending/pending_recheck. Rejected/takedown items still
    exist in media_assets for dedup, though public early-stop reports approved/pending.
    """
    # 1. Exact SHA-256 match linked to an active or pending item
    rows = conn.execute(
        """
        SELECT m.id AS media_id, m.item_id, m.phash, i.status
        FROM media_assets m
        JOIN items i ON i.id = m.item_id
        WHERE m.sha256 = ?
          AND i.status IN ('approved', 'pending', 'pending_recheck')
        ORDER BY CASE i.status WHEN 'approved' THEN 0 ELSE 1 END, i.created_at DESC
        LIMIT 5
        """,
        (sha256_hex,),
    ).fetchall()
    for row in rows:
        if exclude_item_id and row["item_id"] == exclude_item_id:
            continue
        status = "approved" if row["status"] == "approved" else "pending"
        return {
            "duplicate": True,
            "match_type": "exact",
            "existing_status": status,
            "existing_item_id": row["item_id"] if status == "approved" else None,
            "raw_item_id": row["item_id"],
            "similarity": 1.0,
        }

    # 2. Perceptual hash match
    if phash_hex:
        candidates = conn.execute(
            """
            SELECT m.item_id, m.phash, i.status
            FROM media_assets m
            JOIN items i ON i.id = m.item_id
            WHERE m.phash != ''
              AND i.status IN ('approved', 'pending', 'pending_recheck')
            """,
        ).fetchall()
        best: dict | None = None
        best_dist = threshold + 1
        for row in candidates:
            if exclude_item_id and row["item_id"] == exclude_item_id:
                continue
            dist = phash_hamming_distance(phash_hex, row["phash"])
            if dist <= threshold:
                status = "approved" if row["status"] == "approved" else "pending"
                # Prefer lower distance, then approved status
                is_better = (
                    best is None
                    or dist < best_dist
                    or (dist == best_dist and status == "approved" and best["existing_status"] != "approved")
                )
                if is_better:
                    best_dist = dist
                    best = {
                        "duplicate": True,
                        "match_type": "similar",
                        "existing_status": status,
                        "existing_item_id": row["item_id"] if status == "approved" else None,
                        "raw_item_id": row["item_id"],
                        "similarity": phash_similarity(dist),
                    }
        if best is not None:
            return best

    return None


def find_similar_items_for_admin(
    conn: sqlite3.Connection,
    item_id: str,
    threshold: int = 8,
) -> list[dict]:
    """Return top similar items (for admin decision-aid card, including item IDs)."""
    item_media = conn.execute(
        "SELECT sha256, phash FROM media_assets WHERE item_id = ?",
        (item_id,),
    ).fetchall()
    if not item_media:
        return []

    other_media = conn.execute(
        """
        SELECT m.item_id, m.sha256, m.phash, i.status
        FROM media_assets m
        JOIN items i ON i.id = m.item_id
        WHERE m.item_id IS NOT NULL
          AND m.item_id != ?
          AND i.status IN ('approved', 'pending', 'pending_recheck', 'rejected')
        """,
        (item_id,),
    ).fetchall()

    best_by_item: dict[str, dict] = {}
    for mine in item_media:
        for other in other_media:
            oid = other["item_id"]
            if mine["sha256"] == other["sha256"]:
                cand = {
                    "item_id": oid,
                    "status": other["status"],
                    "similarity": 1.0,
                    "match_type": "exact",
                }
            else:
                dist = phash_hamming_distance(mine["phash"], other["phash"])
                if dist > threshold:
                    continue
                cand = {
                    "item_id": oid,
                    "status": other["status"],
                    "similarity": phash_similarity(dist),
                    "match_type": "similar",
                }
            prev = best_by_item.get(oid)
            if prev is None or cand["similarity"] > prev["similarity"]:
                best_by_item[oid] = cand

    return sorted(
        best_by_item.values(),
        key=lambda x: x["similarity"],
        reverse=True,
    )[:5]
