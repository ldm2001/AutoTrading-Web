import json
import logging
import time
from typing import Any
import redis
from config import settings
from service.metrics import cache_hit, cache_miss

logger = logging.getLogger(__name__)

# Redis 기반 TTL 캐시
# 연결 실패 시 인메모리 폴백
class TTLCache:
    def __init__(self) -> None:
        self._local: dict[str, tuple[float, Any]] = {}
        self._redis: redis.Redis | None = None
        self._connect()

    def _connect(self) -> None:
        try:
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=1,
            )
            self._redis.ping()
            logger.info("Redis connected: %s", settings.redis_url)
        except Exception as e:
            logger.warning("Redis unavailable, using in-memory fallback: %s", e)
            self._redis = None

    # 유효한 캐시만 반환
    def get(self, key: str) -> Any | None:
        if self._redis is not None:
            try:
                raw = self._redis.get(f"cache:{key}")
                if raw is not None:
                    cache_hit.inc()
                    return json.loads(raw)
                cache_miss.inc()
                return None
            except Exception:
                pass

        entry = self._local.get(key)
        if entry is None:
            cache_miss.inc()
            return None
        exp, value = entry
        if time.time() < exp:
            cache_hit.inc()
            return value
        del self._local[key]
        cache_miss.inc()
        return None

    # 값을 ttl 함께 저장
    def set(self, key: str, value: Any, ttl: float) -> None:
        if self._redis is not None:
            try:
                self._redis.setex(f"cache:{key}", int(max(ttl, 1)), json.dumps(value, default=str))
                return
            except Exception:
                pass

        self._local[key] = (time.time() + ttl, value)

    # 접두사 기준으로 캐시를 무효화
    def invalidate(self, *prefixes: str) -> None:
        if self._redis is not None:
            try:
                for prefix in prefixes:
                    cursor = 0
                    while True:
                        cursor, keys = self._redis.scan(cursor, match=f"cache:{prefix}*", count=100)
                        if keys:
                            self._redis.delete(*keys)
                        if cursor == 0:
                            break
                return
            except Exception:
                pass

        keys = [k for k in self._local if any(k.startswith(p) for p in prefixes)]
        for k in keys:
            del self._local[k]

    # 전체 캐시 정리
    def clear(self) -> None:
        if self._redis is not None:
            try:
                cursor = 0
                while True:
                    cursor, keys = self._redis.scan(cursor, match="cache:*", count=100)
                    if keys:
                        self._redis.delete(*keys)
                    if cursor == 0:
                        break
                return
            except Exception:
                pass

        self._local.clear()

    @property
    def redis(self) -> redis.Redis | None:
        return self._redis
