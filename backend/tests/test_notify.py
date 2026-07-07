import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.infra.discord as discord
from service.trading.bot import Bot


# Redis 없는 캐시 스텁
class _Cache:
    redis = None


# 틱 큐 스텁 — start만 노출
class _Queue:
    async def start(self) -> None:
        return None


# 최소 broker 스텁 (cache만 필요)
class _Broker:
    def __init__(self) -> None:
        self.cache = _Cache()


# FR-03 — Discord 알림 큐 격리 (주문 경로 비차단 + 백프레셔)
class NotifyIsolationTest(unittest.IsolatedAsyncioTestCase):
    # webhook URL/_post 보존 후 더미 URL 주입 (큐 동작 활성화)
    def setUp(self) -> None:
        self._url = discord.settings.discord_webhook_url
        self._post = discord._post
        discord.settings.discord_webhook_url = "https://discord.test/webhook"

    # 전역 상태 복원 + 큐/태스크/카운터 초기화
    def tearDown(self) -> None:
        discord.settings.discord_webhook_url = self._url
        discord._post = self._post
        discord._queue = None
        discord._task = None
        discord._dropped = 0

    # T4 — 소비자(POST)에 무한 지연을 줘도 bot.msg는 즉시 반환 (큐 격리)
    async def test_t4(self):
        release = asyncio.Event()

        # release 전까지 끝나지 않는 소비자 POST mock
        async def blocked(_msg: str) -> None:
            await release.wait()

        discord._post = blocked
        await discord.start()

        bot = Bot(_Broker(), _Queue(), {}, lambda: [])

        start = time.monotonic()
        await bot.msg("손절 발생")
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1)

        release.set()
        await discord.close()

    # T5 — 큐 maxsize 초과 시 예외 없이 드롭 카운트만 증가
    async def test_t5(self):
        # 소비자 미기동 → 큐가 비워지지 않음
        discord._queue = asyncio.Queue(maxsize=discord._MAXSIZE)
        discord._dropped = 0

        for i in range(discord._MAXSIZE + 50):
            await discord.notify(f"msg-{i}")

        self.assertEqual(discord._dropped, 50)
        self.assertEqual(discord._queue.qsize(), discord._MAXSIZE)


if __name__ == "__main__":
    unittest.main()
