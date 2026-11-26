import inspect
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.ai.predict as predict_module
import service.trading.strategy as strat
from config import settings
from service.ai.spec import VERSION
from service.trading.strategy import Scorer
from service.trading.stoploss import stoploss
from service.trading.ports import Quotes, Account, Orders
from service.kis import kis


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def bars(n: int) -> list[dict]:
    out = []
    for i in range(n):
        o = 10000 + i * 10
        c = o + 5
        out.append({"open": o, "high": c + 10, "low": o - 10, "close": c, "volume": 1000 + i})
    return out


def bearbars(n: int) -> list[dict]:
    # Descending candles so RSI goes high (overbought) and structure is bearish
    out = []
    for i in range(n):
        o = 20000 - i * 10
        c = o - 5
        out.append({"open": o, "high": o + 5, "low": c - 10, "close": c, "volume": 1000 + i})
    return out


class BrokerStub:
    def __init__(self, daily, price, c15):
        self._daily = daily
        self._price = price
        self._c15 = c15

    async def daily(self, code: str, count: int = 60) -> list[dict]:
        return self._daily

    async def price(self, code: str) -> dict:
        return self._price

    async def c15(self, code: str) -> list[dict]:
        return self._c15


class BrokenBroker:
    async def daily(self, code: str, count: int = 60) -> list[dict]:
        raise RuntimeError("KIS daily unavailable")

    async def price(self, code: str) -> dict:
        raise RuntimeError("KIS price unavailable")

    async def c15(self, code: str) -> list[dict]:
        raise RuntimeError("KIS c15 unavailable")


# Quotes stub for stoploss tests
class QuoteStub:
    def __init__(self, price):
        self._p = price

    async def raw(self, code: str) -> int:
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


# ---------------------------------------------------------------------------
# Gap 1 (HIGH): sell-signal on real-data negative scoring path
# ---------------------------------------------------------------------------

class SaleSignalTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.eviction("fast:", "full:")

    def tearDown(self):
        strat._cache.eviction("fast:", "full:")

    async def testsalesig(self):
        # Patch _FACTORS to a deterministic single factor that scores -40
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (-40.0, "bearish"))]
            daily = bars(60)
            price = {"price": daily[-1]["close"]}
            s = Scorer(BrokerStub(daily, price, bars(30)))
            res = await s.verdict("S001")
            self.assertEqual(res["signal"], "sell")
            self.assertLessEqual(res["score"], -40)
        finally:
            strat._FACTORS = original

    async def testhold(self):
        # Score between -40 and +55 must produce hold
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (10.0, "neutral"))]
            daily = bars(60)
            price = {"price": daily[-1]["close"]}
            s = Scorer(BrokerStub(daily, price, bars(30)))
            res = await s.verdict("S002")
            self.assertEqual(res["signal"], "hold")
        finally:
            strat._FACTORS = original

    async def testsummary(self):
        # summary string includes score for sell branch
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (-50.0, "bearish"))]
            daily = bars(60)
            price = {"price": daily[-1]["close"]}
            s = Scorer(BrokerStub(daily, price, bars(30)))
            res = await s.verdict("S003")
            self.assertEqual(res["signal"], "sell")
            self.assertIn("매도 시그널", res["summary"])
        finally:
            strat._FACTORS = original


# ---------------------------------------------------------------------------
# Gap 2 (HIGH): prediction-present path bypasses cache
# ---------------------------------------------------------------------------

class ForecastBypassTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.eviction("fast:", "full:")

    def tearDown(self):
        strat._cache.eviction("fast:", "full:")

    def outlook(self, base_price: int) -> dict:
        return {
            "predictions": [
                {"close": base_price + 100 * i} for i in range(1, 6)
            ]
        }

    async def testnocache(self):
        daily = bars(60)
        base_price = daily[-1]["close"]
        fake = BrokerStub(daily, {"price": base_price}, bars(30))
        s = Scorer(fake)

        res1 = await s.verdict("P001", prediction=self.outlook(base_price))
        # Change price before second call
        new_price = base_price + 9999
        fake._price = {"price": new_price}
        res2 = await s.verdict("P001", prediction=self.outlook(new_price))

        # Cache bypass means res2 must reflect the updated price
        self.assertEqual(res2["price"], new_price)
        self.assertNotEqual(res2["price"], res1["price"])

    async def testckeynone(self):
        # ckey contract: returns None when prediction is not None
        s = Scorer()
        self.assertIsNone(s.ckey("X001", fast=False, prediction={"predictions": []}))
        self.assertIsNone(s.ckey("X001", fast=True,  prediction={"predictions": []}))

    async def testckeystr(self):
        s = Scorer()
        self.assertEqual(s.ckey("X001", fast=False, prediction=None), "full:X001")
        self.assertEqual(s.ckey("X001", fast=True,  prediction=None), "fast:X001")

    async def testpredscore(self):
        # pred() returns non-zero when prediction drives strong uptrend
        daily = bars(60)
        base_price = daily[-1]["close"]
        # 0-5 M-01: Direction 기준은 base_close — 기준가 없는 예측은 0점이라 필드를 넣음
        # 3-6: 문(predgate)이 열린 경우 — 검증된 공용 모델 예측 필드 + 플래그 True + 지금 읽힌 공용 모델 메타 검증됨
        prediction = {
            "predictions": [{"close": base_price + 500 * i} for i in range(1, 6)],
            "base_close": base_price,
            "source": "pooled", "validated": True, "spec": VERSION,
        }
        pool = {"model": None, "stats": {}, "meta": {"validated": True, "version": VERSION}}
        fake = BrokerStub(daily, {"price": base_price}, bars(30))
        s = Scorer(fake)
        with mock.patch.object(settings, "use_prediction", True), \
                mock.patch.object(predict_module.predictor, "bundle", lambda: pool):
            res = await s.verdict("P002", prediction=prediction)
        direction_factor = next(f for f in res["factors"] if f["name"] == "Direction")
        # Strong uptrend prediction must score positively
        self.assertGreater(direction_factor["score"], 0)


# ---------------------------------------------------------------------------
# Gap 3 (MEDIUM): evaluate broker exception → hold fallback
# ---------------------------------------------------------------------------

class VerdictFaultTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.eviction("fast:", "full:")

    def tearDown(self):
        strat._cache.eviction("fast:", "full:")

    async def testholdsig(self):
        s = Scorer(BrokenBroker())
        res = await s.verdict("E001")
        self.assertEqual(res["signal"], "hold")

    async def testzeroscore(self):
        s = Scorer(BrokenBroker())
        res = await s.verdict("E002")
        self.assertEqual(res["score"], 0)

    async def testnofactors(self):
        s = Scorer(BrokenBroker())
        res = await s.verdict("E003")
        self.assertEqual(res["factors"], [])

    async def testnostop(self):
        s = Scorer(BrokenBroker())
        res = await s.verdict("E004")
        self.assertIsNone(res["stop_price"])


# ---------------------------------------------------------------------------
# Gap 4 (MEDIUM): stoploss boundary and zero structural_price
# ---------------------------------------------------------------------------

class StopLossBoundaryTest(unittest.IsolatedAsyncioTestCase):

    async def testatstruct(self):
        # strict less-than: current == structural_price must NOT trigger the structural stop
        # fallback_pct를 충분히 낮춰 폴백이 독립적으로 트리거되지 않게 격리
        stop, pnl, _ = await stoploss(QuoteStub(950), "x", 1000, structural_price=950, fallback_pct=-10.0)
        self.assertFalse(stop)

    async def testbelowstruct(self):
        stop, _, _ = await stoploss(QuoteStub(949), "x", 1000, structural_price=950)
        self.assertTrue(stop)

    async def testzerostruct(self):
        # structural_price=0.0 is falsy; branch is skipped, fallback governs
        # pnl = (960 - 1000)/1000 * 100 = -4.0, fallback_pct=-3.0 → stop
        stop, pnl, _ = await stoploss(QuoteStub(960), "x", 1000, structural_price=0.0, fallback_pct=-3.0)
        self.assertTrue(stop)
        self.assertAlmostEqual(pnl, -4.0)

    async def testatpct(self):
        # pnl == fallback_pct (e.g. exactly -3.0) must trigger (<=)
        stop, pnl, _ = await stoploss(QuoteStub(970), "x", 1000, fallback_pct=-3.0)
        self.assertTrue(stop)
        self.assertAlmostEqual(pnl, -3.0)

    async def testabovepct(self):
        # pnl slightly above fallback_pct must not stop
        stop, _, _ = await stoploss(QuoteStub(971), "x", 1000, fallback_pct=-3.0)
        self.assertFalse(stop)


# ---------------------------------------------------------------------------
# Gap 5 (MEDIUM): individual factor parity for bb / vol / ob / fvg15
# ---------------------------------------------------------------------------

class FactorParityRemainingTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.eviction("fast:", "full:")

    def tearDown(self):
        strat._cache.eviction("fast:", "full:")

    async def respair(self):
        daily, c15 = bars(60), bars(30)
        price = {"price": daily[-1]["close"]}
        from service.market import indicators
        s = Scorer(BrokerStub(daily, price, c15))
        res = await s.verdict("FAC001")
        ind = indicators.summary(daily)
        cp = price["price"]
        return res, s, ind, daily, c15, cp

    async def testbollinger(self):
        res, s, ind, daily, c15, cp = await self.respair()
        expected = round(s.bb(ind["bollinger"])[0], 1)
        self.assertEqual(res["factors"][2]["score"], expected)

    async def testvolatility(self):
        res, s, ind, daily, c15, cp = await self.respair()
        expected = round(s.vol(daily, cp)[0], 1)
        self.assertEqual(res["factors"][3]["score"], expected)

    async def testorderblock(self):
        res, s, ind, daily, c15, cp = await self.respair()
        expected = round(s.ob(daily, cp)[0], 1)
        self.assertEqual(res["factors"][6]["score"], expected)

    async def testfvg15m(self):
        res, s, ind, daily, c15, cp = await self.respair()
        expected = round(s.fvg15(c15, cp)[0], 1)
        self.assertEqual(res["factors"][7]["score"], expected)


# ---------------------------------------------------------------------------
# Gap 6 (MEDIUM): Protocol conformance — async callability, not just presence
# ---------------------------------------------------------------------------

class PortsCallabilityTest(unittest.TestCase):

    def testraw(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.raw))

    def testprice(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.price))

    def testdaily(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.daily))

    def testc15(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.c15))

    def testindices(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.indices))

    def testholdings(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.holdings))

    def testcash(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.cash))

    def testpurchase(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.purchase))

    def testsale(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.sale))


# ---------------------------------------------------------------------------
# Gap 7 (LOW): fast vs full cache key isolation
# ---------------------------------------------------------------------------

class CacheKeyIsolationTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.eviction("fast:", "full:")

    def tearDown(self):
        strat._cache.eviction("fast:", "full:")

    async def testfastkey(self):
        daily, c15 = bars(60), bars(30)
        price = {"price": daily[-1]["close"]}
        s = Scorer(BrokerStub(daily, price, c15))

        fast_res = await s.verdict("K001", fast=True)
        full_res = await s.verdict("K001", fast=False)

        # Fast result has "1단계 스크리닝에서 제외" for FVG 15m;
        # full result must not carry that reason
        fast_fvg15 = next(f for f in fast_res["factors"] if f["name"] == "FVG 15m")
        full_fvg15 = next(f for f in full_res["factors"] if f["name"] == "FVG 15m")
        self.assertEqual(fast_fvg15["reason"], "1단계 스크리닝에서 제외")
        self.assertNotEqual(full_fvg15["reason"], "1단계 스크리닝에서 제외")

    async def testfullkey(self):
        daily, c15 = bars(60), bars(30)
        price = {"price": daily[-1]["close"]}
        s = Scorer(BrokerStub(daily, price, c15))

        # Evaluate full first, then fast — fast must not inherit full's 15m score
        full_res = await s.verdict("K002", fast=False)
        fast_res = await s.verdict("K002", fast=True)

        fast_fvg15 = next(f for f in fast_res["factors"] if f["name"] == "FVG 15m")
        self.assertEqual(fast_fvg15["reason"], "1단계 스크리닝에서 제외")
        self.assertEqual(fast_fvg15["score"], 0.0)


# ---------------------------------------------------------------------------
# Flakiness guard: _FACTORS monkeypatch + cache interaction
# ---------------------------------------------------------------------------

class FactorPatchCacheFlakeTest(unittest.IsolatedAsyncioTestCase):
    # Demonstrates that without cache invalidation, a second test run in the
    # same process would serve stale cached values and ignore _FACTORS patches.
    # setUp/tearDown invalidation is the fix — this test verifies the fix works.

    def setUp(self):
        strat._cache.eviction("fast:", "full:")

    def tearDown(self):
        strat._cache.eviction("fast:", "full:")

    async def teststalepatch(self):
        daily = bars(60)
        price = {"price": daily[-1]["close"]}
        s = Scorer(BrokerStub(daily, price, bars(30)))

        # Pre-populate cache with a buy result
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (55.0, "buy"))]
            res1 = await s.verdict("M001")
            self.assertEqual(res1["signal"], "buy")
        finally:
            strat._FACTORS = original

        # Invalidate so next call actually runs the real registry
        strat._cache.eviction("fast:", "full:")

        # Real factors on bars(60) score somewhere — not necessarily buy
        # The key assertion: it must NOT return the cached {"signal": "buy"} from the patch
        original2 = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (-50.0, "sell"))]
            res2 = await s.verdict("M001")
            self.assertEqual(res2["signal"], "sell")
        finally:
            strat._FACTORS = original2


if __name__ == "__main__":
    unittest.main()
