import contextlib
import datetime
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline import injection as injection_module
from offline.injection import comparison, coverage, forecasts, main, minutes, ohlc, options
from offline.rules import HOLDOUT_START
from offline.walk import rows
from service.trading.backtest import BacktestConfig
from service.trading.strategy import Scorer


# 합성 일봉 (FDR 형식, 영업일)
def frame(first: str = "2024-06-03", last: str = "2026-01-30") -> pd.DataFrame:
    idx = pd.bdate_range(first, last, name="Date")
    close = [1000.0 + (k % 7) * 3 for k in range(len(idx))]
    return pd.DataFrame({"Open": close, "High": [c + 5 for c in close], "Low": [c - 5 for c in close],
                         "Close": close, "Volume": [1000] * len(idx)}, index=idx)


# 기울기가 컷마다 바뀌는 가짜 core — Direction이 +10·+5·0·−5·−10으로 고루 나옴
def core(window: pd.DataFrame, seed: int, label: str) -> dict:
    base = float(window["Close"].iloc[-1])
    slope = (0.01, 0.003, 0.0, -0.003, -0.01)[len(window) % 5]
    return {"predictions": [{"date": f"d{k}", "close": base * (1 + slope * k)} for k in range(1, 6)],
            "metrics": {"mae": 0.1}, "base_close": int(base), "base_date": str(window.index[-1].date())}


# CandleStore 형식 15분봉 CSV 하루치 (26봉) — stray면 파일 이름과 다른 날짜의 봉을 넣음
def session(root: Path, code: str, day: str, stray: str | None = None) -> None:
    folder = root / code
    folder.mkdir(parents=True, exist_ok=True)
    start = datetime.datetime.fromisoformat(f"{stray or day} 09:00:00")
    lines = ["time,open,high,low,close,volume"]
    for k in range(26):
        p = 1000 + k
        lines.append(f"{start + datetime.timedelta(minutes=15 * k):%Y-%m-%d %H:%M:%S},{p},{p + 1},{p - 1},{p},1000")
    (folder / f"{day}_15m.csv").write_text("\n".join(lines) + "\n")


# 결과 표 한 행 (rows() 형식의 필요한 열만)
def row(code: str, day: str, pct: float = 4.0) -> dict:
    preds = {f"pred{k}": 100 * (1 + pct / 100 * k / 5) for k in range(1, 6)}
    return {"base_date": day, "code": code, "base_close": 100.0, **preds, "pred_ret": pct, "actual_close": 999.0,
            "actual_ret": 899.0, "direction": 10.0}


# 결과 표 → 주입용 표 (기준일 → 예측 dict)
class TableTest(unittest.TestCase):
    # 하네스 결과 표의 Direction 열과 변환한 예측의 Scorer.labscore 점수(문 없는 같은 규칙)가 같고, 실제값 열은 넣지 않음
    def testshape(self):
        frames = {"000001": frame(last="2025-07-31"), "000002": frame(last="2025-07-31")}
        table, skipped = rows(frames, "2025-05-01", "2025-07-15", step=1, core=core)
        self.assertEqual(skipped, [])
        self.assertEqual({r["direction"] for r in table}, {10.0, 5.0, 0.0, -5.0, -10.0})
        result = forecasts(table)
        self.assertEqual(set(result), {"000001", "000002"})
        scorer = Scorer()
        for r in table:
            with self.subTest(code=r["code"], day=r["base_date"]):
                pred = result[r["code"]][r["base_date"]]
                self.assertEqual(set(pred), {"predictions", "base_close", "base_date"})
                self.assertEqual(pred["base_date"], r["base_date"])
                self.assertEqual(scorer.labscore(pred, None)[0], r["direction"])

    # HOLDOUT_START 이후 기준일은 버림
    def testholdout(self):
        result = forecasts([row("005930", "2025-11-21"), row("005930", HOLDOUT_START), row("005930", "2026-01-05")])
        self.assertEqual(list(result["005930"]), ["2025-11-21"])

    # CSV 왕복 — pandas가 종목 코드를 숫자로 읽어도 6자리로 맞춤
    def testcsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.csv"
            pd.DataFrame([row("005930", "2025-11-20"), row("000660", "2025-11-20", -4.0)]).to_csv(path, index=False)
            result = forecasts(pd.read_csv(path).to_dict("records"))
        self.assertEqual(set(result), {"005930", "000660"})
        self.assertEqual(Scorer().labscore(result["000660"]["2025-11-20"], None)[0], -10)


# CandleStore 15분봉 — 개발 구간은 HOLDOUT_START 이전 파일·봉만
class BarsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # 경계 이후 이름의 파일은 열지 않고(안의 봉 날짜가 경계 이전이어도), 경계 이전 이름의 파일에 섞인 경계 이후 봉도 버림
    def testguard(self):
        for day in ("2025-11-19", "2025-11-20", HOLDOUT_START):
            session(self.root, "005930", day)
        session(self.root, "005930", "2025-11-25", stray="2025-11-18")
        session(self.root, "005930", "2025-11-21", stray=HOLDOUT_START)
        bars = minutes(self.root, "005930", "2025-11-01", "2026-12-31")
        self.assertEqual(sorted({b["time"].date().isoformat() for b in bars}), ["2025-11-19", "2025-11-20"])
        self.assertEqual(len(bars), 52)
        self.assertEqual(bars, sorted(bars, key=lambda b: b["time"]))

    # 평가 구간 [start, end] 밖 날짜는 읽지 않음
    def testwindow(self):
        for day in ("2025-10-31", "2025-11-03", "2025-11-04"):
            session(self.root, "005930", day)
        bars = minutes(self.root, "005930", "2025-11-03", "2025-11-03")
        self.assertEqual({b["time"].date().isoformat() for b in bars}, {"2025-11-03"})

    # 폴더가 없으면 빈 목록 — 폴더를 새로 만들지 않음
    def testmissing(self):
        self.assertEqual(minutes(self.root, "005930", "2025-11-01", "2025-11-21"), [])
        self.assertFalse(self.root.exists())


