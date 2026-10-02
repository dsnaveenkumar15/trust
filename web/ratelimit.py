import math
import threading
import time

from fastapi import HTTPException


class RateLimiter:
    """Token bucket per key. ponytail: in-process memory, fine for one web container; move to SQLite/Redis if
    web ever runs more than one replica."""

    def __init__(self, per_minute: int, burst: int | None = None, max_keys: int = 10_000):
        self.rate = per_minute / 60.0
        self.burst = float(burst if burst is not None else per_minute)
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                wait = 0.0
            else:
                self._buckets[key] = (tokens, now)
                wait = (1 - tokens) / self.rate
            if len(self._buckets) > self.max_keys:
                oldest = sorted(self._buckets, key=lambda k: self._buckets[k][1])[: len(self._buckets) // 2]
                for stale in oldest:
                    del self._buckets[stale]
            return wait


def enforce(limiter: RateLimiter, key: str) -> None:
    wait = limiter.check(key)
    if wait > 0:
        raise HTTPException(429, "rate limited", headers={"Retry-After": str(math.ceil(wait))})
