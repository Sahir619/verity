"""In-process token buckets (ARCHITECTURE 12.2). Correct only because the server is a single process."""

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _Bucket:
    tokens: float
    stamp: float


class TokenBucketLimiter:
    """`capacity` requests per `window_s` seconds per key: a bucket of `capacity` tokens refilled continuously.

    acquire() is synchronous and never awaits, so no lock is needed inside one event loop.
    """

    def __init__(
        self,
        capacity: int,
        window_s: float,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1 or window_s <= 0:
            raise ValueError("capacity must be >= 1 and window_s > 0")
        self.capacity = float(capacity)
        self.rate = capacity / window_s  # tokens per second
        self._max_keys = max_keys
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}

    def _refill(self, bucket: _Bucket, now: float) -> None:
        bucket.tokens = min(self.capacity, bucket.tokens + (now - bucket.stamp) * self.rate)
        bucket.stamp = now

    def acquire(self, key: str) -> float:
        """Take one token. Returns 0.0 when allowed, otherwise the seconds until a token is available."""
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            self._prune(now)
            bucket = self._buckets[key] = _Bucket(tokens=self.capacity, stamp=now)
        else:
            self._refill(bucket, now)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return 0.0
        return (1.0 - bucket.tokens) / self.rate

    def refund(self, key: str) -> None:
        """Give back a token (used when a request was refused before it did any work)."""
        bucket = self._buckets.get(key)
        if bucket is not None:
            bucket.tokens = min(self.capacity, bucket.tokens + 1.0)

    def _prune(self, now: float) -> None:
        if len(self._buckets) < self._max_keys:
            return
        for key in [k for k, b in self._buckets.items() if b.tokens + (now - b.stamp) * self.rate >= self.capacity]:
            del self._buckets[key]
        if len(self._buckets) >= self._max_keys:
            # Still full of active keys: drop the least recently used half.
            oldest = sorted(self._buckets, key=lambda k: self._buckets[k].stamp)
            for key in oldest[: len(oldest) // 2]:
                del self._buckets[key]
