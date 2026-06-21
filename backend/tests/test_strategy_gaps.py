import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.trading.strategy as strat
from service.trading.strategy import Scorer
from service.trading.stop_loss import stop_loss
from service.trading.ports import Quotes, Account, Orders
from service.kis import kis


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _candles(n: int) -> list[dict]:
    out = []
    for i in range(n):
        o = 10000 + i * 10
        c = o + 5
        out.append({"open": o, "high": c + 10, "low": o - 10, "close": c, "volume": 1000 + i})
    return out


def _bearish_candles(n: int) -> list[dict]:
    # Descending candles so RSI goes high (overbought) and structure is bearish
    out = []
    for i in range(n):
        o = 20000 - i * 10
        c = o - 5
        out.append({"open": o, "high": o + 5, "low": c - 10, "close": c, "volume": 1000 + i})
    return out


class _FakeBroker:
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


class _BrokenBroker:
    async def daily(self, code: str, count: int = 60) -> list[dict]:
        raise RuntimeError("KIS daily unavailable")

    async def price(self, code: str) -> dict:
        raise RuntimeError("KIS price unavailable")

    async def c15(self, code: str) -> list[dict]:
        raise RuntimeError("KIS c15 unavailable")


# Quotes stub for stop_loss tests
class _Q:
    def __init__(self, price):
        self._p = price

    async def raw(self, code: str) -> int:
        if isinstance(self._p, Exception):
            raise self._p
        return self._p


# ---------------------------------------------------------------------------
# Gap 1 (HIGH): sell-signal on real-data negative scoring path
# ---------------------------------------------------------------------------

class SellSignalTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.invalidate("fast:", "full:")

    def tearDown(self):
        strat._cache.invalidate("fast:", "full:")

    async def test_sell_signal_score_below_threshold(self):
        # Patch _FACTORS to a deterministic single factor that scores -40
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (-40.0, "bearish"))]
            daily = _candles(60)
            price = {"price": daily[-1]["close"]}
            s = Scorer(_FakeBroker(daily, price, _candles(30)))
            res = await s.evaluate("S001")
            self.assertEqual(res["signal"], "sell")
            self.assertLessEqual(res["score"], -40)
        finally:
            strat._FACTORS = original

    async def test_signal_hold_between_thresholds(self):
        # Score between -40 and +55 must produce hold
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (10.0, "neutral"))]
            daily = _candles(60)
            price = {"price": daily[-1]["close"]}
            s = Scorer(_FakeBroker(daily, price, _candles(30)))
            res = await s.evaluate("S002")
            self.assertEqual(res["signal"], "hold")
        finally:
            strat._FACTORS = original

    async def test_sell_summary_string_contains_score(self):
        # summary string includes score for sell branch
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (-50.0, "bearish"))]
            daily = _candles(60)
            price = {"price": daily[-1]["close"]}
            s = Scorer(_FakeBroker(daily, price, _candles(30)))
            res = await s.evaluate("S003")
            self.assertEqual(res["signal"], "sell")
            self.assertIn("매도 시그널", res["summary"])
        finally:
            strat._FACTORS = original


# ---------------------------------------------------------------------------
# Gap 2 (HIGH): prediction-present path bypasses cache
# ---------------------------------------------------------------------------

class PredictionCacheBypassTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.invalidate("fast:", "full:")

    def tearDown(self):
        strat._cache.invalidate("fast:", "full:")

    def _prediction(self, base_price: int) -> dict:
        return {
            "predictions": [
                {"close": base_price + 100 * i} for i in range(1, 6)
            ]
        }

    async def test_prediction_result_not_served_from_cache(self):
        daily = _candles(60)
        base_price = daily[-1]["close"]
        fake = _FakeBroker(daily, {"price": base_price}, _candles(30))
        s = Scorer(fake)

        res1 = await s.evaluate("P001", prediction=self._prediction(base_price))
        # Change price before second call
        new_price = base_price + 9999
        fake._price = {"price": new_price}
        res2 = await s.evaluate("P001", prediction=self._prediction(new_price))

        # Cache bypass means res2 must reflect the updated price
        self.assertEqual(res2["price"], new_price)
        self.assertNotEqual(res2["price"], res1["price"])

    async def test_ckey_returns_none_when_prediction_present(self):
        # ckey contract: returns None when prediction is not None
        s = Scorer()
        self.assertIsNone(s.ckey("X001", fast=False, prediction={"predictions": []}))
        self.assertIsNone(s.ckey("X001", fast=True,  prediction={"predictions": []}))

    async def test_ckey_returns_string_when_no_prediction(self):
        s = Scorer()
        self.assertEqual(s.ckey("X001", fast=False, prediction=None), "full:X001")
        self.assertEqual(s.ckey("X001", fast=True,  prediction=None), "fast:X001")

    async def test_prediction_present_path_uses_pred_score(self):
        # pred() returns non-zero when prediction drives strong uptrend
        daily = _candles(60)
        base_price = daily[-1]["close"]
        prediction = {
            "predictions": [{"close": base_price + 500 * i} for i in range(1, 6)]
        }
        fake = _FakeBroker(daily, {"price": base_price}, _candles(30))
        s = Scorer(fake)
        res = await s.evaluate("P002", prediction=prediction)
        direction_factor = next(f for f in res["factors"] if f["name"] == "Direction")
        # Strong uptrend prediction must score positively
        self.assertGreater(direction_factor["score"], 0)


