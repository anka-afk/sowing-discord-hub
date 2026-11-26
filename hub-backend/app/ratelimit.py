from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException, Request, status

from app.config import Settings


class SlidingWindowRateLimiter:
    """Thread-safe bounded in-memory sliding-window rate limiter with per-bucket windows."""

    def __init__(self, max_buckets: int = 20000) -> None:
        self._lock = threading.Lock()
        # bucket_key -> (timestamps_deque, window_seconds)
        self._buckets: dict[str, tuple[deque[float], int]] = {}
        self._max_buckets = max_buckets

    def check(self, bucket_key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        if limit <= 0:
            return False, max(1, int(window_seconds))
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            entry = self._buckets.get(bucket_key)
            if entry is None:
                if len(self._buckets) >= self._max_buckets:
                    stale = [
                        k
                        for k, (bq, bwin) in self._buckets.items()
                        if not bq or bq[-1] <= now - bwin
                    ]
                    for k in stale:
                        del self._buckets[k]
                    if len(self._buckets) >= self._max_buckets:
                        oldest = min(
                            self._buckets,
                            key=lambda k: self._buckets[k][0][-1] if self._buckets[k][0] else 0.0,
                        )
                        del self._buckets[oldest]
                q: deque[float] = deque()
                self._buckets[bucket_key] = (q, window_seconds)
            else:
                q, prev_win = entry
                if prev_win != window_seconds:
                    self._buckets[bucket_key] = (q, window_seconds)

            while q and q[0] <= cutoff:
                q.popleft()

            if len(q) >= limit:
                retry_after = max(1, int(q[0] + window_seconds - now) + 1)
                return False, retry_after

            q.append(now)
            return True, 0


def get_client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return str(request.client.host)
    return "unknown"


def enforce_rate_limit(
    request: Request,
    settings: Settings,
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
) -> None:
    if not settings.rate_limit_enabled:
        return
    limiter: SlidingWindowRateLimiter | None = getattr(
        request.app.state, "rate_limiter", None
    )
    if limiter is None:
        return
    allowed, retry_after = limiter.check(f"{scope}:{identity}", limit, window_seconds)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {scope}. Retry in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )
