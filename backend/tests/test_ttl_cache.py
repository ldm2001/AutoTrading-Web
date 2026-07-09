import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.infra.ttl_cache as ttl_module
from service.infra.ttl_cache import TTLCache


# 항상 실패하는 Redis 스텁
class _DownRedis:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, key):
        self.calls += 1
        raise ConnectionError("down")

    def setex(self, key, ttl, value):
        self.calls += 1
        raise ConnectionError("down")

    def scan(self, cursor, match=None, count=None):
        self.calls += 1
        raise ConnectionError("down")


# 정상 동작하는 Redis 스텁
class _UpRedis:
    def __init__(self) -> None:
        self.store = {}
        self.calls = 0

    def get(self, key):
        self.calls += 1
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.calls += 1
        self.store[key] = value


# 실연결 없이 캐시 인스턴스 생성
def build(redis_stub) -> TTLCache:
    with mock.patch.object(TTLCache, "conn", lambda self: None):
        cache = TTLCache()
    cache._redis = redis_stub
    return cache


class BreakerTest(unittest.TestCase):
    # 연속 실패 한도 도달 시 Redis 호출 중단
    def test_opens_after_limit(self):
        stub = _DownRedis()
        cache = build(stub)
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.get("k")
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT)
        cache.get("k")
        cache.set("k", 1, 5)
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT)

    # 강등 중에도 인메모리 경로는 정상 동작
    def test_fallback_serves_local(self):
        cache = build(_DownRedis())
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.get("k")
        cache.set("k", {"v": 1}, 5)
        self.assertEqual(cache.get("k"), {"v": 1})

    # 쿨다운 경과 후 1회 재시도, 실패 시 재강등
    def test_half_open_retry(self):
        stub = _DownRedis()
        cache = build(stub)
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.get("k")
        cache._retry_at = time.time() - 1
        cache.get("k")
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT + 1)
        cache.get("k")
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT + 1)

    # 재시도 성공 시 완전 복구
    def test_recovery(self):
        cache = build(_DownRedis())
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.get("k")
        cache._redis = _UpRedis()
        cache._retry_at = time.time() - 1
        cache.set("k", {"v": 2}, 5)
        self.assertEqual(cache._fails, 0)
        self.assertEqual(cache.get("k"), {"v": 2})

    # 강등 중 invalidate는 인메모리를 정리
    def test_invalidate_local_when_down(self):
        cache = build(_DownRedis())
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.get("k")
        cache.set("holdings", {"v": 3}, 5)
        cache.invalidate("holdings")
        self.assertIsNone(cache.get("holdings"))


if __name__ == "__main__":
    unittest.main()
