"""
A small, dependency-free in-memory TTL cache.

For a single-process deployment this is sufficient and adds no operational
overhead. See docs/recommendations.md for why this should be swapped for a
shared cache (e.g., Redis) once HealthTrack runs more than one API process
-- an in-memory cache like this one is NOT shared across processes/replicas,
which is a real scaling limitation, not a hidden detail.
"""
import time
from threading import Lock


class TTLCache:
    def __init__(self, default_ttl_seconds: float = 30.0):
        self._store = {}
        self._lock = Lock()
        self.default_ttl = default_ttl_seconds
        self.hits = 0
        self.misses = 0

    def get(self, key):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            value, expires_at = entry
            if time.time() > expires_at:
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key, value, ttl_seconds=None):
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def invalidate(self, key):
        with self._lock:
            self._store.pop(key, None)

    def stats(self):
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else None,
            "entries": len(self._store),
        }


# One process-wide cache instance, keyed by (endpoint, params).
risk_report_cache = TTLCache(default_ttl_seconds=30.0)
