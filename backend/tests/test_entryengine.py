import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
import service.trading.entryengine as entry_module
from service.trading.entryengine import EntryEngine


# 알림 스텁
class _Notifier:
    def __init__(self) -> None:
        self.msgs: list[str] = []

    async def msg(self, text: str) -> None:
        self.msgs.append(text)


# 포지션 스텁 — pos_result가 있으면 잔고 반영된 것으로 등록
class _Positions:
    def __init__(self, pos_result: dict | None = None) -> None:
        self.bought: dict[str, dict] = {}
        self.pending_buys: set[str] = set()
        self.pending_stops: dict[str, float] = {}
        self._pos_result = pos_result

    async def pos(self, code: str, stop_price=None) -> dict | None:
        if self._pos_result is not None:
            self.bought[code] = self._pos_result
        return self._pos_result


# 기록 스텁
class _Journal:
    def __init__(self) -> None:
        self.recs: list = []

    async def rec(self, *a) -> None:
        self.recs.append(a)


# broker 스텁
class _Broker:
    def __init__(self, indices=None) -> None:
        self.buys: list[tuple[str, int]] = []
        self._indices = indices

    async def buy(self, code: str, qty: int) -> dict:
        self.buys.append((code, qty))
        return {"success": True}

    async def indices(self):
        if isinstance(self._indices, Exception):
            raise self._indices
        return self._indices or []


# 매수 시그널 평가 스텁
def _buysig(score: float, price: int = 1000, stop_price=900):
    async def fake(_sym: str, _prediction: object) -> dict:
        return {
            "signal": "buy",
            "score": score,
            "price": price,
            "factors": [],
            "stop_price": stop_price,
        }
    return fake


# EntryEngine — 매수 진입 판단 + 시장 국면 게이트
class EntryEngineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._evaluate = entry_module.evaluate
        self._wfin = entry_module.wfin
        self._regime = entry_module.regime_state
        entry_module.wfin = lambda *_a, **_k: None

    def tearDown(self) -> None:
        entry_module.evaluate = self._evaluate
        entry_module.wfin = self._wfin
        entry_module.regime_state = self._regime

    # 매수 시그널 → 주문 + 포지션 등록 + 기록 + pending 해제
    async def test_buy_signal_orders_and_records(self):
        entry_module.evaluate = _buysig(settings.buy_score_threshold + 10)
        pos = _Positions(pos_result={"name": "삼성", "qty": 10, "avg_price": 1000})
        broker = _Broker()
        journal = _Journal()
        e = EntryEngine(broker, pos, journal, _Notifier(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [("005930", 10)])
        self.assertIn("005930", pos.bought)
        self.assertEqual(len(journal.recs), 1)
        self.assertNotIn("005930", pos.pending_buys)

    # 임계 미달 → 주문 없음
    async def test_below_threshold_no_order(self):
        entry_module.evaluate = _buysig(settings.buy_score_threshold - 1)
        broker = _Broker()
        e = EntryEngine(broker, _Positions(), _Journal(), _Notifier(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [])

    # 예산 미달(수량 0) → 주문 없음
    async def test_zero_qty_no_order(self):
        entry_module.evaluate = _buysig(settings.buy_score_threshold + 10, price=20_000)
        broker = _Broker()
        e = EntryEngine(broker, _Positions(), _Journal(), _Notifier(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [])

    # 접수 후 잔고 미반영 → pending 유지 + 손절가 보관
    async def test_pending_position_keeps_flag(self):
        entry_module.evaluate = _buysig(settings.buy_score_threshold + 10)
        pos = _Positions(pos_result=None)
        broker = _Broker()
        e = EntryEngine(broker, pos, _Journal(), _Notifier(), {})
        await e.ent("005930", 10_000)
        self.assertIn("005930", pos.pending_buys)
        self.assertEqual(pos.pending_stops.get("005930"), 900)

    # 보유 중 종목 재진입 차단
    async def test_held_symbol_skipped(self):
        entry_module.evaluate = _buysig(settings.buy_score_threshold + 10)
        pos = _Positions()
        pos.bought["005930"] = {"qty": 1}
        broker = _Broker()
        e = EntryEngine(broker, pos, _Journal(), _Notifier(), {})
        await e.ent("005930", 10_000)
        self.assertEqual(broker.buys, [])

    # gate — risk_off면 차단 + 국면 전환 시 1회만 알림
    async def test_gate_risk_off_blocks(self):
        entry_module.regime_state = lambda _idx: {
            "state": "risk_off", "reason": "지수 급락", "allow_new_buys": False,
        }
        notifier = _Notifier()
        e = EntryEngine(_Broker(indices=[]), _Positions(), _Journal(), notifier, {})
        self.assertFalse(await e.gate())
        self.assertFalse(await e.gate())
        self.assertEqual(len(notifier.msgs), 1)

    # gate — 지수 조회 실패 시 fail-open 허용
    async def test_gate_failure_allows(self):
        e = EntryEngine(_Broker(indices=RuntimeError("api down")), _Positions(), _Journal(), _Notifier(), {})
        self.assertTrue(await e.gate())


if __name__ == "__main__":
    unittest.main()
