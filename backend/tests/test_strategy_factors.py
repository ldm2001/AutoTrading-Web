import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.market import indicators
import service.trading.strategy as strat
from service.trading.strategy import Scorer


# 더미 일봉/분봉 (상승 추세)
def _candles(n: int) -> list[dict]:
    out = []
    for i in range(n):
        o = 10000 + i * 10
        c = o + 5
        out.append({"open": o, "high": c + 10, "low": o - 10, "close": c, "volume": 1000 + i})
    return out


# Quotes 스텁 — daily/price/c15
class _FakeBroker:
    def __init__(self, daily, price, c15):
        self._daily, self._price, self._c15 = daily, price, c15

    async def daily(self, code: str, count: int = 60) -> list[dict]:
        return self._daily

    async def price(self, code: str) -> dict:
        return self._price

    async def c15(self, code: str) -> list[dict]:
        return self._c15


# 팩터 레지스트리 — 직접 메서드 호출과 동일성(특성화) + fast 스킵
class StrategyFactorsTest(unittest.IsolatedAsyncioTestCase):
    # full 평가: 9팩터 이름/순서/가중치 + 점수가 직접 호출과 일치
    async def test_registry_parity_full(self):
        daily, c15 = _candles(60), _candles(30)
        price = {"price": daily[-1]["close"]}
        s = Scorer(_FakeBroker(daily, price, c15))

        res = await s.evaluate("000001")

        names = [f["name"] for f in res["factors"]]
        self.assertEqual(
            names,
            ["RSI", "MACD", "Bollinger", "Volatility", "Direction",
             "FVG", "OrderBlock", "FVG 15m", "Structure"],
        )
        self.assertEqual([f["max"] for f in res["factors"]], [15, 15, 10, 12, 10, 8, 7, 15, 8])

        ind = indicators.summary(daily)
        cp = price["price"]
        self.assertEqual(res["factors"][0]["score"], round(s.rsi(ind["rsi"])[0], 1))
        self.assertEqual(res["factors"][1]["score"], round(s.macd(ind["macd"])[0], 1))
        self.assertEqual(res["factors"][5]["score"], round(s.fvg(daily, cp)[0], 1))
        self.assertEqual(res["factors"][8]["score"], round(s.struct(c15)[0], 1))
        # total = 9팩터 원점수 합의 반올림 (수치 동일성)
        expected = (s.rsi(ind["rsi"])[0] + s.macd(ind["macd"])[0] + s.bb(ind["bollinger"])[0]
                    + s.vol(daily, cp)[0] + s.pred(None, cp)[0] + s.fvg(daily, cp)[0]
                    + s.ob(daily, cp)[0] + s.fvg15(c15, cp)[0] + s.struct(c15)[0])
        self.assertEqual(res["score"], round(expected, 1))

    # fast 평가: 15분봉 팩터는 스킵 사유 + 0점
    async def test_fast_skips_15m(self):
        daily = _candles(60)
        price = {"price": daily[-1]["close"]}
        s = Scorer(_FakeBroker(daily, price, []))

        res = await s.evaluate("000002", fast=True)

        f15 = next(f for f in res["factors"] if f["name"] == "FVG 15m")
        st = next(f for f in res["factors"] if f["name"] == "Structure")
        self.assertEqual(f15["reason"], "1단계 스크리닝에서 제외")
        self.assertEqual(f15["score"], 0.0)
        self.assertEqual(st["reason"], "1단계 스크리닝에서 제외")
        self.assertEqual(st["score"], 0.0)

    # full 모드 + 15분봉 빈 데이터 → 15m 팩터 사유 "15분봉 데이터 부족"
    async def test_full_no_15m_reason(self):
        daily = _candles(60)
        price = {"price": daily[-1]["close"]}
        s = Scorer(_FakeBroker(daily, price, []))

        res = await s.evaluate("000003")

        f15 = next(f for f in res["factors"] if f["name"] == "FVG 15m")
        st = next(f for f in res["factors"] if f["name"] == "Structure")
        self.assertEqual(f15["reason"], "15분봉 데이터 부족")
        self.assertEqual(st["reason"], "15분봉 데이터 부족")
        self.assertEqual(f15["score"], 0.0)

    # broker 미주입 시 Scorer.broker 접근은 fail-fast (서비스 로케이터 제거 검증)
    def test_unbound_broker_raises(self):
        with self.assertRaises(RuntimeError):
            _ = Scorer().broker

    # 시그널 임계값 경계 — 55 buy / -40 sell / 그 사이 hold
    async def test_signal_thresholds(self):
        daily = _candles(60)
        price = {"price": daily[-1]["close"]}
        s = Scorer(_FakeBroker(daily, price, _candles(30)))

        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (55.0, "x"))]
            self.assertEqual((await s.evaluate("000004"))["signal"], "buy")
            strat._FACTORS = [("T", 100, lambda sc, fi: (-40.0, "x"))]
            self.assertEqual((await s.evaluate("000005"))["signal"], "sell")
            strat._FACTORS = [("T", 100, lambda sc, fi: (0.0, "x"))]
            self.assertEqual((await s.evaluate("000006"))["signal"], "hold")
        finally:
            strat._FACTORS = original

    # 동일 코드 재평가 시 캐시 적중 — 2차 호출 전 데이터를 바꿔도 결과 유지
    async def test_cache_hit(self):
        daily = _candles(60)
        fake = _FakeBroker(daily, {"price": daily[-1]["close"]}, _candles(30))
        s = Scorer(fake)

        res1 = await s.evaluate("000007")
        fake._price = {"price": 99999}  # 캐시 적중이면 반영 안 됨
        res2 = await s.evaluate("000007")

        self.assertEqual(res2["price"], res1["price"])
        self.assertNotEqual(res2["price"], 99999)


if __name__ == "__main__":
    unittest.main()
