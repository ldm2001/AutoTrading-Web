import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline.walk import HORIZON, cuts, rows
from service.ai.predict import codeseed


# 합성 일봉 — 영업일, 종가 = base + 위치 (1씩 오름)
def bars(first: str = "2024-01-01", last: str = "2025-06-30", base: float = 1000.0) -> pd.DataFrame:
    idx = pd.bdate_range(first, last)
    close = [base + k for k in range(len(idx))]
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": [1000] * len(idx)}, index=idx)


# 기록용 가짜 core — 넘겨받은 창을 남기고, 기준가 대비 매일 +1%씩 오르는 5일 예측을 돌려줌
class Recorder:
    def __init__(self, fault: set | None = None, shift: bool = False) -> None:
        self.calls: list[dict] = []
        self.fault = fault or set()
        self.shift = shift

    def __call__(self, window: pd.DataFrame, seed: int, label: str) -> dict:
        last = window.index.max()
        self.calls.append({"code": label, "first": window.index.min(), "last": last,
                           "rows": len(window), "seed": seed})
        if (label, str(last.date())) in self.fault:
            raise RuntimeError("학습 실패")
        base = float(window["Close"].iloc[-1])
        day = last + pd.Timedelta(days=1) if self.shift else last
        return {"predictions": [{"date": f"d{k}", "close": base * (1 + 0.01 * k)} for k in range(1, 6)],
                "metrics": {"mae": 0.1}, "base_close": int(base), "base_date": str(day.date())}


# 2025-05-01 ~ 2025-06-13의 거래일 32개 → 5일 간격 컷 7개 중 마지막(06-12)은 목표가 구간 밖이라 버림
_CUTS = ["2025-05-01", "2025-05-08", "2025-05-15", "2025-05-22", "2025-05-29", "2025-06-05"]
_TARGETS = ["2025-05-08", "2025-05-15", "2025-05-22", "2025-05-29", "2025-06-05", "2025-06-12"]


# 워크포워드 — 컷 일정, 미래 누출 없음, 블록 비겹침, 건너뜀 사유
class WalkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.frames = {"000001": bars(), "000002": bars(base=2000.0)}

    def result(self, core, **kw):
        return rows(self.frames, "2025-05-01", "2025-06-13", core=core, **kw)

    # 컷은 5거래일마다, 목표일 = 다음 컷 (블록이 겹치지 않음), 데이터가 end 뒤에 있어도 목표는 end 안
    def testcuts(self):
        table, skipped = self.result(Recorder())
        self.assertEqual(skipped, [])
        for code in self.frames:
            mine = [r for r in table if r["code"] == code]
            self.assertEqual([r["base_date"] for r in mine], _CUTS)
            self.assertEqual([r["target_date"] for r in mine], _TARGETS)
            for a, b in zip(mine, mine[1:]):
                self.assertLessEqual(a["target_date"], b["base_date"])

    # 미래 누출 없음 — 넘겨받은 창의 마지막 날짜 = 컷, 첫 날짜는 컷 − 365일 이후 (그 앞은 넘기지 않음)
    def testleak(self):
        rec = Recorder()
        self.result(rec)
        self.assertEqual(len(rec.calls), len(_CUTS) * 2)
        for call, cut in zip(rec.calls, [c for c in _CUTS for _ in range(2)]):
            cut = pd.Timestamp(cut)
            self.assertEqual(call["last"], cut)
            self.assertGreaterEqual(call["first"], cut - pd.Timedelta(days=365))
            self.assertLess(call["first"] - pd.Timedelta(days=4), cut - pd.Timedelta(days=365))
            self.assertEqual(call["seed"], codeseed(call["code"]))

    # 결과 행 값 — 종가 1씩 증가: 실제 5일 수익률 = 5 / 기준가, 모멘텀 = 기준가 / (기준가 − 5) − 1
    def testvalues(self):
        table, _ = self.result(Recorder())
        r = table[0]
        base = float(self.frames[r["code"]].loc[r["base_date"], "Close"])
        self.assertEqual(r["base_close"], int(base))
        self.assertAlmostEqual(r["actual_close"], base + 5)
        self.assertAlmostEqual(r["actual_ret"], 5 / base * 100)
        self.assertAlmostEqual(r["mom_ret"], (base / (base - 5) - 1) * 100)
        self.assertAlmostEqual(r["pred5"], base * 1.05)
        self.assertAlmostEqual(r["pred_ret"], 5.0)
        # Scorer.labscore 그대로(문 없는 Direction 규칙) — +5%이고 5일 연속 상승이라 +W_PREDICT
        self.assertEqual(r["direction"], 10.0)
        self.assertEqual(r["val_mae"], 0.1)

    # 컷 간격 1 (주입용 표) — 목표가 데이터 안인 거래일 전부 (32 − 5 = 27개)
    def teststep(self):
        table, skipped = rows({"000001": self.frames["000001"]}, "2025-05-01", "2025-06-13",
                              step=1, core=Recorder())
        self.assertEqual(len(table), 27)
        self.assertEqual(table[-1]["base_date"], "2025-06-06")
        self.assertEqual(table[-1]["target_date"], "2025-06-13")
        self.assertEqual(skipped, [])
        with self.assertRaises(ValueError):
            cuts(self.frames["000001"].index, "2025-05-01", "2025-06-13", step=0)

    # 학습 실패·기준일 불일치·결측 컷은 행 대신 사유를 남기고 다음 컷으로
    def testskipped(self):
        table, skipped = self.result(Recorder(fault={("000002", "2025-05-15")}))
        self.assertEqual(len(table), len(_CUTS) * 2 - 1)
        self.assertEqual(skipped, [{"code": "000002", "base_date": "2025-05-15",
                                    "reason": "RuntimeError: 학습 실패"}])
        _, skipped = self.result(Recorder(shift=True))
        self.assertEqual(len(skipped), len(_CUTS) * 2)
        self.assertTrue(all(s["reason"].startswith("기준일 불일치") for s in skipped))

    # 한 종목만 거래가 없거나 데이터가 먼저 끝나면 그 종목의 그 컷만 버림
    def testgaps(self):
        short = self.frames["000002"]
        self.frames["000002"] = short[(short.index != "2025-05-15") & (short.index <= "2025-06-10")]
        table, skipped = self.result(Recorder())
        reasons = {(s["base_date"], s["reason"]) for s in skipped if s["code"] == "000002"}
        self.assertEqual(reasons, {("2025-05-15", "거래 없음"), ("2025-06-05", "목표 기간이 데이터 밖")})
        self.assertEqual(len([r for r in table if r["code"] == "000001"]), len(_CUTS))


if __name__ == "__main__":
    unittest.main()
