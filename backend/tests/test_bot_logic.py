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

class _Cache:
    redis = None

class _Queue:
    async def start(self) -> None:
        return None

class _Broker:
    def __init__(self, holdings: dict[str, dict] | None = None) -> None:
        self.cache = _Cache()
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

    async def buy(self, code: str, qty: int) -> dict:
        if self.buy_delay:
            import asyncio
            await asyncio.sleep(self.buy_delay)
        self.buys.append((code, qty))
        return {"success": True, "data": {"msg1": "accepted"}}

    async def sell(self, code: str, qty: int) -> dict:
        self.sells.append((code, qty))
        return {"success": self.sell_results.get(code, True)}


class TradingBotLogicTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_append = getattr(bot_module, "order_log_append", None)
        if self._original_append:
            bot_module.order_log_append = lambda _entry: None
        self._original_trade_append = getattr(journal_module, "trade_log_append", None)
        if self._original_trade_append:
            journal_module.trade_log_append = lambda _entry: None

    def tearDown(self) -> None:
        if self._original_append:
            bot_module.order_log_append = self._original_append
        if self._original_trade_append:
            journal_module.trade_log_append = self._original_trade_append

    async def test_account_holdings_are_not_registered_as_bot_positions(self):
        broker = _Broker({
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
        bot = Bot(broker, _Queue(), {}, lambda: [])

        await bot.loop()

        self.assertEqual(bot.bought, {})

    async def test_sell_tracked_positions_keeps_failed_positions(self):
        broker = _Broker({
            "111111": {"code": "111111", "name": "성공", "qty": 2, "avg_price": 1000, "current_price": 1100},
            "222222": {"code": "222222", "name": "실패", "qty": 4, "avg_price": 2000, "current_price": 1900},
            "999999": {"code": "999999", "name": "수동", "qty": 1, "avg_price": 3000, "current_price": 3100},
        })
        broker.sell_results = {"111111": True, "222222": False}
        bot = Bot(broker, _Queue(), {}, lambda: [])
        bot.bought = {
            "111111": {"name": "성공", "qty": 2, "avg_price": 1000},
            "222222": {"name": "실패", "qty": 4, "avg_price": 2000},
        }

        await bot.sellpos()

        self.assertEqual(broker.sells, [("111111", 2), ("222222", 4)])
        self.assertEqual(list(bot.bought), ["222222"])

    async def test_entry_records_actual_position_after_order_acceptance(self):
        async def buysig(_sym: str, _prediction: object) -> dict:
            return {
                "signal": "buy",
                "score": 90,
                "price": 1000,
                "factors": [],
                "stop_price": 900,
            }

        broker = _Broker({
            "111111": {
                "code": "111111",
                "name": "실체결",
                "qty": 7,
                "avg_price": 1200,
                "current_price": 1210,
            }
        })
        bot = Bot(broker, _Queue(), {"111111": "실체결"}, lambda: ["111111"])
        original_evaluate = entry_module.evaluate
        entry_module.evaluate = buysig
        try:
            await bot.ent("111111", 10_000)
        finally:
            entry_module.evaluate = original_evaluate

        self.assertEqual(broker.buys, [("111111", 10)])
        self.assertEqual(bot.bought["111111"]["avg_price"], 1200)
        self.assertEqual(bot.bought["111111"]["qty"], 7)
        self.assertEqual(bot.bought["111111"]["name"], "실체결")

    async def test_entry_blocks_duplicate_inflight_buy(self):
        async def buysig(_sym: str, _prediction: object) -> dict:
            return {
                "signal": "buy",
                "score": 90,
                "price": 1000,
                "factors": [],
                "stop_price": 900,
            }

        broker = _Broker()
        broker.buy_delay = 0.01
        bot = Bot(broker, _Queue(), {"111111": "대기체결"}, lambda: ["111111"])
        original_evaluate = entry_module.evaluate
        entry_module.evaluate = buysig
        try:
            import asyncio
            await asyncio.gather(
                bot.ent("111111", 10_000),
                bot.ent("111111", 10_000),
            )
        finally:
            entry_module.evaluate = original_evaluate

        self.assertEqual(broker.buys, [("111111", 10)])
        self.assertEqual(bot.pending_buys, {"111111"})

    # 국면 게이트가 신규매수를 막아도 대기 매수는 잔고와 재대조 — 건너뛰면 손절·장마감 청산 대상에서 빠짐
    async def test_sweep_reconciles_pending_when_gate_blocks(self):
        broker = _Broker({
            "111111": {"code": "111111", "name": "대기체결", "qty": 7, "avg_price": 1200, "current_price": 1210},
        })
        broker.index_rows = [{"code": "KOSPI", "change_percent": -2.5}]
        bot = Bot(broker, _Queue(), {}, lambda: ["222222"])
        bot.notifier.msg = noop
        bot.pending_buys = {"111111"}

        await bot.sweep()

        self.assertIn("111111", bot.bought)
        self.assertEqual(bot.bought["111111"]["qty"], 7)
        self.assertEqual(bot.pending_buys, set())
        self.assertEqual(broker.buys, [])

    # 장마감 청산 직전 대기 매수 재대조 — 마지막 스캔 뒤 잔고에 반영된 매수분이 오버나이트로 남지 않게
    async def test_sellpos_sells_reconciled_pending(self):
        broker = _Broker({
            "111111": {"code": "111111", "name": "지연체결", "qty": 5, "avg_price": 1000, "current_price": 1010},
        })
        bot = Bot(broker, _Queue(), {}, lambda: [])
        bot.pending_buys = {"111111"}

        await bot.sellpos()

        self.assertEqual(broker.sells, [("111111", 5)])
        self.assertEqual(bot.bought, {})
        self.assertEqual(bot.pending_buys, set())

    # 장마감 매도 실패분은 60초 뒤 재시도 — 1회 시도로 끝내면 실패 종목이 오버나이트
    async def test_closeout_retries_failed_sell(self):
        broker = _Broker({
            "222222": {"code": "222222", "name": "실패", "qty": 4, "avg_price": 2000, "current_price": 1900},
        })
        broker.sell_results = {"222222": False}
        bot = Bot(broker, _Queue(), {}, lambda: [])
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
    async def test_closeout_survives_holdings_error(self):
        broker = _Broker({
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
        bot = Bot(broker, _Queue(), {}, lambda: [])
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
    async def test_closeout_alerts_leftover_at_exit(self):
        bot = Bot(_Broker(), _Queue(), {}, lambda: [])
        captured: list[str] = []

        async def cap(text: str) -> None:
            captured.append(text)

        bot.notifier.msg = cap
        bot.bought = {"222222": {"name": "실패", "qty": 4, "avg_price": 2000}}
        bot.pending_buys = {"333333"}

        done = await bot.closeout(datetime.datetime(2026, 10, 22, 15, 20, 1))

        self.assertTrue(done)
        alert = next(m for m in captured if "[장마감 청산 미완료]" in m)
        self.assertIn("222222", alert)
        self.assertIn("333333", alert)

    async def test_start_alerts_untracked_holdings(self):
        broker = _Broker({
            "999999": {
                "code": "999999",
                "name": "수동보유",
                "qty": 3,
                "avg_price": 1000,
                "current_price": 1100,
            }
        })
        bot = Bot(broker, _Queue(), {}, lambda: [])
        captured: list[str] = []

        async def cap(text: str) -> None:
            captured.append(text)

        bot.msg = cap

        # 즉시 종료 loop — start가 띄우는 run 태스크를 바로 끝냄
        async def quickloop() -> None:
            return None

        bot.loop = quickloop

        original_rows = journal_module.trade_log_rows
        journal_module.trade_log_rows = lambda: []
        try:
            await bot.start()
            await bot._task
        finally:
            journal_module.trade_log_rows = original_rows

        self.assertTrue(any("미추적" in m and "수동보유(999999)" in m for m in captured))

if __name__ == "__main__":
    unittest.main()
