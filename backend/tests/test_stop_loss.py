import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.trading.stoploss import stoploss


# raw()만 노출하는 Quotes 스텁 (값 또는 예외)
class QuoteStub:
    def __init__(self, price):
        self._p = price

    async def raw(self, code: str) -> int:
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


# stoploss 순수 함수 검증
class StopLossTest(unittest.IsolatedAsyncioTestCase):
    # 구조적 손절가 하회 시 손절
    async def teststructhit(self):
        stop, pnl, _ = await stoploss(QuoteStub(900), "x", 1000, structural_price=950)
        self.assertTrue(stop)

    # 구조적 손절가 위 + 폴백 미달 → 보유
    async def teststructok(self):
        stop, _, _ = await stoploss(QuoteStub(1000), "x", 1000, structural_price=950, fallback_pct=-3.0)
        self.assertFalse(stop)

    # 구조적 없음 + 고정 % 하회 → 손절
    async def testfallback(self):
        stop, pnl, _ = await stoploss(QuoteStub(960), "x", 1000, fallback_pct=-3.0)
        self.assertTrue(stop)
        self.assertAlmostEqual(pnl, -4.0)

    # 판정에 쓴 현재가를 함께 반환 — 호출부가 매도 후 시세를 재조회하지 않도록
    async def testdecision(self):
        _, _, current = await stoploss(QuoteStub(960), "x", 1000, fallback_pct=-3.0)
        self.assertEqual(current, 960)

    # 시세 조회 예외는 그대로 전파 (fail-safe)
    async def testexception(self):
        with self.assertRaises(RuntimeError):
            await stoploss(QuoteStub(RuntimeError("kis down")), "x", 1000)


if __name__ == "__main__":
    unittest.main()
