from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import Settings

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def create_admin_jwt(
    admin_id: str,
    username: str,
    role: str,
    settings: Settings,
) -> str:
    now = int(time.time())
    payload = {
        "sub": admin_id,
        "username": username,
        "role": role,
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
        )
        if payload.get("type") != "admin":
            return None
        return payload
    except jwt.PyJWTError:
        return None


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


def compute_collector_signature(
    secret: str,
    timestamp: str,
    method: str,
    path: str,
    raw_body: bytes,
) -> str:
    body_hash = sha256_hex(raw_body)
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
    expected = compute_collector_signature(
        secret=settings.collector_hmac_secret,
        timestamp=timestamp,
        method=method,
        path=path,
        raw_body=raw_body,
    )
    return hmac.compare_digest(expected, signature.lower().strip())


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
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")
_QQ_CONTEXT_RE = re.compile(
    r"(?:QQ|qq|扣扣|群号|群|联系|加我|🐧)[^\d]{0,6}([1-9]\d{4,11})(?!\d)"
)
_STANDALONE_QQ_RE = re.compile(r"(?<!\d)([1-9]\d{6,10})(?!\d)")


def _mask_token(val: str) -> str:
    if len(val) <= 4:
        return "*" * len(val)
    return val[:2] + "*" * (len(val) - 4) + val[-2:]


def detect_privacy_warnings(texts: list[str]) -> list[dict[str, str]]:
    """Scan texts for suspected phone numbers, emails, and QQ numbers."""
    warnings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for raw in texts:
        if not raw:
            continue
        for match in _PHONE_RE.finditer(raw):
            val = match.group(1)
            key = ("phone", val)
            if key not in seen:
                seen.add(key)
                start = max(0, match.start() - 10)
                end = min(len(raw), match.end() + 10)
                warnings.append(
                    {
                        "type": "phone",
                        "match": _mask_token(val),
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
                        "match": _mask_token(val),
                        "snippet": raw[start:end],
                    }
                )

        for match in _STANDALONE_QQ_RE.finditer(raw):
            val = match.group(1)
            # Skip already flagged phones/QQs or obvious years/dates
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
