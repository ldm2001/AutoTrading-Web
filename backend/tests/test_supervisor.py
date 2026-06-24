import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.trading.bot as bot_module
import service.trading.journal as journal_module
from service.trading.bot import Bot
from config import settings
from service.event_bus import bus


# msg 모킹용 no-op
async def noop(_text: str = "") -> None:
    return None


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


# 테스트용 Bot 인스턴스 생성
def _bot() -> Bot:
    return Bot(_Broker(), _Queue(), {}, lambda: [])


# FR-07/08 — 봇 슈퍼바이저 (바운드 재시작 + tick 구독 누수 방지)
class BotSupervisorTest(unittest.IsolatedAsyncioTestCase):
    # 재시작 플래그/백오프/notify/trade_log 보존 후 안전값으로 교체
    def setUp(self) -> None:
        self._restart = settings.bot_restart_on_crash
        self._backoff = bot_module._BACKOFF
        self._rows = journal_module.trade_log_rows
        journal_module.trade_log_rows = lambda: []

    # 교체했던 전역 복원
    def tearDown(self) -> None:
        settings.bot_restart_on_crash = self._restart
        bot_module._BACKOFF = self._backoff
        journal_module.trade_log_rows = self._rows

    # T9 — restart off: 보유 종목 포함 경보 + crashed=True + 재시작 없음
    async def test_t9(self):
        settings.bot_restart_on_crash = False
        bot = _bot()
        bot.bought = {"005930": {"name": "삼성전자", "qty": 1, "avg_price": 70000}}
        captured: list[str] = []

        # msg 캡처
        async def cap(text: str) -> None:
            captured.append(text)

        bot.msg = cap

        loops = {"n": 0}

        # 호출 시 항상 예외를 내는 loop mock
        async def boom() -> None:
            loops["n"] += 1
            raise RuntimeError("disconnect")

        bot.loop = boom

        await bot.run()

        self.assertEqual(loops["n"], 1)
        self.assertTrue(bot.crashed)
        self.assertFalse(bot.running)
        alert = next(m for m in captured if "[봇 비정상 정지]" in m)
        self.assertIn("삼성전자", alert)
        self.assertIn("005930", alert)
        self.assertFalse(any("재시작" in m for m in captured))

    # T10 — restart on + 장중: 백오프 재시작 3회 후 4회째 포기 (재대조 동반)
    async def test_t10(self):
        settings.bot_restart_on_crash = True
        bot_module._BACKOFF = (0, 0, 0)
        bot = _bot()
        bot.hours = lambda: True
        captured: list[str] = []

        # msg 캡처
        async def cap(text: str) -> None:
            captured.append(text)

        bot.msg = cap

        loops = {"n": 0}

        # 호출 시 항상 예외를 내는 loop mock
        async def boom() -> None:
            loops["n"] += 1
            raise RuntimeError("kaboom")

        bot.loop = boom

        # 재시작 시 재대조(redo/pend) 호출 집계
        redos = {"n": 0}
        bot.redo = lambda: redos.__setitem__("n", redos["n"] + 1)
        pends = {"n": 0}

        async def fakepend() -> None:
            pends["n"] += 1

        bot.pend = fakepend

        await bot.run()

        self.assertEqual(loops["n"], 4)
        self.assertEqual(redos["n"], 3)
        self.assertEqual(pends["n"], 3)
        self.assertTrue(bot.crashed)
        self.assertTrue(any("재시작 포기" in m for m in captured))
        restarts = [m for m in captured if "[봇 재시작" in m and "포기" not in m]
        self.assertEqual(len(restarts), 3)

    # T11 — FR-08: start→자연종료→start 반복 시 tick 핸들러 누적 없음
    async def test_t11(self):
        base = len(bus._handlers.get("tick", []))
        bot = _bot()

        # msg 캡처(무시)
        async def cap(_text: str) -> None:
            return None

        bot.msg = cap

        for _ in range(2):
            release = asyncio.Event()

            # release 전까지 유지되는 loop mock (자연 종료 트리거용)
            async def waitloop() -> None:
                await release.wait()

            bot.loop = waitloop

            await bot.start()
            self.assertEqual(len(bus._handlers.get("tick", [])), base + 1)

            release.set()
            await bot._task
            self.assertEqual(len(bus._handlers.get("tick", [])), base)


if __name__ == "__main__":
    unittest.main()
