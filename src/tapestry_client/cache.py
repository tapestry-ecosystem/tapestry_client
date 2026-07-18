"""Tiny in-process TTL cache for companion app use.

The platform client does not cache by default; callers wire a ``TTLCache`` at
whatever layer makes sense (per-request, per-session, module-level). Values
are stored with a monotonic deadline; expired entries are evicted lazily on
read.
"""

from __future__ import annotations

import time


class TTLCache[K, V]:
    """Monotonic TTL cache.

    Args:
        ttl_seconds: Default time-to-live for entries set via :meth:`set`.
        max_size: Optional cap on the number of entries. When exceeded, the
            entry with the earliest expiry is evicted.
    """

    def __init__(self, ttl_seconds: float, *, max_size: int | None = None) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_size is not None and max_size <= 0:
            raise ValueError("max_size must be positive when provided")
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._entries: dict[K, tuple[float, V]] = {}

    def get(self, key: K) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._entries.pop(key, None)
            return None
        return value

    def set(self, key: K, value: V, *, ttl_seconds: float | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        expires_at = time.monotonic() + ttl
        # Re-insert to keep insertion order meaningful for FIFO eviction.
        self._entries.pop(key, None)
        self._entries[key] = (expires_at, value)
        if self._max_size is not None and len(self._entries) > self._max_size:
            oldest_key = next(iter(self._entries))
            self._entries.pop(oldest_key, None)

    def invalidate(self, key: K) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        entry = self._entries.get(key)  # type: ignore[arg-type]
        if entry is None:
            return False
        if time.monotonic() >= entry[0]:
            self._entries.pop(key, None)  # type: ignore[arg-type]
            return False
        return True
