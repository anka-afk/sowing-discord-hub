from __future__ import annotations

import os
import secrets
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for i in range(length - 1, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_ulid(timestamp_ms: int | None = None) -> str:
    """Generate a 26-character Crockford Base32 ULID."""
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    rand = int.from_bytes(secrets.token_bytes(10), "big")
    return _encode_base32(ts, 10) + _encode_base32(rand, 16)


def generate_prefixed_id(prefix: str) -> str:
    return f"{prefix}_{generate_ulid()}"
