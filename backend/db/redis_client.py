from __future__ import annotations

import json
from threading import Lock
from typing import Any

import redis

from backend.config import get_settings
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)
_redis_client: redis.Redis | None = None
_redis_pool: redis.ConnectionPool | None = None
_cache_instance: "RedisCache" | None = None
_fallback_memory_cache: dict[str, Any] = {}
_redis_lock = Lock()
_cache_lock = Lock()


def get_redis() -> redis.Redis:
    global _redis_client, _redis_pool
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                _redis_pool = redis.ConnectionPool.from_url(
                    settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                    health_check_interval=30,
                    retry_on_timeout=True,
                )
                _redis_client = redis.Redis(connection_pool=_redis_pool)
    return _redis_client


class RedisCache:
    def __init__(self) -> None:
        self.client = get_redis()
        self._warned_down = False

    def _warn(self, exc: Exception) -> None:
        if not self._warned_down:
            logger.warning("Redis unavailable, falling back to in-memory cache: %s", exc)
            self._warned_down = True

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        _fallback_memory_cache[key] = value
        try:
            self.client.set(key, json.dumps(value), ex=ttl)
            self._warned_down = False
        except redis.RedisError as exc:
            self._warn(exc)

    def get_json(self, key: str, default: Any = None) -> Any:
        try:
            raw = self.client.get(key)
            self._warned_down = False
        except redis.RedisError as exc:
            self._warn(exc)
            return _fallback_memory_cache.get(key, default)
        if raw is None:
            return _fallback_memory_cache.get(key, default)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return _fallback_memory_cache.get(key, default)
        _fallback_memory_cache[key] = value
        return value

    def publish_json(self, channel: str, value: Any) -> None:
        try:
            self.client.publish(channel, json.dumps(value))
            self._warned_down = False
        except redis.RedisError as exc:
            self._warn(exc)


def get_cache() -> RedisCache:
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = RedisCache()
    return _cache_instance
