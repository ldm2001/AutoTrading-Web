import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.trading.bot as bot_module
from service.trading.bot import Bot


# msg 모킹용 no-op
async def noop(_text: str) -> None:
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


# FR-01/02 — 손절 모니터링 fail-safe (예외 전파 + 연속 실패 경보, 스로틀)
class StopLossFailsafeTest(unittest.IsolatedAsyncioTestCase):
    # 모듈 전역 notify/sl 보존 후 notify는 no-op으로 교체
    def setUp(self) -> None:
        self._stoploss = bot_module.stoploss

    # 교체했던 전역 복원
    def tearDown(self) -> None:
        bot_module.stoploss = self._stoploss

    # T1 — 시세 조회 3회 연속 실패 시 손절 모니터링 장애 경보 정확히 1회
    async def test_t1(self):
        bot = _bot()
        captured: list[str] = []

        # msg 캡처
        async def cap(text: str) -> None:
            captured.append(text)

        bot.msg = cap
        bot.bought = {"005930": {"name": "삼성전자", "qty": 1, "avg_price": 70000}}

        # 시세 조회가 항상 실패하는 mock
        async def boom(*_a, **_k):
            raise RuntimeError("KIS raw timeout")

        bot_module.stoploss = boom

        for _ in range(3):
            await bot.risk()

        alerts = [m for m in captured if "[손절 모니터링 장애]" in m]
        self.assertEqual(len(alerts), 1)
        self.assertIn("삼성전자", alerts[0])
        self.assertIn("005930", alerts[0])
        self.assertEqual(bot._sl_fails["005930"], 3)

    # T2 — 실패 2회 후 성공하면 연속 실패 카운터 리셋, 경보 0회
    async def test_t2(self):
        bot = _bot()
        captured: list[str] = []

        # msg 캡처
        async def cap(text: str) -> None:
            captured.append(text)

        bot.msg = cap
        bot.bought = {"005930": {"name": "삼성전자", "qty": 1, "avg_price": 70000}}

        calls = {"n": 0}

        # 2회 실패 후 3회째 성공하는 mock
        async def flaky(*_a, **_k):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise RuntimeError("transient")
            return (False, 0.0)

        bot_module.stoploss = flaky

        for _ in range(3):
            await bot.risk()

        alerts = [m for m in captured if "[손절 모니터링 장애]" in m]
        self.assertEqual(len(alerts), 0)
        self.assertNotIn("005930", bot._sl_fails)

    # T3 — riskgate는 시간창과 무관하게 동작하되 최소 2초 간격으로 스로틀
    async def test_t3(self):
        bot = _bot()
        bot.bought = {"005930": {"name": "삼성전자", "qty": 1, "avg_price": 70000}}
        riskcalls = {"n": 0}

        # risk 호출 횟수만 집계하는 mock
        async def fakerisk() -> None:
            riskcalls["n"] += 1

        bot.risk = fakerisk

        await bot.riskgate()
        self.assertEqual(riskcalls["n"], 1)

        # 2초 이내 재호출 → 스로틀로 스킵
        await bot.riskgate()
        self.assertEqual(riskcalls["n"], 1)

        # 2초 경과 시뮬레이션 → 재호출 허용
        bot._risk_last -= 3.0
        await bot.riskgate()
        self.assertEqual(riskcalls["n"], 2)


if __name__ == "__main__":
    unittest.main()
