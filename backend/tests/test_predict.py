import asyncio
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
import service.ai.predict as predict_module
from service.ai.predict import Predictor
from service.infra.ttl_cache import TTLCache
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 실연결 없이 예측기 생성 (인메모리 캐시 강제)
def build() -> Predictor:
    with mock.patch.object(TTLCache, "conn", lambda self: None):
        p = Predictor()
    return p

class SingleFlightTest(unittest.TestCase):
    # 동일 종목 동시 요청은 학습 1회로 병합
    def test_concurrent_merge(self):
        p = build()
        calls: list[str] = []

        def fit(symbol, *args, **kwargs):
            calls.append(symbol)
            return {"predictions": [], "metrics": {"mae": 0, "accuracy_pct": 0}}

        p.fit = fit

        async def run():
            return await asyncio.gather(p.predict("5930"), p.predict("005930"))

        r1, r2 = asyncio.run(run())
        self.assertEqual(calls, ["005930"])
        self.assertIs(r1, r2)
        self.assertEqual(p._inflight, {})

    # 완료 후에는 캐시 히트로 재학습 없음
    def test_cache_after_flight(self):
        p = build()
        calls: list[str] = []

        def fit(symbol, *args, **kwargs):
            calls.append(symbol)
            return {"predictions": []}

        p.fit = fit

        asyncio.run(p.predict("000660"))
        asyncio.run(p.predict("000660"))
        self.assertEqual(calls, ["000660"])

    # 학습 실패는 대기자 전원에 전파되고 in-flight는 정리됨
    def test_failure_clears_inflight(self):
        p = build()

        def fit(symbol, *args, **kwargs):
            raise ValueError("데이터 수집 실패")

        p.fit = fit

        with self.assertRaises(ValueError):
            asyncio.run(p.predict("005930"))
        self.assertEqual(p._inflight, {})

class FeatTest(unittest.TestCase):
    # 거래량 0 → 재개 구간의 pct_change inf가 피처에 남지 않음
    def test_inf_scrubbed(self):
        import numpy as np
        import pandas as pd

        p = build()
        df = pd.DataFrame({
            "Open":   [100.0] * 30,
            "High":   [101.0] * 30,
            "Low":    [99.0] * 30,
            "Close":  [100.0] * 30,
            "Volume": [0.0] * 15 + [1000.0] * 15,
        })
        out = p.feat(df)
        self.assertTrue(np.isfinite(out.to_numpy()).all())

class SettledTest(unittest.TestCase):
    # 고정 시각/개장 여부로 settled 실행
    def run_settled(self, dates, now, open_day):
        import pandas as pd

        class _Clock:
            @classmethod
            def now(cls):
                return now

        df = pd.DataFrame(
            {"Close": [float(i) for i in range(len(dates))]},
            index=pd.to_datetime(dates),
        )
        p = build()
        with mock.patch.object(predict_module, "datetime", _Clock), \
             mock.patch.object(predict_module, "mkt", lambda d=None: open_day):
            return p.settled(df, "005930")

    # 장중에는 형성 중인 당일봉 제거
    def test_intraday_drop(self):
        out = self.run_settled(
            ["2026-07-10", "2026-07-13", "2026-07-14"],
            datetime(2026, 7, 14, 10, 0), open_day=True,
        )
        self.assertEqual(len(out), 2)
        self.assertEqual(out.index[-1].date(), datetime(2026, 7, 13).date())

    # 장 마감 후에는 당일 완결봉 유지
    def test_after_close_keep(self):
        out = self.run_settled(
            ["2026-07-13", "2026-07-14"],
            datetime(2026, 7, 14, 15, 31), open_day=True,
        )
        self.assertEqual(len(out), 2)

    # 휴장일 조회는 손대지 않음
    def test_holiday_keep(self):
        out = self.run_settled(
            ["2026-07-13", "2026-07-14"],
            datetime(2026, 7, 14, 10, 0), open_day=False,
        )
        self.assertEqual(len(out), 2)

    # 장중이라도 당일봉이 아직 없으면 그대로
    def test_no_today_bar(self):
        out = self.run_settled(
            ["2026-07-10", "2026-07-13"],
            datetime(2026, 7, 14, 10, 0), open_day=True,
        )
        self.assertEqual(len(out), 2)

    # raw()의 FDR 경로에 가드가 적용됨
    def test_raw_strips_forming_bar(self):
        import types
        import pandas as pd

        idx = pd.to_datetime(["2026-07-13", "2026-07-14"])
        frame = pd.DataFrame(
            {"Open": [1.0, 1.0], "High": [1.0, 1.0], "Low": [1.0, 1.0],
             "Close": [1.0, 1.0], "Volume": [10, 10]},
            index=idx,
        )
        fake_fdr = types.SimpleNamespace(DataReader=lambda *a, **k: frame)

        class _Clock:
            @classmethod
            def now(cls):
                return datetime(2026, 7, 14, 10, 0)

        p = build()
        with mock.patch.dict(sys.modules, {"FinanceDataReader": fake_fdr}), \
             mock.patch.object(predict_module, "datetime", _Clock), \
             mock.patch.object(predict_module, "mkt", lambda d=None: True):
            out = p.raw("005930")
        self.assertEqual(len(out), 1)
        self.assertEqual(out.index[-1].date(), datetime(2026, 7, 13).date())

if __name__ == "__main__":
    unittest.main()
