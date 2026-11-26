import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.trading.riskmonitor as rm_module
from service.trading.riskmonitor import RiskMonitor


# 알림 스텁
class NotifierStub:
    def __init__(self) -> None:
        self.msgs: list[str] = []

    async def msg(self, text: str) -> None:
        self.msgs.append(text)


# 포지션 스텁
class PositionStub:
    def __init__(self, bought: dict) -> None:
        self.bought = bought
        self.dropped: list[str] = []

    def drop(self, code: str) -> None:
        self.bought.pop(code, None)
        self.dropped.append(code)

    def acct(self, code: str, info: dict, stop_price=None) -> dict:
        return info

    def snap(self) -> None:
        return None


# 기록 스텁
class JournalStub:
    def __init__(self) -> None:
        self.recs: list = []

    async def rec(self, *a) -> None:
        self.recs.append(a)


# broker 스텁
class BrokerStub:
    def __init__(self, raw: int = 0) -> None:
        self._raw = raw
        self.sold: list = []

    async def sale(self, code: str, qty: int) -> dict:
        self.sold.append((code, qty))
        return {"success": True}

    async def raw(self, code: str) -> int:
        if isinstance(self._raw, Exception):
            raise self._raw
        return self._raw

    async def holdings(self):
        return {}, {}


# RiskMonitor — 손절/익절 청산 + 스로틀
class RiskMonitorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._sl = rm_module.stoploss

    def tearDown(self) -> None:
        rm_module.stoploss = self._sl

    # 손절 신호 → 매도 + 제거 + 기록
    async def testslexit(self):
        async def sl(*_a, **_k):
            return (True, -5.0, 66000)
        rm_module.stoploss = sl
        pos = PositionStub({"005930": {"name": "삼성", "qty": 1, "avg_price": 70000}})
        broker = BrokerStub(66000)
        journal = JournalStub()
        r = RiskMonitor(broker, pos, journal, NotifierStub(), {})
        await r.risk()
        self.assertEqual(broker.sold, [("005930", 1)])
        self.assertIn("005930", pos.dropped)
        self.assertEqual(len(journal.recs), 1)

    # 익절 신호 → 매도 + 제거
    async def testtpexit(self):
        async def sl(*_a, **_k):
            return (False, 100.0, 140000)
        rm_module.stoploss = sl
        pos = PositionStub({"005930": {"name": "삼성", "qty": 2, "avg_price": 70000}})
        broker = BrokerStub(140000)
        r = RiskMonitor(broker, pos, JournalStub(), NotifierStub(), {})
        await r.risk()
        self.assertEqual(broker.sold, [("005930", 2)])
        self.assertIn("005930", pos.dropped)

    # 손절 매도 성공 후 시세 재조회가 실패해도 판정가로 기록·제거 — 재조회하면 보유로 남아 재매도 시도
    async def testslprice(self):
        async def sl(*_a, **_k):
            return (True, -5.0, 66500)
        rm_module.stoploss = sl
        pos = PositionStub({"005930": {"name": "삼성", "qty": 1, "avg_price": 70000}})
        broker = BrokerStub(RuntimeError("kis down"))
        journal = JournalStub()
        r = RiskMonitor(broker, pos, journal, NotifierStub(), {})
        await r.risk()
        self.assertEqual(broker.sold, [("005930", 1)])
        self.assertIn("005930", pos.dropped)
        self.assertEqual(journal.recs[0][4], 66500)

    # 익절 매도 성공 후 시세 재조회가 실패해도 판정가로 기록·제거
    async def testtpprice(self):
        async def sl(*_a, **_k):
            return (False, 6.0, 74200)
        rm_module.stoploss = sl
        pos = PositionStub({"005930": {"name": "삼성", "qty": 2, "avg_price": 70000}})
        broker = BrokerStub(RuntimeError("kis down"))
        journal = JournalStub()
        r = RiskMonitor(broker, pos, journal, NotifierStub(), {})
        await r.risk()
        self.assertEqual(broker.sold, [("005930", 2)])
        self.assertIn("005930", pos.dropped)
        self.assertEqual(journal.recs[0][4], 74200)

    # riskgate — 2초 스로틀
    async def testthrottle(self):
        pos = PositionStub({"005930": {"name": "삼성", "qty": 1, "avg_price": 70000}})
        r = RiskMonitor(BrokerStub(), pos, JournalStub(), NotifierStub(), {})
        calls = {"n": 0}

        async def stub() -> None:
            calls["n"] += 1

        r.risk = stub
        await r.riskgate()
        await r.riskgate()
        self.assertEqual(calls["n"], 1)
        r._risk_last -= 3.0
        await r.riskgate()
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
