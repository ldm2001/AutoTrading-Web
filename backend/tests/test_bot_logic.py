import datetime
import sys
import unittest
from pathlib import Path
import service.trading.bot as bot_module
import service.trading.entryengine as entry_module
import service.trading.journal as journal_module
from service.trading.bot import Bot
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

async def noop(_text: str) -> None:
    return None

class CacheStub:
    redis = None

class QueueStub:
    async def boot(self) -> None:
        return None

class BrokerStub:
    def __init__(self, holdings: dict[str, dict] | None = None) -> None:
        self.cache = CacheStub()
        self._holdings = holdings or {}
        self.sells: list[tuple[str, int]] = []
        self.buys: list[tuple[str, int]] = []
        self.sell_results: dict[str, bool] = {}
        self.buy_delay = 0.0
        self.index_rows: list[dict] = []

    async def cash(self) -> int:
        return 1_000_000

    async def indices(self, stale: bool = True) -> list[dict]:
        return self.index_rows

    async def holdings(self) -> tuple[dict[str, dict], dict]:
        return self._holdings, {
            "scts_evlu_amt": "0",
            "evlu_pfls_smtl_amt": "0",
            "tot_evlu_amt": "0",
        }

    async def raw(self, code: str) -> int:
        return self._holdings.get(code, {}).get("current_price", 0)

    async def purchase(self, code: str, qty: int) -> dict:
        if self.buy_delay:
            import asyncio
            await asyncio.sleep(self.buy_delay)
        self.buys.append((code, qty))
        return {"success": True, "data": {"msg1": "accepted"}}

    async def sale(self, code: str, qty: int) -> dict:
        self.sells.append((code, qty))
        return {"success": self.sell_results.get(code, True)}


class TradingBotLogicTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_append = getattr(bot_module, "order_log_append", None)
        if self._original_append:
            bot_module.order_log_append = lambda _entry: None
        self._original_trade_append = getattr(journal_module, "traderec", None)
        if self._original_trade_append:
            journal_module.traderec = lambda _entry: None

    def tearDown(self) -> None:
        if self._original_append:
            bot_module.order_log_append = self._original_append
        if self._original_trade_append:
            journal_module.traderec = self._original_trade_append

    async def testacctholds(self):
        broker = BrokerStub({
            "999999": {
                "code": "999999",
                "name": "수동보유",
                "qty": 3,
                "avg_price": 1000,
                "current_price": 1100,
                "eval_amount": 3300,
                "profit_loss_percent": 10.0,
            }
        })
        bot = Bot(broker, QueueStub(), {}, lambda: [])

        await bot.loop()

        self.assertEqual(bot.bought, {})

    async def testsalefault(self):
        broker = BrokerStub({
            "111111": {"code": "111111", "name": "성공", "qty": 2, "avg_price": 1000, "current_price": 1100},
            "222222": {"code": "222222", "name": "실패", "qty": 4, "avg_price": 2000, "current_price": 1900},
            "999999": {"code": "999999", "name": "수동", "qty": 1, "avg_price": 3000, "current_price": 3100},
        })
        broker.sell_results = {"111111": True, "222222": False}
        bot = Bot(broker, QueueStub(), {}, lambda: [])
        bot.bought = {
            "111111": {"name": "성공", "qty": 2, "avg_price": 1000},
            "222222": {"name": "실패", "qty": 4, "avg_price": 2000},
        }

        await bot.sellpos()

        self.assertEqual(broker.sells, [("111111", 2), ("222222", 4)])
        self.assertEqual(list(bot.bought), ["222222"])

    async def testentryfill(self):
        async def buysig(_sym: str, _prediction: object) -> dict:
            return {
                "signal": "buy",
                "score": 90,
                "price": 1000,
                "factors": [],
                "stop_price": 900,
            }

        broker = BrokerStub({
            "111111": {
                "code": "111111",
                "name": "실체결",
                "qty": 7,
                "avg_price": 1200,
                "current_price": 1210,
            }
        })
        bot = Bot(broker, QueueStub(), {"111111": "실체결"}, lambda: ["111111"])
        original_evaluate = entry_module.verdict
        entry_module.verdict = buysig
        try:
            await bot.ent("111111", 10_000)
        finally:
            entry_module.verdict = original_evaluate

        self.assertEqual(broker.buys, [("111111", 10)])
        self.assertEqual(bot.bought["111111"]["avg_price"], 1200)
        self.assertEqual(bot.bought["111111"]["qty"], 7)
        self.assertEqual(bot.bought["111111"]["name"], "실체결")

    async def testinflight(self):
        async def buysig(_sym: str, _prediction: object) -> dict:
            return {
                "signal": "buy",
                "score": 90,
                "price": 1000,
                "factors": [],
                "stop_price": 900,
            }

        broker = BrokerStub()
        broker.buy_delay = 0.01
        bot = Bot(broker, QueueStub(), {"111111": "대기체결"}, lambda: ["111111"])
        original_evaluate = entry_module.verdict
        entry_module.verdict = buysig
        try:
            import asyncio
            await asyncio.gather(
                bot.ent("111111", 10_000),
                bot.ent("111111", 10_000),
            )
        finally:
            entry_module.verdict = original_evaluate

        self.assertEqual(broker.buys, [("111111", 10)])
        self.assertEqual(bot.pendbuys, {"111111"})

    # 국면 게이트가 신규매수를 막아도 대기 매수는 잔고와 재대조 — 건너뛰면 손절·장마감 청산 대상에서 빠짐
    async def testsweeppend(self):
        broker = BrokerStub({
            "111111": {"code": "111111", "name": "대기체결", "qty": 7, "avg_price": 1200, "current_price": 1210},
        })
        broker.index_rows = [{"code": "KOSPI", "change_percent": -2.5}]
        bot = Bot(broker, QueueStub(), {}, lambda: ["222222"])
        bot.notifier.msg = noop
        bot.pendbuys = {"111111"}

        await bot.sweep()

        self.assertIn("111111", bot.bought)
        self.assertEqual(bot.bought["111111"]["qty"], 7)
        self.assertEqual(bot.pendbuys, set())
        self.assertEqual(broker.buys, [])

    # 장마감 청산 직전 대기 매수 재대조 — 마지막 스캔 뒤 잔고에 반영된 매수분이 오버나이트로 남지 않게
    async def testsellpos(self):
        broker = BrokerStub({
            "111111": {"code": "111111", "name": "지연체결", "qty": 5, "avg_price": 1000, "current_price": 1010},
        })
        bot = Bot(broker, QueueStub(), {}, lambda: [])
        bot.pendbuys = {"111111"}

        await bot.sellpos()

        self.assertEqual(broker.sells, [("111111", 5)])
        self.assertEqual(bot.bought, {})
        self.assertEqual(bot.pendbuys, set())

    # 장마감 매도 실패분은 60초 뒤 재시도 — 1회 시도로 끝내면 실패 종목이 오버나이트
    async def testeodretry(self):
        broker = BrokerStub({
            "222222": {"code": "222222", "name": "실패", "qty": 4, "avg_price": 2000, "current_price": 1900},
        })
        broker.sell_results = {"222222": False}
        bot = Bot(broker, QueueStub(), {}, lambda: [])
        bot.notifier.msg = noop
        bot.bought = {"222222": {"name": "실패", "qty": 4, "avg_price": 2000}}
        day = datetime.datetime(2026, 10, 22)

        await bot.closeout(day.replace(hour=15, minute=15, second=5))
        await bot.closeout(day.replace(hour=15, minute=15, second=30))
        broker.sell_results = {}
        await bot.closeout(day.replace(hour=15, minute=16, second=5))

        self.assertEqual(broker.sells, [("222222", 4), ("222222", 4)])
        self.assertEqual(bot.bought, {})

    # 장마감 청산 중 잔고 조회 예외는 경보 후 재시도 — 루프로 전파되면 15:15 이후라 재시작 없이 종료
    async def testeodfault(self):
        broker = BrokerStub({
            "111111": {"code": "111111", "name": "보유", "qty": 2, "avg_price": 1000, "current_price": 1100},
        })
        healthy = broker.holdings
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("kis down")
            return await healthy()

        broker.holdings = flaky
        bot = Bot(broker, QueueStub(), {}, lambda: [])
        captured: list[str] = []

        async def cap(text: str) -> None:
            captured.append(text)

        bot.notifier.msg = cap
        bot.bought = {"111111": {"name": "보유", "qty": 2, "avg_price": 1000}}
        day = datetime.datetime(2026, 10, 22)

        with self.assertLogs("service.trading.bot", level="ERROR"):
            await bot.closeout(day.replace(hour=15, minute=15, second=5))
        await bot.closeout(day.replace(hour=15, minute=16, second=5))

        self.assertTrue(any("[장마감 청산 오류]" in m for m in captured))
        self.assertEqual(broker.sells, [("111111", 2)])
        self.assertEqual(bot.bought, {})

    # 15:20 이후 잔여(보유·대기) 있으면 오버나이트 경보 후 종료 — 무경보면 다음 날 복원 대조로만 인지
    async def testleftover(self):
        bot = Bot(BrokerStub(), QueueStub(), {}, lambda: [])
        captured: list[str] = []

        async def cap(text: str) -> None:
            captured.append(text)

        bot.notifier.msg = cap
        bot.bought = {"222222": {"name": "실패", "qty": 4, "avg_price": 2000}}
        bot.pendbuys = {"333333"}

        done = await bot.closeout(datetime.datetime(2026, 10, 22, 15, 20, 1))

        self.assertTrue(done)
        alert = next(m for m in captured if "[장마감 청산 미완료]" in m)
        self.assertIn("222222", alert)
        self.assertIn("333333", alert)

    async def testbootorphan(self):
        broker = BrokerStub({
            "999999": {
                "code": "999999",
                "name": "수동보유",
                "qty": 3,
                "avg_price": 1000,
                "current_price": 1100,
            }
        })
        bot = Bot(broker, QueueStub(), {}, lambda: [])
        captured: list[str] = []

        async def cap(text: str) -> None:
            captured.append(text)

        bot.msg = cap

        # 즉시 종료 loop — start가 띄우는 run 태스크를 바로 끝냄
        async def quickloop() -> None:
            return None

        bot.loop = quickloop

        original_rows = journal_module.traderows
        journal_module.traderows = lambda: []
        try:
            await bot.boot()
            await bot._task
        finally:
            journal_module.traderows = original_rows

        self.assertTrue(any("미추적" in m and "수동보유(999999)" in m for m in captured))

if __name__ == "__main__":
    unittest.main()
