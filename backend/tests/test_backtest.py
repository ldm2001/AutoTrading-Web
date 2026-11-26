import asyncio
import contextlib
import datetime
import hashlib
import json
import random
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import service.ai.predict as predict_module
from config import settings
from service.ai.spec import VERSION
from service.market.candle_store import CandleStore
from service.trading import backtest as backtest_module
from service.trading.backtest import BacktestConfig, BacktestScorer, bt, grid, wf
from service.trading.regime import regime as mkt
from service.trading.research import wftab, wfrows
from service.trading.strategy import Scorer


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


def candles(values: list[tuple]) -> list[dict]:
    base = datetime.datetime(2026, 3, 2, 9, 0)
    return [
        {"time": base + datetime.timedelta(minutes=15 * i), "open": o, "high": h, "low": l, "close": c,
         "volume": 10_000}
        for i, (o, h, l, c) in enumerate(values)
    ]


# 비용 0, 항상 진입 — 체결가를 손으로 계산하기 위한 설정
def frictionless(**kw) -> BacktestConfig:
    return BacktestConfig(**{"buy_threshold": -999, "fee_bps": 0, "sell_tax_bps": 0, "slippage_bps": 0,
                             "spread_bps": 0, **kw})


# 1봉에서 신호 → 2봉 시가 100에 진입, 이후 손절(97)·익절(105)에 닿지 않고 97.5까지 밀림
_DRIFT = [
    (100, 100.5, 99.5, 100),
    (100, 100.5, 99.5, 100),
    (100, 100.2, 99.0, 99.0),
    (99.0, 99.2, 98.0, 98.0),
    (98.0, 98.2, 97.5, 97.5),
    (97.5, 97.6, 97.4, 97.5),
]


# 합성 시장 — 일봉 100일 + 15분봉 6거래일(하루 26봉, 그날 일봉도 덧붙임). 가끔 갭이 있는 랜덤워크
# random()만 쓴다 (같은 시드면 파이썬 버전이 달라도 같은 수열)
def market(seed: int = 9) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed)
    price = 50_000.0
    day = datetime.date(2025, 9, 1)
    daily: list[dict] = []
    seq: list[dict] = []
    for n in range(106):
        while day.weekday() >= 5:
            day += datetime.timedelta(days=1)
        if n < 100:
            o = price
            c = o * (1 + (rng.random() - 0.5) * 0.06)
            h, l = max(o, c) * (1 + rng.random() * 0.01), min(o, c) * (1 - rng.random() * 0.01)
            daily.append({"date": day.isoformat(), "open": round(o), "high": round(h), "low": round(l),
                          "close": round(c), "volume": int(1_000_000 * (0.5 + rng.random()))})
            price = c
        else:
            start = datetime.datetime.combine(day, datetime.time(9, 0))
            rows = []
            for k in range(26):
                jump = (rng.random() - 0.5) * 0.03 if rng.random() < 0.08 else 0.0
                o = price * (1 + jump)
                c = o * (1 + (rng.random() - 0.5) * 0.008)
                h, l = max(o, c) * (1 + rng.random() * 0.003), min(o, c) * (1 - rng.random() * 0.003)
                rows.append({"time": start + datetime.timedelta(minutes=15 * k), "open": round(o), "high": round(h),
                             "low": round(l), "close": round(c), "volume": int(10_000 * (0.5 + rng.random()))})
                price = c
            seq += rows
            daily.append({"date": day.isoformat(), "open": rows[0]["open"], "high": max(r["high"] for r in rows),
                          "low": min(r["low"] for r in rows), "close": rows[-1]["close"],
                          "volume": sum(r["volume"] for r in rows)})
        day += datetime.timedelta(days=1)
    return daily, seq


