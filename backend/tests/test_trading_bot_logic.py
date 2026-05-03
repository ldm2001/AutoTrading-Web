import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.trading.bot as bot_module
from service.trading.bot import Bot


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

    async def cash(self) -> int:
        return 1_000_000

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
        self._original_notify = bot_module.notify
        bot_module.notify = noop
        self._original_append = getattr(bot_module, "order_log_append", None)
        if self._original_append:
            bot_module.order_log_append = lambda _entry: None
        self._original_trade_append = getattr(bot_module, "trade_log_append", None)
        if self._original_trade_append:
            bot_module.trade_log_append = lambda _entry: None

    def tearDown(self) -> None:
        bot_module.notify = self._original_notify
        if self._original_append:
            bot_module.order_log_append = self._original_append
        if self._original_trade_append:
            bot_module.trade_log_append = self._original_trade_append

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
        original_evaluate = bot_module.evaluate
        bot_module.evaluate = buysig
        try:
            await bot.ent("111111", 10_000)
        finally:
            bot_module.evaluate = original_evaluate

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
        original_evaluate = bot_module.evaluate
        bot_module.evaluate = buysig
        try:
            import asyncio
            await asyncio.gather(
                bot.ent("111111", 10_000),
                bot.ent("111111", 10_000),
            )
        finally:
            bot_module.evaluate = original_evaluate

        self.assertEqual(broker.buys, [("111111", 10)])
        self.assertEqual(bot.pending_buys, {"111111"})


if __name__ == "__main__":
    unittest.main()