# ---------------------------------------------------------------------------
# Gap 3 (MEDIUM): evaluate broker exception → hold fallback
# ---------------------------------------------------------------------------

class EvaluateBrokerExceptionTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.invalidate("fast:", "full:")

    def tearDown(self):
        strat._cache.invalidate("fast:", "full:")

    async def test_broker_exception_returns_hold_signal(self):
        s = Scorer(_BrokenBroker())
        res = await s.evaluate("E001")
        self.assertEqual(res["signal"], "hold")

    async def test_broker_exception_returns_zero_score(self):
        s = Scorer(_BrokenBroker())
        res = await s.evaluate("E002")
        self.assertEqual(res["score"], 0)

    async def test_broker_exception_returns_empty_factors(self):
        s = Scorer(_BrokenBroker())
        res = await s.evaluate("E003")
        self.assertEqual(res["factors"], [])

    async def test_broker_exception_stop_price_is_none(self):
        s = Scorer(_BrokenBroker())
        res = await s.evaluate("E004")
        self.assertIsNone(res["stop_price"])


# ---------------------------------------------------------------------------
# Gap 4 (MEDIUM): stop_loss boundary and zero structural_price
# ---------------------------------------------------------------------------

class StopLossBoundaryTest(unittest.IsolatedAsyncioTestCase):

    async def test_current_equals_structural_price_does_not_trigger(self):
        # strict less-than: current == structural_price must NOT stop
        stop, pnl = await stop_loss(_Q(950), "x", 1000, structural_price=950)
        self.assertFalse(stop)

    async def test_current_one_below_structural_triggers(self):
        stop, _ = await stop_loss(_Q(949), "x", 1000, structural_price=950)
        self.assertTrue(stop)

    async def test_structural_price_zero_is_skipped_silently(self):
        # structural_price=0.0 is falsy; branch is skipped, fallback governs
        # pnl = (960 - 1000)/1000 * 100 = -4.0, fallback_pct=-3.0 → stop
        stop, pnl = await stop_loss(_Q(960), "x", 1000, structural_price=0.0, fallback_pct=-3.0)
        self.assertTrue(stop)
        self.assertAlmostEqual(pnl, -4.0)

    async def test_fallback_pct_boundary_exactly_equal_triggers(self):
        # pnl == fallback_pct (e.g. exactly -3.0) must trigger (<=)
        stop, pnl = await stop_loss(_Q(970), "x", 1000, fallback_pct=-3.0)
        self.assertTrue(stop)
        self.assertAlmostEqual(pnl, -3.0)

    async def test_fallback_pct_one_tick_above_does_not_trigger(self):
        # pnl slightly above fallback_pct must not stop
        stop, _ = await stop_loss(_Q(971), "x", 1000, fallback_pct=-3.0)
        self.assertFalse(stop)


# ---------------------------------------------------------------------------
# Gap 5 (MEDIUM): individual factor parity for bb / vol / ob / fvg15
# ---------------------------------------------------------------------------

class FactorParityRemainingTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.invalidate("fast:", "full:")

    def tearDown(self):
        strat._cache.invalidate("fast:", "full:")

    async def _res_and_scorer(self):
        daily, c15 = _candles(60), _candles(30)
        price = {"price": daily[-1]["close"]}
        from service.market import indicators
        s = Scorer(_FakeBroker(daily, price, c15))
        res = await s.evaluate("FAC001")
        ind = indicators.summary(daily)
        cp = price["price"]
        return res, s, ind, daily, c15, cp

    async def test_bollinger_factor_matches_method(self):
        res, s, ind, daily, c15, cp = await self._res_and_scorer()
        expected = round(s.bb(ind["bollinger"])[0], 1)
        self.assertEqual(res["factors"][2]["score"], expected)

    async def test_volatility_factor_matches_method(self):
        res, s, ind, daily, c15, cp = await self._res_and_scorer()
        expected = round(s.vol(daily, cp)[0], 1)
        self.assertEqual(res["factors"][3]["score"], expected)

    async def test_orderblock_factor_matches_method(self):
        res, s, ind, daily, c15, cp = await self._res_and_scorer()
        expected = round(s.ob(daily, cp)[0], 1)
        self.assertEqual(res["factors"][6]["score"], expected)

    async def test_fvg15m_factor_matches_method(self):
        res, s, ind, daily, c15, cp = await self._res_and_scorer()
        expected = round(s.fvg15(c15, cp)[0], 1)
        self.assertEqual(res["factors"][7]["score"], expected)


# ---------------------------------------------------------------------------
# Gap 6 (MEDIUM): Protocol conformance — async callability, not just presence
# ---------------------------------------------------------------------------

class PortsCallabilityTest(unittest.TestCase):

    def test_quotes_raw_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.raw))

    def test_quotes_price_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.price))

    def test_quotes_daily_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.daily))

    def test_quotes_c15_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.c15))

    def test_quotes_indices_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.indices))

    def test_account_holdings_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.holdings))

    def test_account_cash_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.cash))

    def test_orders_buy_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.buy))

    def test_orders_sell_is_async(self):
        self.assertTrue(inspect.iscoroutinefunction(kis.sell))


# ---------------------------------------------------------------------------
# Gap 7 (LOW): fast vs full cache key isolation
# ---------------------------------------------------------------------------

class CacheKeyIsolationTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        strat._cache.invalidate("fast:", "full:")

    def tearDown(self):
        strat._cache.invalidate("fast:", "full:")

    async def test_fast_result_not_returned_for_full_evaluation(self):
        daily, c15 = _candles(60), _candles(30)
        price = {"price": daily[-1]["close"]}
        s = Scorer(_FakeBroker(daily, price, c15))

        fast_res = await s.evaluate("K001", fast=True)
        full_res = await s.evaluate("K001", fast=False)

        # Fast result has "1단계 스크리닝에서 제외" for FVG 15m;
        # full result must not carry that reason
        fast_fvg15 = next(f for f in fast_res["factors"] if f["name"] == "FVG 15m")
        full_fvg15 = next(f for f in full_res["factors"] if f["name"] == "FVG 15m")
        self.assertEqual(fast_fvg15["reason"], "1단계 스크리닝에서 제외")
        self.assertNotEqual(full_fvg15["reason"], "1단계 스크리닝에서 제외")

    async def test_full_result_not_returned_for_fast_evaluation(self):
        daily, c15 = _candles(60), _candles(30)
        price = {"price": daily[-1]["close"]}
        s = Scorer(_FakeBroker(daily, price, c15))

        # Evaluate full first, then fast — fast must not inherit full's 15m score
        full_res = await s.evaluate("K002", fast=False)
        fast_res = await s.evaluate("K002", fast=True)

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
        strat._cache.invalidate("fast:", "full:")

    def tearDown(self):
        strat._cache.invalidate("fast:", "full:")

    async def test_monkeypatch_not_masked_by_stale_cache(self):
        daily = _candles(60)
        price = {"price": daily[-1]["close"]}
        s = Scorer(_FakeBroker(daily, price, _candles(30)))

        # Pre-populate cache with a buy result
        original = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (55.0, "buy"))]
            res1 = await s.evaluate("M001")
            self.assertEqual(res1["signal"], "buy")
        finally:
            strat._FACTORS = original

        # Invalidate so next call actually runs the real registry
        strat._cache.invalidate("fast:", "full:")

        # Real factors on _candles(60) score somewhere — not necessarily buy
        # The key assertion: it must NOT return the cached {"signal": "buy"} from the patch
        original2 = strat._FACTORS
        try:
            strat._FACTORS = [("T", 100, lambda sc, fi: (-50.0, "sell"))]
            res2 = await s.evaluate("M001")
            self.assertEqual(res2["signal"], "sell")
        finally:
            strat._FACTORS = original2


if __name__ == "__main__":
    unittest.main()
