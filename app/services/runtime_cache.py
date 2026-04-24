from __future__ import annotations

import time
from threading import Lock
from typing import Any

_CACHE_LOCK = Lock()
_CACHE: dict[str, tuple[float, Any]] = {}
MISSING = object()


def get_cached(key: str) -> Any:
    now = time.time()
    with _CACHE_LOCK:
        row = _CACHE.get(key)
        if not row:
            return MISSING
        expires_at, value = row
        if expires_at <= now:
            _CACHE.pop(key, None)
            return MISSING
        return value


def set_cached(key: str, value: Any, *, ttl_seconds: float) -> None:
    expires_at = time.time() + max(0.1, float(ttl_seconds))
    with _CACHE_LOCK:
        _CACHE[key] = (expires_at, value)


def invalidate_cached(key: str) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    with _CACHE_LOCK:
        keys = [k for k in _CACHE.keys() if k.startswith(prefix)]
        for key in keys:
            _CACHE.pop(key, None)
