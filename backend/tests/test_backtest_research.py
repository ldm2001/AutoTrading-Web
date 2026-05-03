import datetime
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service.market.candle_store import CandleStore
from service.trading.backtest import BacktestConfig, bt, grid, wf
from service.trading.regime import regime as mkt
from service.trading.research import wftab, wfrows


def dayset(count: int = 45) -> list[dict]:
    start = datetime.date(2026, 1, 1)
    rows = []
    for i in range(count):
        price = 100 + i
        day = start + datetime.timedelta(days=i)
        rows.append({
            "date": day.isoformat(),
            "open": price,
            "high": price + 2,
            "low": price - 2,
            "close": price,
            "volume": 1_000_000,
        })
    return rows


def bars() -> list[dict]:
    base = datetime.datetime(2026, 3, 2, 9, 0)
    values = [
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 106, 99, 105),
        (105, 106, 104, 105),
        (105, 106, 104, 105),
        (105, 106, 104, 105),
    ]
    return [
        {
            "time": base + datetime.timedelta(minutes=15 * i),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 10_000,
        }
        for i, (o, h, l, c) in enumerate(values)
    ]


class BacktestResearchTest(unittest.TestCase):
    def test_costs_reduce_trade_return(self):
        frictionless = BacktestConfig(
            buy_threshold=-999,
            fee_bps=0,
            sell_tax_bps=0,
            slippage_bps=0,
            spread_bps=0,
        )
        realistic = BacktestConfig(
            buy_threshold=-999,
            fee_bps=1.5,
            sell_tax_bps=23,
            slippage_bps=5,
            spread_bps=4,
        )

        raw = bt("005930", bars(), dayset(), frictionless)
        costed = bt("005930", bars(), dayset(), realistic)

        self.assertGreater(raw.trades[0].pnl_pct, costed.trades[0].pnl_pct)
        self.assertEqual(raw.avg_trade_cost_bps, 0)
        self.assertGreater(costed.avg_trade_cost_bps, 0)
        self.assertIsNotNone(costed.buy_hold_return_pct)
        self.assertIsNotNone(costed.excess_return_pct)

    def test_parameter_grid_reports_multiple_thresholds(self):
        cfg = BacktestConfig(buy_threshold=-999, fee_bps=0, sell_tax_bps=0, slippage_bps=0, spread_bps=0)

        rows = grid(
            "005930",
            bars(),
            dayset(),
            cfg,
            buy_thresholds=[-999, 999],
            take_profit_pcts=[5.0],
            stop_pcts=[3.0],
        )

        self.assertEqual(len(rows), 2)
        self.assertGreater(rows[0]["total_trades"], rows[1]["total_trades"])

    def test_walk_forward_reports_chronological_windows(self):
        cfg = BacktestConfig(buy_threshold=-999, fee_bps=0, sell_tax_bps=0, slippage_bps=0, spread_bps=0)

        windows = wf("005930", bars(), dayset(), cfg, windows=2)

        self.assertEqual(len(windows), 2)
        self.assertLessEqual(windows[0]["start_time"], windows[0]["end_time"])
        self.assertLess(windows[0]["end_time"], windows[1]["start_time"])

    def test_market_regime_blocks_new_buys_on_broad_selloff(self):
        regime = mkt([
            {"code": "KOSPI", "change_percent": -1.4},
            {"code": "KOSDAQ", "change_percent": -2.1},
        ])

        self.assertEqual(regime["state"], "risk_off")
        self.assertFalse(regime["allow_new_buys"])

    def test_span_loads_old_session_files_without_calendar_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CandleStore(Path(tmp))
            root = Path(tmp) / "005930"
            root.mkdir()
            for idx, day in enumerate(("2026-01-15", "2026-02-15", "2026-03-12")):
                path = root / f"{day}_15m.csv"
                path.write_text(
                    "time,open,high,low,close,volume\n"
                    f"{day} 09:00:00,{100 + idx},{101 + idx},{99 + idx},{100 + idx},1000\n"
                )

            rows = store.span("005930", interval=15, days=2)

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["time"].date().isoformat(), "2026-02-15")
            self.assertEqual(rows[1]["time"].date().isoformat(), "2026-03-12")

    def test_walk_forward_history_accumulates_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            self.assertEqual(wftab("005930", {"walk_forward": [{"window": 1}]}, root=root), 1)
            self.assertEqual(wftab("005930", {"walk_forward": [{"window": 2}]}, root=root), 2)

            rows = wfrows("005930", root=root)

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["code"], "005930")
            self.assertEqual(rows[1]["walk_forward"][0]["window"], 2)
            self.assertIn("created_at", rows[0])


if __name__ == "__main__":
    unittest.main()
