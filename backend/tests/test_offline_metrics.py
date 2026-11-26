import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline import rules
from offline.metrics import buckets, hits, ic, judgment, mae, stats, wilson


# 결과 표 한 행 (지표가 읽는 필드만)
def row(day: str, code: str, pred: float, actual: float, mom: float | None = None, direction: float = 0) -> dict:
    return {"base_date": day, "code": code, "pred_ret": pred, "actual_ret": actual,
            "mom_ret": mom, "direction": direction}


# 날짜 하나에 n종목 — 예측 1..n, 실제는 actual(k)
def day(name: str, n: int, actual) -> list[dict]:
    return [row(name, f"{k:06d}", float(k), float(actual(k))) for k in range(1, n + 1)]


# 1-2 사전 등록 기준 상수 — 지시서 값 그대로
class RulesTest(unittest.TestCase):
    def testvalues(self):
        self.assertEqual(rules.REGISTERED, "2026-11-23")
        self.assertEqual(rules.HOLDOUT_START, "2025-11-24")
        self.assertEqual(rules.IC_T_MIN, 2.0)
        self.assertEqual(rules.IC_MIN_NAMES, 10)
        self.assertEqual(rules.SKILL_MIN, 0.0)
        self.assertEqual(rules.HIT_ABS_PCT, 1.0)
        self.assertEqual(rules.BUCKET_MIN_N, 30)


# 수익률 MAE와 "0% 예측" MAE, skill = 1 − MAE_model / MAE_zero
class MaeTest(unittest.TestCase):
    # |오차| 1·2·1 → 4/3, |실제| 2·3·1 → 2, skill 1/3
    def testskill(self):
        out = mae([row("d", "a", 1, 2), row("d", "b", -1, -3), row("d", "c", 0, 1)])
        self.assertEqual(out["n"], 3)
        self.assertAlmostEqual(out["mae_model"], 4 / 3)
        self.assertAlmostEqual(out["mae_zero"], 2.0)
        self.assertAlmostEqual(out["skill"], 1 / 3)

    # 표본 없음·실제 수익률이 전부 0 → skill 없음 (판정 불가)
    def testzero(self):
        self.assertIsNone(mae([])["skill"])
        self.assertIsNone(mae([row("d", "a", 1, 0), row("d", "b", -1, 0)])["skill"])

    # 예측이나 실제가 비면 표본에서 뺌
    def testmissing(self):
        out = mae([row("d", "a", 1, 2), {"base_date": "d", "pred_ret": None, "actual_ret": 1.0}])
        self.assertEqual(out["n"], 1)


# 부호 적중률 — |예측| > 1% 표본만, 같은 표본의 "항상 상승"·"5일 모멘텀"과 비교
class HitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.table = [
            row("d", "a", 2.0, 1.0, mom=1.0),     # 모델 적중, 상승 적중, 모멘텀 적중
            row("d", "b", -3.0, 2.0, mom=-1.0),   # 모델 실패, 상승 적중, 모멘텀 실패
            row("d", "c", 0.5, 4.0, mom=1.0),     # |예측| ≤ 1% → 제외
            row("d", "e", 1.5, -1.0, mom=2.0),    # 모델 실패, 상승 실패, 모멘텀 실패
            row("d", "f", -1.2, -0.5, mom=3.0),   # 모델 적중, 상승 실패, 모멘텀 실패
            row("d", "g", 1.0, 3.0, mom=1.0),     # 정확히 1% → 제외 (초과만)
        ]

    def testbaseline(self):
        out = hits(self.table)
        self.assertEqual(out["n"], 4)
        self.assertEqual((out["model"]["k"], out["up"]["k"], out["momentum"]["k"]), (2, 2, 1))
        self.assertAlmostEqual(out["model"]["rate"], 0.5)
        self.assertAlmostEqual(out["momentum"]["rate"], 0.25)

    # 실제 0%는 어느 쪽에도 적중이 아님
    def testflat(self):
        out = hits([row("d", "a", 2.0, 0.0, mom=1.0)])
        self.assertEqual((out["model"]["k"], out["up"]["k"], out["momentum"]["k"]), (0, 0, 0))

    # Wilson 95% — 2/4 → [0.150, 0.850], 0/0 → 없음
    def testwilson(self):
        lo, hi = wilson(2, 4)
        self.assertAlmostEqual(lo, 0.1500, places=3)
        self.assertAlmostEqual(hi, 0.8500, places=3)
        self.assertEqual(wilson(0, 0), (None, None))
        out = hits(self.table)
        self.assertAlmostEqual(out["model"]["lo"], lo)