# 봉마다의 (점수, 손절가)와 bt·grid·wf 출력 전체를 한 JSON 문자열로 (kw는 bt에만 넘김)
# memo를 주면 bt·grid·wf가 그 점수 표 하나를 같이 씀 (1-6)
def snapshot(daily: list[dict], seq: list[dict], *, memo: dict | None = None, **kw) -> str:
    shared = {} if memo is None else {"scores": memo}
    scorer = BacktestScorer()
    marks = []
    for i in range(1, len(seq)):
        day = seq[i]["time"].date().isoformat()
        marks.append(scorer.val([d for d in daily if d["date"] < day], seq[:i], seq[i]["close"]))
    runs = [asdict(bt("005930", seq, daily, cfg, **kw, **shared))
            for cfg in (BacktestConfig(), BacktestConfig(buy_threshold=15))]
    table = grid("005930", seq, daily, BacktestConfig(), buy_thresholds=[15, 30], take_profit_pcts=[2.0],
                 stop_pcts=[1.0, 3.0], **shared)
    windows = wf("005930", seq, daily, BacktestConfig(buy_threshold=15), windows=3, **shared)
    return json.dumps([marks, runs, table, windows], sort_keys=True, default=str)


# E-04 — 미청산 포지션·갭 하락·wf 구간 경계
class BacktestExitTest(unittest.TestCase):
    # 마지막 봉까지 들고 있던 미실현 −2.5% → 마지막 봉 종가로 강제 청산해 거래 1건·누적 −2.5%
    def testleftover(self):
        result = bt("005930", candles(_DRIFT), dayset(), frictionless())
        self.assertEqual(result.total_trades, 1)
        trade = result.trades[0]
        self.assertEqual((trade.entry_bar, trade.exit_bar, trade.exit_reason), (2, 5, "end"))
        self.assertEqual(trade.exit_price, 98)
        self.assertAlmostEqual(trade.pnl_pct, -2.5)
        self.assertAlmostEqual(result.cum_return_pct, -2.5)
        self.assertAlmostEqual(result.mdd_pct, -2.5)
        self.assertTrue(any("강제 청산" in w for w in result.validation_warnings))

    # FVG 손절가(98) 아래로 갭 시가(97.5) → 시가에 체결, 갭이 없으면 손절가에 체결
    def testgap(self):
        gap = [(100, 100.5, 99.5, 100)] * 3 + [(97.5, 97.8, 97.2, 97.6)]
        touch = [(100, 100.5, 99.5, 100)] * 3 + [(99.0, 99.2, 97.8, 98.5)]
        with mock.patch.object(backtest_module.smc, "slprice", return_value=98.0):
            gapped = bt("005930", candles(gap), dayset(), frictionless())
            touched = bt("005930", candles(touch), dayset(), frictionless())
        self.assertEqual(gapped.trades[0].exit_reason, "stop")
        self.assertAlmostEqual(gapped.trades[0].pnl_pct, -2.5)
        self.assertEqual(touched.trades[0].exit_reason, "stop")
        self.assertAlmostEqual(touched.trades[0].pnl_pct, -2.0)
        self.assertFalse(any("강제 청산" in w for w in gapped.validation_warnings))

    # 고정 % 폴백 손절(97)도 갭 시가(95)에 체결
    def testfloorgap(self):
        values = [(100, 100.5, 99.5, 100)] * 3 + [(95, 95.5, 94.5, 95)]
        result = bt("005930", candles(values), dayset(), frictionless())
        self.assertEqual(result.trades[0].exit_reason, "stop")
        self.assertAlmostEqual(result.trades[0].pnl_pct, -5.0)

    # wf 구간 안의 봉 채점 입력·점수가 전체 실행과 같음 (15분봉 이력을 구간마다 처음부터 쌓지 않음)
    def testwindowscore(self):
        values = [(100 + k % 3, 101 + k % 3, 99 + k % 3, 100 + k % 3) for k in range(12)]
        seq = candles(values)
        cfg = frictionless(buy_threshold=999)
        original = backtest_module.BacktestScorer.val
        calls: list[tuple] = []

        def recorder(self, daily, candles_15m, price, *args, **kwargs):
            total, stop = original(self, daily, candles_15m, price, *args, **kwargs)
            calls.append((len(candles_15m), candles_15m[0]["time"], len(daily), total, stop))
            return total, stop

        with mock.patch.object(backtest_module.BacktestScorer, "val", recorder):
            bt("005930", seq, dayset(), cfg)
            full = sorted(calls)
            calls.clear()
            rows = wf("005930", seq, dayset(), cfg, windows=3)
            windowed = sorted(calls)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(full), 11)
        self.assertEqual(windowed, full)

    # 구간 끝 포지션도 그 구간 마지막 봉 종가로 강제 청산 — 2구간은 앞 구간 이력으로 3봉에서 신호, 4봉 시가 98 진입
    def testwindowend(self):
        rows = wf("005930", candles(_DRIFT), dayset(), frictionless(), windows=2)
        self.assertEqual([r["total_trades"] for r in rows], [1, 1])
        self.assertEqual([r["total_bars"] for r in rows], [3, 3])
        self.assertAlmostEqual(rows[0]["cum_return_pct"], -1.0)
        self.assertAlmostEqual(rows[1]["cum_return_pct"], round((97.5 - 98.0) / 98.0 * 100, 2))
        # 보유 대비 초과수익도 그 구간 봉만으로 (1구간 100→99, 2구간 99→97.5)
        self.assertAlmostEqual(rows[0]["excess_return_pct"], 0.0)
        self.assertAlmostEqual(rows[1]["excess_return_pct"], round(-0.51 - round((97.5 - 99.0) / 99.0 * 100, 2), 2))

    # 구간 마지막 봉의 신호는 다음 구간 봉에 진입하지 않음 — 1구간(0~1봉)은 거래 0건
    def testwindowedge(self):
        rows = wf("005930", candles(_DRIFT), dayset(), frictionless(), windows=3)
        self.assertEqual([r["total_trades"] for r in rows], [0, 1, 1])
        self.assertAlmostEqual(rows[1]["cum_return_pct"], round((98.0 - 99.0) / 99.0 * 100, 2))
        self.assertAlmostEqual(rows[2]["cum_return_pct"], 0.0)
        result = bt("005930", candles(_DRIFT), dayset(), frictionless(), span=(2, 4))
        self.assertEqual([(t.entry_bar, t.exit_bar, t.exit_reason) for t in result.trades], [(3, 3, "end")])


