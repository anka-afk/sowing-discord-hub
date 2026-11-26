from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import Settings

_password_hasher = PasswordHasher()
_DUMMY_PASSWORD_HASH = _password_hasher.hash("dummy-timing-equalizer-never-matches-9f8e7d")


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def verify_password_constant_time(password_hash: str | None, password: str) -> bool:
    """Verify password while running a dummy Argon2 verify when user doesn't exist."""
    if not password_hash:
        verify_password(_DUMMY_PASSWORD_HASH, password)
        return False
    return verify_password(password_hash, password)


def create_admin_jwt(
    admin_id: str,
    username: str,
    role: str,
    settings: Settings,
    token_version: int = 1,
) -> str:
    now = int(time.time())
    payload = {
        "sub": admin_id,
        "username": username,
        "role": role,
        "ver": int(token_version),
        "type": "admin",
        "iat": now,
        "exp": now + settings.jwt_expire_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_admin_jwt(token: str, settings: Settings) -> dict | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "ver"]},
        )
        if payload.get("type") != "admin":
            return None
        return payload
    except jwt.PyJWTError:
        return None


def sign_admin_media_params(
    media_id: str,
    settings: Settings,
    ttl_seconds: int | None = None,
) -> tuple[int, str]:
    """Create short-lived single-resource HMAC signature for GET /api/v1/admin/media/{media_id}."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.admin_media_url_ttl_seconds
    exp = int(time.time()) + max(10, int(ttl))
    msg = f"admin_media:{media_id}:{exp}".encode("utf-8")
    sig = hmac.new(settings.jwt_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return exp, sig


def sign_admin_media_url(
    media_id: str,
    settings: Settings,
    ttl_seconds: int | None = None,
) -> str:
    exp, sig = sign_admin_media_params(media_id, settings, ttl_seconds=ttl_seconds)
    return f"/api/v1/admin/media/{media_id}?exp={exp}&sig={sig}"


def verify_admin_media_signature(
    media_id: str,
    exp: str | None,
    sig: str | None,
    settings: Settings,
) -> bool:
    if not media_id or not exp or not sig:
        return False
    try:
        exp_int = int(exp)
    except ValueError:
        return False
    now = int(time.time())
    if exp_int < now:
        return False
    # Reject absurdly far-future expiry timestamps
    if exp_int > now + max(3600, settings.admin_media_url_ttl_seconds * 4):
        return False
    msg = f"admin_media:{media_id}:{exp_int}".encode("utf-8")
    expected = hmac.new(settings.jwt_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.lower().strip())


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def salted_hmac(value: str, settings: Settings, namespace: str = "default") -> str:
    msg = f"{namespace}:{value}".encode("utf-8")
    return hmac.new(
        settings.hmac_salt.encode("utf-8"),
        msg,
        hashlib.sha256,
    ).hexdigest()


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def verify_pow(nonce_prefix: str, solution: str, difficulty_bits: int) -> bool:
    """Verify SHA-256(f'{nonce_prefix}:{solution}') has >= difficulty_bits leading zero bits."""
    if not solution or len(solution) > 128:
        return False
    digest = hashlib.sha256(f"{nonce_prefix}:{solution}".encode("utf-8")).digest()
    value = int.from_bytes(digest, "big")
    shift = 256 - difficulty_bits
    if shift <= 0:
        return value == 0
    return (value >> shift) == 0


class CollectorReplayCache:
    """Thread-safe bounded TTL replay cache for Collector HMAC requests."""

    def __init__(self, max_entries: int = 10000) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}
        self._max_entries = max_entries

    def check_and_record(self, replay_key: str, ttl_seconds: int) -> bool:
        """Return True if key is fresh and recorded; False if replayed."""
        now = time.monotonic()
        with self._lock:
            # Prune expired keys periodically or when approaching cap
            if len(self._seen) >= self._max_entries // 2:
                expired = [k for k, exp in self._seen.items() if exp <= now]
                for k in expired:
                    del self._seen[k]
            existing_exp = self._seen.get(replay_key)
            if existing_exp is not None and existing_exp > now:
                return False
            if len(self._seen) >= self._max_entries:
                # Evict oldest entry
                oldest_key = min(self._seen, key=self._seen.get)
                del self._seen[oldest_key]
            self._seen[replay_key] = now + max(10, ttl_seconds)
            return True


def compute_collector_signature(
    secret: str,
    timestamp: str,
    method: str,
    path: str,
    raw_body: bytes,
    nonce: str | None = None,
) -> str:
    body_hash = sha256_hex(raw_body)
    if nonce:
        canonical = f"{timestamp}.{nonce}.{method.upper()}.{path}.{body_hash}".encode("utf-8")
    else:
        canonical = f"{timestamp}.{method.upper()}.{path}.{body_hash}".encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()


def verify_collector_hmac(
    timestamp: str | None,
    signature: str | None,
    method: str,
    path: str,
    raw_body: bytes,
    settings: Settings,
    *,
    nonce: str | None = None,
    replay_cache: CollectorReplayCache | None = None,
) -> bool:
    if not timestamp or not signature:
        return False
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    now = int(time.time())
    if abs(now - ts_int) > settings.collector_max_skew_seconds:
        return False
    clean_nonce = nonce.strip()[:128] if nonce else None
    expected = compute_collector_signature(
        secret=settings.collector_hmac_secret,
        timestamp=timestamp,
        method=method,
        path=path,
        raw_body=raw_body,
        nonce=clean_nonce,
    )
    sig_clean = signature.lower().strip()
    if not hmac.compare_digest(expected, sig_clean):
        return False
    if replay_cache is not None:
        replay_key = f"{timestamp}:{clean_nonce or sig_clean}"
        if not replay_cache.check_and_record(
            replay_key,
            ttl_seconds=max(60, settings.collector_max_skew_seconds * 2),
        ):
            return False
    return True


async def verify_turnstile(
    token: str | None,
    settings: Settings,
    remote_ip: str | None = None,
) -> bool:
    if not settings.turnstile_enabled:
        return True
    if not token or not settings.turnstile_secret_key:
        return False
    data = {
        "secret": settings.turnstile_secret_key,
        "response": token,
    }
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=data,
            )
            if resp.status_code != 200:
                return False
            payload = resp.json()
            return bool(payload.get("success"))
    except Exception:
        return False


def is_allowed_video_link(url: str, settings: Settings) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    allowed = {d.lower() for d in settings.video_link_domains}
    if host in allowed:
        return True
    for domain in allowed:
        if host.endswith("." + domain):
            return True
    return False


# Privacy regex detectors (for admin review warnings)
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d[\s-]?\d{4}[\s-]?\d{4})(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)([1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])(?!\d)")
_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")
_WECHAT_RE = re.compile(r"(?:微信号|微信|vx|VX|wx|WX|WeChat)\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]{5,19})")
_QQ_CONTEXT_RE = re.compile(
    r"(?:QQ|qq|扣扣|群号|群|联系|加我|🐧)[^\d]{0,6}([1-9]\d{4,11})(?!\d)"
)
_STANDALONE_QQ_RE = re.compile(r"(?<!\d)([1-9]\d{6,10})(?!\d)")


def _mask_token(val: str) -> str:
    if len(val) <= 4:
        return "*" * len(val)
    return val[:2] + "*" * (len(val) - 4) + val[-2:]


def detect_privacy_warnings(texts: list[str]) -> list[dict[str, str]]:
    """Scan texts for suspected phone numbers, ID cards, emails, WeChat IDs, and QQ numbers."""
    warnings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw in texts:
        if not raw:
            continue

        for match in _ID_CARD_RE.finditer(raw):
            val = match.group(1)
            key = ("id_card", val)
            if key not in seen:
                seen.add(key)
                start = max(0, match.start() - 10)
                end = min(len(raw), match.end() + 10)
                warnings.append(
                    {
                        "type": "id_card",
                        "label": "疑似身份证号",
                        "match": _mask_token(val),
                        "snippet": raw[start:end],
                    }
                )

        for match in _PHONE_RE.finditer(raw):
            val_digits = re.sub(r"[\s-]", "", match.group(1))
            key = ("phone", val_digits)
            if key not in seen:
                seen.add(key)
                start = max(0, match.start() - 10)
                end = min(len(raw), match.end() + 10)
                warnings.append(
                    {
                        "type": "phone",
                        "label": "疑似手机号",
                        "match": _mask_token(val_digits),
                        "snippet": raw[start:end],
                    }
                )

        for match in _EMAIL_RE.finditer(raw):
            val = match.group(1)
            key = ("email", val)
            if key not in seen:
                seen.add(key)
                start = max(0, match.start() - 10)
                end = min(len(raw), match.end() + 10)
                warnings.append(
                    {
                        "type": "email",
                        "label": "疑似邮箱",
                        "match": _mask_token(val),
                        "snippet": raw[start:end],
                    }
                )

        for match in _WECHAT_RE.finditer(raw):
            val = match.group(1)
            key = ("wechat", val)
            if key not in seen:
                seen.add(key)
                start = max(0, match.start() - 10)
                end = min(len(raw), match.end() + 10)
                warnings.append(
                    {
                        "type": "wechat",
                        "label": "疑似微信号",
                        "match": _mask_token(val),
                        "snippet": raw[start:end],
                    }
                )

        for match in _QQ_CONTEXT_RE.finditer(raw):
            val = match.group(1)
            if ("phone", val) in seen:
                continue
            key = ("qq", val)
            if key not in seen:
                seen.add(key)
                start = max(0, match.start() - 10)
                end = min(len(raw), match.end() + 10)
                warnings.append(
                    {
                        "type": "qq",
                        "label": "疑似 QQ/群号",
                        "match": _mask_token(val),
                        "snippet": raw[start:end],
                    }
                )

        for match in _STANDALONE_QQ_RE.finditer(raw):
            val = match.group(1)
            if ("phone", val) in seen or ("qq", val) in seen:
                continue
            if len(val) == 8 and (val.startswith("20") or val.startswith("19")):
                continue
            key = ("qq", val)
            if key not in seen:
                seen.add(key)
                start = max(0, match.start() - 10)
                end = min(len(raw), match.end() + 10)
                warnings.append(
                    {
                        "type": "qq",
                        "label": "疑似 QQ/群号",
                        "match": _mask_token(val),
                        "snippet": raw[start:end],
                    }
                )

    return warnings


def parse_iso_utc(dt_str: str) -> datetime:
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_after_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
