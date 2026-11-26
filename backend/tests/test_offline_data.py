import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from offline import daily
from offline.daily import cutoff, history, marker
from offline.rules import HOLDOUT_START

_HOLDOUT = pd.Timestamp(HOLDOUT_START)


# 합성 일봉 (영업일)
def bars(first: str = "2024-01-01", last: str = "2026-01-30") -> pd.DataFrame:
    idx = pd.bdate_range(first, last, name="Date")
    close = [1000.0 + k for k in range(len(idx))]
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": [1000] * len(idx), "Change": [0.001] * len(idx)}, index=idx)


# 가짜 일봉 공급자 — 요청을 기록하고, 요청한 끝 날짜를 무시한 채 최종 구간까지 돌려줌 (로더가 잘라야 함)
class Source:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, code: str, start: str, end: str) -> pd.DataFrame:
        self.calls.append((code, start, end))
        return bars()


# 최종 구간 보호 — --final 없이는 HOLDOUT_START 이후 행이 로드 단계에서 사라짐
class HoldoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # 개발 구간 — 요청 끝 날짜부터 HOLDOUT 전날로 줄이고, 공급자가 더 줘도 잘라 내며, 캐시에도 쓰지 않음
    def testloader(self):
        src = Source()
        df = history("005930", "2025-05-01", "2026-01-15", cache=self.cache, reader=src)
        self.assertEqual(src.calls, [("005930", "2024-05-01", "2025-11-23")])
        self.assertLess(df.index.max(), _HOLDOUT)
        self.assertEqual(df.index.max(), pd.Timestamp("2025-11-21"))
        self.assertEqual(df.index.min(), pd.Timestamp("2024-05-01"))
        files = list(self.cache.glob("*.csv"))
        self.assertEqual([f.name for f in files], ["005930_2024-05-01_2025-11-23.csv"])
        stored = pd.read_csv(files[0], index_col=0, parse_dates=True)
        self.assertLess(stored.index.max(), _HOLDOUT)

    # 캐시에 최종 구간 행이 들어 있어도(최종 실행·수동 복사) 개발 구간 로드에서는 사라짐, 공급자는 안 부름
    def testcache(self):
        self.cache.mkdir(parents=True)
        bars("2024-05-01", "2026-01-30").to_csv(self.cache / "005930_2024-05-01_2025-11-23.csv")
        src = Source()
        df = history("005930", "2025-05-01", "2026-01-15", cache=self.cache, reader=src)
        self.assertEqual(src.calls, [])
        self.assertLess(df.index.max(), _HOLDOUT)
        self.assertEqual(list(df.columns), ["Open", "High", "Low", "Close", "Volume", "Change"])

    # --final — 요청한 끝 날짜까지 그대로 (끝 날짜 뒤는 버림)
    def testfinal(self):
        src = Source()
        df = history("005930", "2025-05-01", "2026-01-15", cache=self.cache, final=True, reader=src)
        self.assertEqual(src.calls, [("005930", "2024-05-01", "2026-01-15")])
        self.assertGreaterEqual(df.index.max(), _HOLDOUT)
        self.assertEqual(df.index.max(), pd.Timestamp("2026-01-15"))
        self.assertTrue(cutoff(df, final=True).equals(df))
        self.assertLess(cutoff(df).index.max(), _HOLDOUT)

    # lookback — 시작일 앞에 붙이는 달력일 수 (기본 365 = 학습 창, 공용 학습은 0으로 --since부터 그대로)
    def testlookback(self):
        src = Source()
        df = history("005930", "2023-06-01", "2024-11-21", cache=self.cache, reader=src, lookback=0)
        self.assertEqual(src.calls, [("005930", "2023-06-01", "2024-11-21")])
        self.assertEqual(df.index.min(), pd.Timestamp("2024-01-01"))
        self.assertEqual([f.name for f in self.cache.glob("*.csv")], ["005930_2023-06-01_2024-11-21.csv"])
        far = history("005930", "2025-05-01", "2025-11-21", cache=self.cache, reader=src, lookback=500)
        self.assertEqual(src.calls[-1], ("005930", "2023-12-18", "2025-11-21"))
        self.assertEqual(far.index.max(), pd.Timestamp("2025-11-21"))

    # 빈 응답은 오류 (조용히 0행으로 평가하지 않음)
    def testempty(self):
        with self.assertRaises(ValueError):
            history("005930", "2025-05-01", "2025-11-21", cache=self.cache, reader=lambda *a: pd.DataFrame())

    # 기본 경로 — research 볼륨 아래 하위 폴더·다른 이름이라 research.py의 {code}.jsonl과 섞이지 않음
    def testpaths(self):
        root = Path(__file__).resolve().parents[1] / "data" / "research"
        self.assertEqual(daily.CACHE, root / "cache")
        self.assertEqual(daily.MARKER, root / "holdout_used.json")
        self.assertNotEqual(daily.MARKER.suffix, ".jsonl")


# 최종 구간 사용 표식 — 한 번만
class MarkerTest(unittest.TestCase):
    def testonce(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "holdout_used.json"
            marker(path, {"codes": ["005930"], "start": "2025-05-01"})
            row = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(row["holdout_start"], HOLDOUT_START)
            self.assertEqual(row["config"]["codes"], ["005930"])
            self.assertIn("ran_at", row)
            with self.assertRaises(FileExistsError):
                marker(path, {"codes": ["000660"]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["config"]["codes"], ["005930"])


if __name__ == "__main__":
    unittest.main()
