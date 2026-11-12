"""Sliding-window rate limiter (in-memory, per process).

For each key we remember the timestamps of recent hits. A request is allowed if fewer than
`limit` hits happened in the last `window` seconds.

LIMITATION: state lives in this process only. With N replicas the effective limit is N x limit.
Use a shared store (e.g. Redis) if you need exact global limits. See docs/CATALOG.md.
"""
import math
import threading
import time
from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic, max_keys: int = 10_000):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()  # routes run in a thread pool

    def _prune(self, key: str, now: float) -> deque:
        q = self._hits.setdefault(key, deque())
        while q and q[0] <= now - self.window:
            q.popleft()
        return q

    def _retry_after(self, q: deque, now: float) -> int:
        return max(1, math.ceil(q[0] + self.window - now))

    def _evict_if_full(self, now: float) -> None:
        # Memory safety: an attacker rotating keys (IPs) must not grow this without bound.
        if len(self._hits) >= self.max_keys:
            for k in [k for k in self._hits if not self._prune(k, now)]:
                del self._hits[k]
            if len(self._hits) >= self.max_keys:
                self._hits.pop(next(iter(self._hits)))  # drop the oldest key

    def hit(self, key: str) -> int | None:
        """Count one request. Returns None if allowed, else seconds until retry."""
        with self._lock:
            now = self.clock()
            if key not in self._hits:
                self._evict_if_full(now)
            q = self._prune(key, now)
            if len(q) >= self.limit:
                return self._retry_after(q, now)
            q.append(now)
            return None

    def blocked_for(self, key: str) -> int | None:
        """Without counting: seconds until retry if the key is currently over the limit."""
        with self._lock:
            if key not in self._hits:
                return None
            now = self.clock()
            q = self._prune(key, now)
            return self._retry_after(q, now) if len(q) >= self.limit else None

    def record(self, key: str) -> None:
        """Count an event unconditionally (used for failed logins)."""
        with self._lock:
            now = self.clock()
            if key not in self._hits:
                self._evict_if_full(now)
            self._prune(key, now).append(now)


class Limiters:
    def __init__(self, read_per_min: int, write_per_min: int, auth_fail_per_min: int, clock=time.monotonic):
        self.read = SlidingWindowLimiter(read_per_min, clock=clock)
        self.write = SlidingWindowLimiter(write_per_min, clock=clock)
        self.auth_fail = SlidingWindowLimiter(auth_fail_per_min, clock=clock)