# 1-4 패리티 기준 — 예측 주입을 넣기 전 snapshot(*market())의 sha256 (stage-1 btsnap.py로 두 번 실행해 같은 값)
_GOLD = "66c8cf4590dec5667e6810e42b67d6ee43742a9705129b5488ec2077fab0ca5f"


# 기준 종가 100에서 5일 동안 고르게 pct%까지 가는 예측 (base_date는 주입 표의 키와 같게)
# 3-6부터 출처·검증·사양 필드도 채움 — 문(predgate)이 열리면 Direction이 살아 있는 모양, 문 자체는 tests/test_gate.py
def predrow(day: str, pct: float) -> dict:
    closes = [round(100 * (1 + pct / 100 * k / 5), 4) for k in range(1, 6)]
    return {"predictions": [{"close": c} for c in closes], "base_close": 100, "base_date": day,
            "source": "pooled", "validated": True, "spec": VERSION}


# 3-6 문 열기 — 플래그 True + 지금 읽힌 공용 모델 메타가 검증됨·지금 사양 (예측 쪽 필드는 predrow가 채움)
def gatepass() -> contextlib.ExitStack:
    pool = {"model": None, "stats": {}, "meta": {"validated": True, "version": VERSION}}
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(settings, "use_prediction", True))
    stack.enter_context(mock.patch.object(predict_module.predictor, "bundle", lambda: pool))
    return stack


# 라이브 채점기용 Quotes 스텁 — 백테스트와 같은 일봉·현재가·15분봉
class QuoteStub:
    def __init__(self, daily, price, c15):
        self._daily, self._price, self._c15 = daily, price, c15

    async def daily(self, code, count=60):
        return self._daily

    async def price(self, code):
        return {"price": self._price}

    async def c15(self, code):
        return self._c15


