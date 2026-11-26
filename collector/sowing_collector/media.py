from __future__ import annotations

import io
from dataclasses import dataclass

import httpx
from PIL import Image

from .dedup import compute_phash, compute_sha256

Image.MAX_IMAGE_PIXELS = 40_000_000


class MediaValidationError(ValueError):
    """Raised when media violates type, magic-bytes, size, or safety limits."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ValidatedMedia:
    data: bytes
    mime: str
    filename: str
    width: int
    height: int
    size_bytes: int
    is_gif: bool
    sha256: str
    phash: str


def detect_magic_mime(data: bytes) -> str | None:
    if len(data) < 12:
        return None
    # Video signatures to explicitly reject
    if data[4:8] == b"ftyp":
        return "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "video/x-msvideo"
    if data[:3] == b"FLV":
        return "video/x-flv"

    # Allowed image signatures
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return None


def _ext_for_mime(mime: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/bmp": "bmp",
    }.get(mime, "bin")


def validate_media_bytes(
    data: bytes,
    *,
    max_image_bytes: int = 10_485_760,
    max_gif_bytes: int = 5_242_880,
    filename_hint: str | None = None,
) -> ValidatedMedia:
    if not data:
        raise MediaValidationError("empty_media", "Media payload is empty.")

    mime = detect_magic_mime(data)
    if mime is None:
        raise MediaValidationError("unsupported_media", "Unsupported or unrecognized media magic bytes.")
    if mime.startswith("video/"):
        raise MediaValidationError("video_file_not_allowed", "Video files are forbidden in MVP.")

    is_gif = mime == "image/gif"
    size_bytes = len(data)
    if is_gif and size_bytes > max_gif_bytes:
        raise MediaValidationError(
            "gif_too_large",
            f"GIF size {size_bytes} exceeds limit {max_gif_bytes}.",
        )
    if not is_gif and size_bytes > max_image_bytes:
        raise MediaValidationError(
            "image_too_large",
            f"Image size {size_bytes} exceeds limit {max_image_bytes}.",
        )

    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width <= 0 or height <= 0:
                raise MediaValidationError("invalid_dimensions", "Image has invalid dimensions.")
            if width * height > 40_000_000:
                raise MediaValidationError("decompression_bomb", "Image pixel count exceeds safety limit.")
            img.verify()
    except MediaValidationError:
        raise
    except Exception as exc:
        raise MediaValidationError("corrupt_image", f"Corrupt or unreadable image: {exc}") from exc

    sha256 = compute_sha256(data)
    phash = compute_phash(data)
    ext = _ext_for_mime(mime)
    filename = filename_hint if filename_hint and "." in filename_hint else f"{sha256[:16]}.{ext}"

    return ValidatedMedia(
        data=data,
        mime=mime,
        filename=filename,
        width=width,
        height=height,
        size_bytes=size_bytes,
        is_gif=is_gif,
        sha256=sha256,
        phash=phash,
    )


async def download_media(
    url: str,
    *,
    max_bytes: int = 10_485_760,
    timeout_sec: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """Download media bytes from OneBot URL with hard streaming size cap."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True)
    try:
        chunks: list[bytes] = []
        total = 0
        async with http_client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    raise MediaValidationError(
                        "download_too_large",
                        f"Remote media exceeds hard download cap {max_bytes} bytes.",
                    )
                chunks.append(chunk)
        return b"".join(chunks)
    finally:
        if owns_client:
            await http_client.aclose()
