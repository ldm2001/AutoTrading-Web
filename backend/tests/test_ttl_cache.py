import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.infra.ttl_cache as ttl_module
from service.infra.ttl_cache import TTLCache


# 항상 실패하는 Redis 스텁
class DownRedis:
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
class UpRedis:
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
def cacherig(redis_stub) -> TTLCache:
    with mock.patch.object(TTLCache, "conn", lambda self: None):
        cache = TTLCache()
    cache._redis = redis_stub
    return cache


class BreakerTest(unittest.TestCase):
    # 연속 실패 한도 도달 시 Redis 호출 중단
    def testtrip(self):
        stub = DownRedis()
        cache = cacherig(stub)
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.lookup("k")
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT)
        cache.lookup("k")
        cache.memo("k", 1, 5)
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT)

    # 강등 중에도 인메모리 경로는 정상 동작
    def testfallback(self):
        cache = cacherig(DownRedis())
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.lookup("k")
        cache.memo("k", {"v": 1}, 5)
        self.assertEqual(cache.lookup("k"), {"v": 1})

    # 쿨다운 경과 후 1회 재시도, 실패 시 재강등
    def testhalfopen(self):
        stub = DownRedis()
        cache = cacherig(stub)
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.lookup("k")
        cache._retry_at = time.time() - 1
        cache.lookup("k")
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT + 1)
        cache.lookup("k")
        self.assertEqual(stub.calls, ttl_module._FAIL_LIMIT + 1)

    # 재시도 성공 시 완전 복구
    def testrecovery(self):
        cache = cacherig(DownRedis())
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.lookup("k")
        cache._redis = UpRedis()
        cache._retry_at = time.time() - 1
        cache.memo("k", {"v": 2}, 5)
        self.assertEqual(cache._fails, 0)
        self.assertEqual(cache.lookup("k"), {"v": 2})

    # 강등 중 invalidate는 인메모리를 정리
    def testeviction(self):
        cache = cacherig(DownRedis())
        for _ in range(ttl_module._FAIL_LIMIT):
            cache.lookup("k")
        cache.memo("holdings", {"v": 3}, 5)
        cache.eviction("holdings")
        self.assertIsNone(cache.lookup("holdings"))


if __name__ == "__main__":
    unittest.main()
