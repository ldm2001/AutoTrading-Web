import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import offline.pool as pool_module
import service.ai.predict as predict_module
from offline.pool import deposit
from offline.rules import HOLDOUT_START
from offline.run import main
from service.ai.predict import network
from service.ai.spec import FEATURES, PROFILE, SPEC, VERSION


# 합성 일봉 (영업일, 종가 1씩 증가)
def bars(first: str = "2024-01-01", last: str = "2026-01-30") -> pd.DataFrame:
    idx = pd.bdate_range(first, last, name="Date")
    close = [1000.0 + k for k in range(len(idx))]
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": [1000] * len(idx)}, index=idx)


# 가짜 일봉 공급자 — 부를 때 표식 파일이 있었는지 기록 (최종 구간은 표식 → 읽기 순서)
class Source:
    def __init__(self, marker: Path | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.marked: list[bool] = []
        self.marker = marker

    def __call__(self, code: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((code, start, end))
        self.marked.append(bool(self.marker and self.marker.exists()))
        return bars()


# 가짜 core — 기준가 대비 매일 +1% 예측
def core(window: pd.DataFrame, seed: int, label: str) -> dict:
    base = float(window["Close"].iloc[-1])
    return {"predictions": [{"close": base * (1 + 0.01 * k)} for k in range(1, 6)],
            "metrics": {"mae": 0.1}, "base_close": int(base), "base_date": str(window.index[-1].date())}


# 부르면 안 되는 자리
def tripwire(*args, **kwargs):
    raise AssertionError("호출되면 안 됨")


# CLI — 결과 파일 3개, 요약 첫 줄, 최종 구간 표식
class RunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.out, self.cache, self.marker = root / "out", root / "cache", root / "holdout_used.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def argv(self, *extra: str, end: str = "2025-12-31") -> list[str]:
        return ["--codes", "1,000660", "--start", "2025-05-01", "--end", end, "--out", str(self.out),
                "--cache", str(self.cache), "--marker", str(self.marker), *extra]

    def outcome(self, argv: list[str], **kw) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, **kw)
        return code, out.getvalue(), err.getvalue()

    # 개발 구간 — end가 HOLDOUT을 넘어도 그 앞 행만, 표식 없음, 3종목 미만이라 IC 판정 불가 → 실패
    def testdev(self):
        src = Source(self.marker)
        code, out, err = self.outcome(self.argv(), reader=src, core=core)
        self.assertEqual(code, 0)
        self.assertIn("--final 없음", err)
        self.assertFalse(self.marker.exists())
        self.assertTrue(all(c[2] < HOLDOUT_START for c in src.calls))
        table = pd.read_csv(self.out / "rows.csv", dtype={"code": str})
        self.assertEqual(sorted(table["code"].unique()), ["000001", "000660"])
        for col in ("base_date", "base_close", "pred1", "pred5", "pred_ret", "actual_ret", "direction"):
            self.assertIn(col, table.columns)
        self.assertTrue((table["target_date"] < HOLDOUT_START).all())
        report = json.loads((self.out / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(report["scope"], "dev")
        self.assertEqual(report["rules"]["HOLDOUT_START"], HOLDOUT_START)
        self.assertFalse(report["verdict"]["pass"])
        self.assertIn("판정 불가", report["verdict"]["notes"]["ic"])
        summary = (self.out / "summary.md").read_text(encoding="utf-8")
        self.assertTrue(summary.splitlines()[0].startswith("개발 구간"))
        self.assertIn("Direction 효과 0 고정", summary)
        self.assertIn("Direction 효과 0 고정", out)

    # 표식이 이미 있으면 --final 거부 — 데이터를 읽지 않고, 결과 폴더도 만들지 않음
    def testrefusal(self):
        self.marker.write_text("{}", encoding="utf-8")
        code, _, err = self.outcome(self.argv("--final"), reader=tripwire, core=tripwire)
        self.assertEqual(code, 2)
        self.assertIn("이미 사용됨", err)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "{}")

    # --final (임시 표식·가짜 데이터) — 표식을 먼저 남긴 뒤 읽고, 요약 첫 줄은 최종 구간
    def testfinal(self):
        src = Source(self.marker)
        code, _, _ = self.outcome(self.argv("--final", end="2026-01-15"), reader=src, core=core)
        self.assertEqual(code, 0)
        self.assertEqual(src.marked, [True, True])
        self.assertEqual(json.loads(self.marker.read_text(encoding="utf-8"))["config"]["end"], "2026-01-15")
        summary = (self.out / "summary.md").read_text(encoding="utf-8")
        self.assertTrue(summary.splitlines()[0].startswith("최종 구간"))
        table = pd.read_csv(self.out / "rows.csv")
        self.assertTrue((table["base_date"] >= HOLDOUT_START).any())

    # 컷 간격 < 5 — 블록이 겹쳐 판정 무효
    def testoverlap(self):
        code, _, _ = self.outcome(self.argv("--step", "1", end="2025-07-31"), reader=Source(), core=core)
        self.assertEqual(code, 0)
        report = json.loads((self.out / "metrics.json").read_text(encoding="utf-8"))
        self.assertIn("overlap", report["verdict"]["notes"])
        self.assertFalse(report["verdict"]["pass"])



# 저장 모델 — deposit()으로 쓴 무작위 가중치·단위 통계, 일봉 끝 last
def stored(folder: Path, last: str) -> None:
    model = network(0, len(FEATURES), SPEC["horizon"], SPEC["seq"])
    deposit(folder, model, {"version": VERSION, "spec": PROFILE, "train_end": "2024-11-22", "count": 2,
                            "data": {"since": "2024-01-01", "last": last},
                            "stats": {"mu": [0.0] * len(FEATURES), "sd": [1.0] * len(FEATURES),
                                      "ysd": [0.02] * SPEC["horizon"]},
                            "metrics": {"mae": 1.0, "mae_zero": 1.1, "skill": 0.0909, "n_val": 50}})


# 3-4 공용 모델 평가 CLI — 종목별과 같은 결과 파일·열·지표·판정, 모델 정보는 metrics.json의 model과 요약 한 줄
class PooledTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.out, self.cache, self.marker = root / "out", root / "cache", root / "holdout_used.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def argv(self, *extra: str, out: Path | None = None, end: str = "2025-07-31") -> list[str]:
        return ["--codes", "1,000660", "--start", "2025-05-01", "--end", end, "--out", str(out or self.out),
                "--cache", str(self.cache), "--marker", str(self.marker), *extra]

    def outcome(self, argv: list[str], **kw) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv, **kw)
        return code, out.getvalue(), err.getvalue()

    # --retrain 0 — --since부터 요청(학습 창 365일 없음), 첫 컷에서 한 번 학습, 종목별 실행과 같은 열·같은 지표 키
    def testretrain(self):
        src = Source()
        code, out, _ = self.outcome(self.argv("--retrain", "0", "--since", "2024-01-01"), reader=src, epochs=1)
        self.assertEqual(code, 0)
        self.assertEqual(sorted({c[1] for c in src.calls}), ["2024-01-01"])
        base = self.root / "base"
        self.assertEqual(self.outcome(self.argv(out=base), reader=Source(), core=core)[0], 0)
        table = pd.read_csv(self.out / "rows.csv", dtype={"code": str})
        other = pd.read_csv(base / "rows.csv", dtype={"code": str})
        self.assertEqual(list(table.columns), list(other.columns))
        self.assertEqual(sorted(table["base_date"].unique()), sorted(other["base_date"].unique()))
        report = json.loads((self.out / "metrics.json").read_text(encoding="utf-8"))
        plain = json.loads((base / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(set(report), set(plain))
        self.assertEqual(set(report["stats"]), set(plain["stats"]))
        self.assertEqual(set(report["verdict"]), set(plain["verdict"]))
        self.assertEqual(plain["model"]["kind"], "per_symbol")
        model = report["model"]
        self.assertEqual((model["kind"], model["mode"], model["retrain"], model["since"]),
                         ("pooled", "retrain", 0, "2024-01-01"))
        self.assertEqual([t["cut"] for t in model["trained"]], ["2025-05-01"])
        self.assertEqual(report["config"]["model"]["kind"], "pooled")
        summary = (self.out / "summary.md").read_text(encoding="utf-8")
        self.assertTrue(summary.splitlines()[0].startswith("개발 구간"))
        self.assertIn("공용", summary)
        self.assertIn("학습 1회", summary)

    # --pooled 폴더 — 학습 함수 없이 저장 모델로 추론, 메타 정보가 metrics.json·요약에
    def testcheckpoint(self):
        folder = self.root / "model"
        stored(folder, "2025-04-30")
        with mock.patch.object(predict_module, "trial", tripwire), \
             mock.patch.object(predict_module, "fullmodel", tripwire), \
             mock.patch.object(pool_module, "session", tripwire):
            code, _, _ = self.outcome(self.argv("--pooled", str(folder)), reader=Source())
        self.assertEqual(code, 0)
        table = pd.read_csv(self.out / "rows.csv", dtype={"code": str})
        self.assertEqual(sorted(table["code"].unique()), ["000001", "000660"])
        self.assertEqual(set(table["val_mae"]), {1.0})
        report = json.loads((self.out / "metrics.json").read_text(encoding="utf-8"))
        model = report["model"]
        self.assertEqual((model["kind"], model["mode"], model["folder"]), ("pooled", "checkpoint", str(folder)))
        self.assertEqual((model["version"], model["validated"], model["data"]["last"]), (VERSION, False, "2025-04-30"))
        self.assertIn(str(folder), (self.out / "summary.md").read_text(encoding="utf-8"))

    # 거부(종료 코드 2) — 데이터를 읽지 않고 표식·결과 폴더를 만들지 않음
    # 읽을 수 없는 --pooled(--final이어도 표식 전에), --retrain과 --pooled 같이, --retrain과 --final 같이(최종 판정은 저장 모델로),
    # 음수 간격, --retrain 없는 --since, --since가 --start 이후
    def testrefusal(self):
        cases = [("--pooled", str(self.root / "none"), "--final"),
                 ("--pooled", str(self.root / "none"), "--retrain", "0"),
                 ("--retrain", "0", "--final"),
                 ("--retrain", "-1"),
                 ("--since", "2024-01-01"),
                 ("--retrain", "0", "--since", "2025-05-01")]
        for extra in cases:
            with self.subTest(extra=extra):
                code, _, err = self.outcome(self.argv(*extra), reader=tripwire, core=tripwire)
                self.assertEqual(code, 2)
                self.assertTrue(err.strip())
                self.assertFalse(self.marker.exists())
                self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