# 주입 유무 비교 — 같은 입력으로 시뮬레이션 두 번
class ComparisonTest(unittest.TestCase):
    # 첫 실행은 주입 없음(/api/backtest와 같은 bt 호출), 둘째는 같은 입력에 예측 표와 lag만 더함
    # 3-6: 둘째는 하네스 전용 채점기(LabScorer)로 playback — bt는 문이 닫혀 있으면 주입이 0점이라
    def testwiring(self):
        calls = []
        replays = []
        real, again = injection_module.bt, injection_module.playback

        def recorder(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        def replayer(*args, **kwargs):
            replays.append((args, kwargs))
            return again(*args, **kwargs)

        seq = sessions()
        daily = ohlc(frame(last="2025-11-21"))
        table = forecasts([row("005930", "2025-11-18")])["005930"]
        cfg = BacktestConfig(buy_threshold=10)
        with mock.patch.object(injection_module, "bt", recorder), \
                mock.patch.object(injection_module, "playback", replayer):
            result = comparison("005930", seq, daily, table, cfg, lag=2)
        self.assertEqual(calls, [(("005930", seq, daily, cfg), {})])
        self.assertEqual(len(replays), 1)
        args, kwargs = replays[0]
        self.assertIsInstance(args[0], injection_module.LabScorer)
        self.assertEqual((args[1:], kwargs), (("005930", seq, daily, cfg), {"forecasts": table, "lag": 2}))
        self.assertEqual(set(result), {"code", "lag", "bars", "days", "covered", "plain", "injected", "delta",
                                       "entries"})
        self.assertEqual(result["delta"]["total_trades"],
                         result["injected"]["total_trades"] - result["plain"]["total_trades"])

    # 주입된 날 — 직전 거래일 기준 예측이 있는 15분봉 날만 (lag=1)
    def testcoverage(self):
        daily = ohlc(frame("2025-11-03", "2025-11-21"))
        seq = sessions()
        table = {"2025-11-18": {}, "2025-11-20": {}}
        self.assertEqual(coverage(seq, daily, table), {"days": 3, "covered": 2})
        self.assertEqual(coverage(seq, daily, table, lag=2), {"days": 3, "covered": 3})
        self.assertEqual(coverage(seq, daily, {}), {"days": 3, "covered": 0})


# 2025-11-19·20·21 15분봉 (78봉)
def sessions() -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for day in ("2025-11-19", "2025-11-20", "2025-11-21"):
            session(root, "005930", day)
        return minutes(root, "005930", "2025-11-01", "2025-11-21")


# 가짜 일봉 공급자 — 요청 기간 기록
class Source:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, code: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((code, start, end))
        return frame()


# CLI — 개발 구간 전용, 결과 JSON·요약
class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bars, self.cache, self.out, self.rows = root / "bars", root / "cache", root / "out", root / "rows.csv"
        for day in ("2025-11-19", "2025-11-20", "2025-11-21", HOLDOUT_START, "2025-11-25"):
            session(self.bars, "005930", day)
        for day in (HOLDOUT_START, "2025-11-25"):
            session(self.bars, "000660", day)
        table = [row("005930", d) for d in ("2025-11-18", "2025-11-19", "2025-11-20", HOLDOUT_START)]
        pd.DataFrame(table + [row("000660", "2025-11-21")]).to_csv(self.rows, index=False)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # 경계 이전 15분봉만 비교, 15분봉이 모자란 종목은 건너뜀, 요약 첫 줄은 개발 구간
    def testdev(self):
        source = Source()
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["--rows", str(self.rows), "--start", "2025-11-01", "--end", "2025-12-31",
                         "--out", str(self.out), "--bars", str(self.bars), "--cache", str(self.cache)], reader=source)
        self.assertEqual(code, 0)
        report = json.loads((self.out / "injection.json").read_text(encoding="utf-8"))
        self.assertEqual(report["scope"], "dev")
        self.assertEqual([r["code"] for r in report["results"]], ["005930"])
        first = report["results"][0]
        self.assertEqual((first["bars"], first["days"], first["covered"]), (78, 3, 3))
        self.assertEqual([s["code"] for s in report["skipped"]], ["000660"])
        self.assertTrue(all(end < HOLDOUT_START for _, _, end in source.calls))
        lines = (self.out / "injection.md").read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[0].startswith("개발 구간"))

    # --final 인자가 없음 — 최종 구간은 이 경로로 읽을 수 없음
    def testflag(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            options(["--rows", "r.csv", "--start", "2025-11-01", "--end", "2025-12-31", "--out", "o", "--final"])


if __name__ == "__main__":
    unittest.main()