# 1-4 — 백테스트 예측 주입 (기준일 → 예측 표). 합성 시장의 15분봉 날짜는 2026-01-19 ~ 01-23, 01-26
class BacktestForecastTest(unittest.TestCase):
    # 주입 없음·None·빈 표·오래된 기준일뿐인 표 → 주입을 넣기 전 출력과 바이트 단위로 같음
    def testparity(self):
        daily, seq = market()
        stale = {"2026-01-02": predrow("2026-01-02", 4.0)}
        cases = {"default": {}, "none": {"forecasts": None}, "empty": {"forecasts": {}},
                 "stale": {"forecasts": stale}}
        for label, kw in cases.items():
            with self.subTest(label):
                blob = snapshot(daily, seq, **kw)
                self.assertEqual(hashlib.sha256(blob.encode()).hexdigest(), _GOLD)

    # 봉 날짜보다 앞선 가장 최근 기준일만 — 그 기준일이 직전 거래일이 아니면(lag=1) 쓰지 않고, 당일 기준일도 쓰지 않음
    def testprior(self):
        daily, seq = market()
        table = {d: predrow(d, 4.0) for d in ("2026-01-16", "2026-01-19", "2026-01-21", "2026-01-26")}
        original = backtest_module.BacktestScorer.val
        seen: dict[str, set] = {}

        def recorder(self, daily, candles_15m, price, prediction=None):
            day = seq[len(candles_15m)]["time"].date().isoformat()
            seen.setdefault(day, set()).add(prediction["base_date"] if prediction else None)
            return original(self, daily, candles_15m, price, prediction=prediction)

        cfg = BacktestConfig(buy_threshold=999)
        with mock.patch.object(backtest_module.BacktestScorer, "val", recorder):
            bt("005930", seq, daily, cfg, forecasts=table)
            fresh = dict(seen)
            seen.clear()
            bt("005930", seq, daily, cfg, forecasts=table, lag=2)
            loose = dict(seen)
        self.assertEqual(fresh, {
            "2026-01-19": {"2026-01-16"}, "2026-01-20": {"2026-01-19"}, "2026-01-21": {None},
            "2026-01-22": {"2026-01-21"}, "2026-01-23": {None}, "2026-01-26": {None},
        })
        self.assertEqual(loose, {
            "2026-01-19": {"2026-01-16"}, "2026-01-20": {"2026-01-19"}, "2026-01-21": {"2026-01-19"},
            "2026-01-22": {"2026-01-21"}, "2026-01-23": {"2026-01-21"}, "2026-01-26": {None},
        })

    # 주입한 Direction이 합계에 그대로 — 같은 입력·같은 예측의 라이브 Scorer.verdict 점수·팩터 목록과 일치 (E-09)
    # 3-6: 문이 열린 경우 (닫힌 경우의 라이브·백테스트 0점 일치는 tests/test_gate.py testpaths)
    def testlive(self):
        daily, seq = market()
        scorer = BacktestScorer()
        for pct, want in ((4.0, 10), (2.0, 5), (-4.0, -10), (0.0, 0)):
            prediction = predrow("2026-01-16", pct)
            for i in (40, 90, 150):
                with self.subTest(pct=pct, bar=i), gatepass():
                    day = seq[i]["time"].date().isoformat()
                    past = [d for d in daily if d["date"] < day]
                    price = seq[i]["close"]
                    plain, plainstop = scorer.val(past, seq[:i], price)
                    total, stop = scorer.val(past, seq[:i], price, prediction=prediction)
                    live = asyncio.run(Scorer(QuoteStub(past, price, seq[:i])).verdict("005930", prediction=prediction))
                    factor = next(f for f in live["factors"] if f["name"] == "Direction")
                    self.assertEqual(factor["score"], want)
                    self.assertEqual(total, live["score"])
                    self.assertAlmostEqual(total - plain, want, places=6)
                    self.assertEqual((stop, plainstop), (live["stop_price"], live["stop_price"]))

    # 주입하면 진입이 바뀜 — 임계 25에서 주입 없음 5건, 모든 기준일 상승 예측 9건, 하락 예측 3건
    # 3-6: 문이 열린 경우 (닫혀 있으면 주입 없음과 같음 — tests/test_gate.py testbacktest)
    def testtrades(self):
        daily, seq = market()
        days = [d["date"] for d in daily]
        cfg = BacktestConfig(buy_threshold=25)
        plain = bt("005930", seq, daily, cfg)
        with gatepass():
            up = bt("005930", seq, daily, cfg, forecasts={d: predrow(d, 4.0) for d in days})
            down = bt("005930", seq, daily, cfg, forecasts={d: predrow(d, -4.0) for d in days})
        self.assertGreater(up.total_trades, plain.total_trades)
        self.assertLess(down.total_trades, plain.total_trades)


