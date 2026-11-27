from __future__ import annotations

import asyncio
import io
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
from PIL import Image

from .dedup import compute_phash, compute_sha256

Image.MAX_IMAGE_PIXELS = 40_000_000

_NAT64_WK = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")
_IPV4_TRANSLATED = ipaddress.IPv6Network("::ffff:0:0:0/96")
_IPV4_COMPATIBLE = ipaddress.IPv6Network("::/96")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class MediaValidationError(ValueError):
    """Raised when media violates type, magic-bytes, size, or safety limits."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _is_public_ip(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        elif addr in _NAT64_WK:
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
            return _is_public_ip(str(embedded))
        elif (
            addr in _NAT64_LOCAL
            or addr in _IPV4_TRANSLATED
            or addr in _IPV4_COMPATIBLE
            or addr.sixtofour is not None
            or addr.teredo is not None
        ):
            return False
    return bool(
        addr.is_global
        and not addr.is_private
        and not addr.is_loopback
        and not addr.is_link_local
        and not addr.is_multicast
        and not addr.is_reserved
        and not addr.is_unspecified
    )


def _is_noncanonical_ip_literal(host: str) -> bool:
    """Detect obsolete/alternate IPv4 forms (e.g. 127.1, 0177.0.0.1, 2130706433, 0x7f000001)."""
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return False  # Canonical IPv4/IPv6 literal, handled separately
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
        return True
    except OSError:
        pass
    labels = host.split(".")
    if labels and all(
        lbl and (lbl.isdigit() or lbl.lower().startswith("0x"))
        for lbl in labels
    ):
        return True
    return False


async def _resolve_and_validate_host(host: str, port: int) -> list[str]:
    """Validate host and resolve DNS once, returning ordered validated public IP literals."""
    normalized = (host or "").strip().rstrip(".").lower()
    if not normalized or "%" in normalized:
        raise MediaValidationError("invalid_url", "Invalid or scoped media hostname.")
    if normalized in ("localhost",) or normalized.endswith((".localhost", ".local", ".internal")):
        raise MediaValidationError("ssrf_blocked", "Local/internal hostname is forbidden.")

    # Direct canonical IP literal check
    try:
        ipaddress.ip_address(normalized)
        if not _is_public_ip(normalized):
            raise MediaValidationError("ssrf_blocked", f"Non-public IP literal blocked: {normalized}")
        return [normalized]
    except ValueError:
        pass

    if _is_noncanonical_ip_literal(normalized):
        raise MediaValidationError("ssrf_blocked", f"Non-canonical IP literal blocked: {normalized}")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(normalized, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MediaValidationError("dns_failed", f"Failed to resolve media host: {normalized}") from exc

    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        if info and info[4]:
            ip_str = info[4][0]
            if ip_str not in seen:
                seen.add(ip_str)
                ips.append(ip_str)
    if not ips:
        raise MediaValidationError("dns_failed", f"No DNS records for media host: {normalized}")
    for ip_str in ips:
        if not _is_public_ip(ip_str):
            raise MediaValidationError("ssrf_blocked", f"Resolved non-public IP blocked for host: {normalized}")
    return ips


async def validate_public_media_url(url: str, *, resolve_dns: bool = True) -> str:
    """Validate URL scheme/syntax and optionally ensure every resolved DNS IP is globally routable."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise MediaValidationError("invalid_url", "Empty media URL.")
    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in cleaned):
        raise MediaValidationError("invalid_url", "Control or whitespace characters in media URL.")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise MediaValidationError("invalid_url", f"Unsupported URL scheme: {parsed.scheme!r}")
    if (
        not parsed.hostname
        or "\\" in parsed.netloc
        or "@" in parsed.netloc
        or "%" in parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise MediaValidationError("invalid_url", "Invalid or unsafe media URL authority.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise MediaValidationError("invalid_url", "Invalid media URL port.") from exc
    if port is not None and not (1 <= port <= 65535):
        raise MediaValidationError("invalid_url", "Invalid media URL port range.")

    host = parsed.hostname.rstrip(".").lower()
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal")):
        raise MediaValidationError("ssrf_blocked", "Local/internal hostname is forbidden.")

    # Direct IP literal & non-canonical IP literal check
    try:
        ipaddress.ip_address(host)
        if not _is_public_ip(host):
            raise MediaValidationError("ssrf_blocked", f"Non-public IP literal blocked: {host}")
        return cleaned
    except ValueError:
        pass

    if _is_noncanonical_ip_literal(host):
        raise MediaValidationError("ssrf_blocked", f"Non-canonical IP literal blocked: {host}")

    if resolve_dns:
        await _resolve_and_validate_host(host, port or (443 if parsed.scheme == "https" else 80))
    return cleaned


class _SafePublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect-time DNS pinning network backend eliminating DNS-rebinding TOCTOU."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        ips = await _resolve_and_validate_host(host, port)
        last_exc: Exception | None = None
        for ip in ips:
            try:
                return await self._inner.connect_tcp(
                    host=ip,
                    port=port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise MediaValidationError("connect_failed", f"Failed to connect to {host}")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        raise MediaValidationError("ssrf_blocked", "Unix domain sockets are forbidden for media downloads.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _SafePublicTransport(httpx.AsyncHTTPTransport):
    """AsyncHTTPTransport pinned to _SafePublicNetworkBackend with trust_env=False."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["trust_env"] = False
        super().__init__(**kwargs)
        self._pool._network_backend = _SafePublicNetworkBackend(inner=self._pool._network_backend)


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

    # Allowed image signatures (aligned strictly with hub-backend: jpeg, png, gif, webp)
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _ext_for_mime(mime: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }.get(mime, "bin")


def validate_media_bytes(
    data: bytes,
    *,
    max_image_bytes: int = 10_485_760,
    max_gif_bytes: int = 5_242_880,
    filename_hint: str | None = None,
) -> ValidatedMedia:
    del filename_hint  # Never forward untrusted OneBot filename hints to Hub
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
    filename = f"{sha256[:16]}.{ext}"

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
    max_redirects: int = 3,
    client: httpx.AsyncClient | None = None,
) -> bytes:
    """Download media bytes with connect-time DNS-pinned SSRF protection, hop-by-hop redirects, and byte cap."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=timeout_sec,
        follow_redirects=False,
        trust_env=False,
        transport=_SafePublicTransport(),
    )
    try:
        current_url = url
        for hop in range(max_redirects + 1):
            # When owns_client=True, _SafePublicTransport performs single connect-time DNS resolution
            # and pins the TCP dial to the validated IP (eliminating DNS rebinding TOCTOU).
            current_url = await validate_public_media_url(current_url, resolve_dns=not owns_client)
            chunks: list[bytes] = []
            total = 0
            async with http_client.stream("GET", current_url, follow_redirects=False) as resp:
                if resp.status_code in _REDIRECT_STATUSES:
                    loc = resp.headers.get("location")
                    if not loc or hop >= max_redirects:
                        raise MediaValidationError("redirect_blocked", "Too many or invalid redirects.")
                    current_url = urljoin(current_url, loc.strip())
                    continue
                if 300 <= resp.status_code < 400:
                    raise MediaValidationError("redirect_blocked", f"Unexpected HTTP {resp.status_code} response.")
                resp.raise_for_status()
                cl = resp.headers.get("content-length")
                if cl is not None:
                    cl_clean = cl.strip()
                    if not cl_clean.isdigit():
                        raise MediaValidationError("invalid_content_length", "Invalid Content-Length header.")
                    if int(cl_clean) > max_bytes:
                        raise MediaValidationError(
                            "download_too_large",
                            f"Remote media exceeds hard download cap {max_bytes} bytes.",
                        )
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaValidationError(
                            "download_too_large",
                            f"Remote media exceeds hard download cap {max_bytes} bytes.",
                        )
                    chunks.append(chunk)
            return b"".join(chunks)
        raise MediaValidationError("redirect_blocked", "Exceeded maximum redirects.")
    finally:
        if owns_client:
            await http_client.aclose()