# 날짜별 횡단 Spearman rank IC
class IcTest(unittest.TestCase):
    # 완전 일치 = 1.0, 역순 = −1.0
    def testperfect(self):
        out = ic(day("2025-06-02", 10, lambda k: k * 0.3))
        self.assertAlmostEqual(out["series"]["2025-06-02"], 1.0)
        out = ic(day("2025-06-02", 10, lambda k: -k))
        self.assertAlmostEqual(out["series"]["2025-06-02"], -1.0)

    # 종목 수가 IC_MIN_NAMES(10) 미만인 날짜는 제외 — 평균·t에 들어가지 않음
    def testshort(self):
        table = day("2025-06-02", 10, lambda k: k) + day("2025-06-09", 10, lambda k: -k) \
            + day("2025-06-16", 9, lambda k: k)
        out = ic(table)
        self.assertEqual(out["dates"], 2)
        self.assertEqual(out["excluded"], 1)
        self.assertIn("2025-06-16", out["excluded_dates"])
        self.assertAlmostEqual(out["mean"], 0.0)
        self.assertAlmostEqual(out["std"], math.sqrt(2))
        self.assertAlmostEqual(out["t"], 0.0)

    # 순위 상관(비선형이어도 순서가 같으면 1.0)과 t = 평균 / (표준편차 / √n)
    def testtstat(self):
        table = day("a", 10, lambda k: k ** 3) + day("b", 10, lambda k: k) \
            + day("c", 10, lambda k: [2, 1, 3, 4, 5, 6, 7, 8, 10, 9][k - 1])
        out = ic(table)
        third = 1 - 6 * (1 + 1 + 1 + 1) / (10 * (100 - 1))
        self.assertAlmostEqual(out["series"]["a"], 1.0)
        self.assertAlmostEqual(out["series"]["c"], third)
        vals = [1.0, 1.0, third]
        mean = sum(vals) / 3
        std = math.sqrt(sum((v - mean) ** 2 for v in vals) / 2)
        self.assertAlmostEqual(out["t"], mean / (std / math.sqrt(3)))

    # 날짜가 1개뿐이면 t 없음 (판정 불가)
    def testsingle(self):
        out = ic(day("a", 12, lambda k: k))
        self.assertEqual(out["dates"], 1)
        self.assertIsNone(out["t"])


# Direction 구간(−10, −5, 0, +5, +10)별 실제 5일 수익률 평균과 건수
class BucketTest(unittest.TestCase):
    def testtable(self):
        table = [row("d", "a", 0, 1.0, direction=10), row("d", "b", 0, 3.0, direction=10.0),
                 row("d", "c", 0, -2.0, direction=-5.0), row("d", "e", 0, 0.5, direction=0)]
        out = {b["score"]: b for b in buckets(table)}
        self.assertEqual(list(out), [-10, -5, 0, 5, 10])
        self.assertEqual((out[10]["n"], out[10]["mean_ret"]), (2, 2.0))
        self.assertEqual((out[-5]["n"], out[-5]["mean_ret"]), (1, -2.0))
        self.assertEqual((out[5]["n"], out[5]["mean_ret"]), (0, None))


# 판정 — 넷 다 통과해야 PASS, 표본 부족은 실패
class JudgmentTest(unittest.TestCase):
    # 통과하는 지표 묶음 (손으로 만든 값)
    def board(self) -> dict:
        return {
            "ic": {"t": 2.5, "dates": 40},
            "mae": {"skill": 0.05},
            "hits": {"n": 100, "model": {"rate": 0.58}, "up": {"rate": 0.52}, "momentum": {"rate": 0.50}},
            "buckets": [{"score": -10, "n": 40, "mean_ret": -1.0}, {"score": -5, "n": 10, "mean_ret": 5.0},
                        {"score": 0, "n": 50, "mean_ret": 0.1}, {"score": 5, "n": 35, "mean_ret": 0.4},
                        {"score": 10, "n": 31, "mean_ret": 0.9}],
        }

    def testpass(self):
        out = judgment(self.board())
        self.assertTrue(out["pass"], out)
        self.assertEqual(out["message"], "PASS")

    # 빈 결과 표 — 네 항목 모두 판정 불가 → 실패, "Direction 효과 0 고정"
    def testshortage(self):
        out = judgment(stats([]))
        self.assertFalse(out["pass"])
        self.assertEqual(out["checks"], {"ic": False, "skill": False, "hit": False, "bucket": False})
        self.assertEqual(out["message"], "Direction 효과 0 고정")

    # 항목 하나씩 무너뜨리면 실패 (기준 경계 포함)
    def testeach(self):
        cases = {
            "ic": lambda b: b["ic"].update(t=None),
            "skill": lambda b: b["mae"].update(skill=0.0),
            "hit": lambda b: b["hits"]["momentum"].update(rate=0.58),
            "bucket": lambda b: b["buckets"][4].update(mean_ret=0.3),
        }
        for key, breaker in cases.items():
            with self.subTest(key=key):
                board = self.board()
                breaker(board)
                out = judgment(board)
                self.assertFalse(out["pass"])
                self.assertFalse(out["checks"][key])
        board = self.board()
        board["ic"]["t"] = 2.0
        self.assertTrue(judgment(board)["checks"]["ic"])

    # 건수 30 이상 구간이 1개뿐이면 순서를 볼 수 없어 실패
    def testonebucket(self):
        board = self.board()
        for b in board["buckets"][1:]:
            b["n"] = 29
        out = judgment(board)
        self.assertFalse(out["checks"]["bucket"])

    # 컷 간격 < 5 (겹치는 블록) — 지표가 좋아도 판정 무효
    def testoverlap(self):
        out = judgment(self.board(), overlap=True)
        self.assertFalse(out["pass"])
        self.assertEqual(out["message"], "Direction 효과 0 고정")


if __name__ == "__main__":
    unittest.main()