# 1-6 — 봉 점수는 데이터에만 의존하므로 한 번만 계산해 bt·grid·wf가 같이 씀
class BacktestScoresTest(unittest.TestCase):
    def setUp(self) -> None:
        self.seen: list[int] = []
        original = backtest_module.BacktestScorer.val
        seen = self.seen

        # 채점한 봉 번호 기록 (candles_15m[:i]의 길이 = i)
        def recorder(self, daily, candles_15m, price, *args, **kwargs):
            seen.append(len(candles_15m))
            return original(self, daily, candles_15m, price, *args, **kwargs)

        patcher = mock.patch.object(backtest_module.BacktestScorer, "val", recorder)
        patcher.start()
        self.addCleanup(patcher.stop)

    # grid 조합마다 같은 봉을 다시 채점하지 않음 — 봉마다 한 번
    def testreuse(self):
        daily, seq = market()
        grid("005930", seq, daily, BacktestConfig(), buy_thresholds=[15, 30], take_profit_pcts=[2.0],
             stop_pcts=[1.0, 3.0])
        self.assertTrue(self.seen)
        self.assertEqual(len(self.seen), len(set(self.seen)))

    # 점수 표 하나를 API 순서(bt → grid → wf)로 같이 쓰면 어느 봉도 두 번 채점하지 않고, 출력은 따로 계산한 것과 바이트 단위로 같음
    def testsharing(self):
        daily, seq = market()
        memo: dict = {}
        cfg = BacktestConfig(buy_threshold=15)
        bt("005930", seq, daily, cfg, scores=memo)
        self.assertTrue(self.seen)
        grid("005930", seq, daily, cfg, buy_thresholds=[15, 30], take_profit_pcts=[2.0], stop_pcts=[1.0, 3.0],
             scores=memo)
        wf("005930", seq, daily, cfg, windows=3, scores=memo)
        self.assertEqual(len(self.seen), len(set(self.seen)))
        self.assertEqual(set(memo), set(self.seen))
        self.assertEqual(hashlib.sha256(snapshot(daily, seq, memo={}).encode()).hexdigest(), _GOLD)

    # 일봉 35개 미만인 봉은 진입하지 않음 — 점수 표를 다시 써도 같음
    def testminimum(self):
        memo: dict = {}
        for _ in range(2):
            self.assertEqual(bt("005930", candles(_DRIFT), dayset(34), frictionless(), scores=memo).total_trades, 0)
        self.assertEqual(self.seen, [])
        self.assertEqual(bt("005930", candles(_DRIFT), dayset(35), frictionless(), scores={}).total_trades, 1)


class BacktestResearchTest(unittest.TestCase):
    def testcosts(self):
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

    def testgrid(self):
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

    def testwindows(self):
        cfg = BacktestConfig(buy_threshold=-999, fee_bps=0, sell_tax_bps=0, slippage_bps=0, spread_bps=0)

        windows = wf("005930", bars(), dayset(), cfg, windows=2)

        self.assertEqual(len(windows), 2)
        self.assertLessEqual(windows[0]["start_time"], windows[0]["end_time"])
        self.assertLess(windows[0]["end_time"], windows[1]["start_time"])

    def testselloff(self):
        regime = mkt([
            {"code": "KOSPI", "change_percent": -1.4},
            {"code": "KOSDAQ", "change_percent": -2.1},
        ])

        self.assertEqual(regime["state"], "risk_off")
        self.assertFalse(regime["allow_new_buys"])

    # 지수 데이터 없음은 신규매수 차단 — 허용하면 시장을 못 본 채 매수
    def testnoregime(self):
        regime = mkt([])

        self.assertEqual(regime["state"], "unknown")
        self.assertFalse(regime["allow_new_buys"])

    def testspan(self):
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

    def testwfhistory(self):
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
