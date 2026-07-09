import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock
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

if __name__ == "__main__":
    unittest.main()
