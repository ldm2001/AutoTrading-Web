import asyncio
import gc
import sys
import threading
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
import service.ai.predict as predict_module
import service.infra.ttl_cache as ttl_cache_module
from service.ai.predict import Predictor
from service.infra.ttl_cache import TTLCache
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 실연결 없이 예측기 생성 (인메모리 캐시 강제)
def predrig() -> Predictor:
    with mock.patch.object(TTLCache, "conn", lambda self: None):
        p = Predictor()
    return p

class SingleFlightTest(unittest.TestCase):
    # 동일 종목 동시 요청은 학습 1회로 병합
    def testshared(self):
        p = predrig()
        calls: list[str] = []

        def trainer(symbol, *args, **kwargs):
            calls.append(symbol)
            return {"predictions": [], "metrics": {"mae": 0, "mae_zero": 0, "skill": None, "n_val": 0}}

        p.training = trainer

        async def pair():
            return await asyncio.gather(p.forecast("5930"), p.forecast("005930"))

        r1, r2 = asyncio.run(pair())
        self.assertEqual(calls, ["005930"])
        self.assertIs(r1, r2)
        self.assertEqual(p._inflight, {})

    # 완료 후에는 캐시 히트로 재학습 없음
    def testflightcache(self):
        p = predrig()
        calls: list[str] = []

        def trainer(symbol, *args, **kwargs):
            calls.append(symbol)
            return {"predictions": []}

        p.training = trainer

        asyncio.run(p.forecast("000660"))
        asyncio.run(p.forecast("000660"))
        self.assertEqual(calls, ["000660"])

    # 학습 실패는 대기자 전원에 전파되고 in-flight는 정리됨
    def testflightfault(self):
        p = predrig()

        def trainer(symbol, *args, **kwargs):
            raise ValueError("데이터 수집 실패")

        p.training = trainer

        with self.assertRaises(ValueError):
            asyncio.run(p.forecast("005930"))
        self.assertEqual(p._inflight, {})

# Event로 막히는 가짜 학습을 단 예측기 — 대기자·백그라운드 학습 테스트 공통
class GatedBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.p = predrig()
        self.gate = threading.Event()
        self.calls: list[str] = []
        self.boom = False

        # gate가 열릴 때까지 막힘 (최대 2초) — boom이면 실패
        def trainer(symbol, *args, **kwargs):
            self.calls.append(symbol)
            self.gate.wait(timeout=2)
            if self.boom:
                raise ValueError("데이터 수집 실패")
            return {"predictions": [], "metrics": {"mae": 0, "mae_zero": 0, "skill": None, "n_val": 0}}

        self.p.training = trainer

    # 남은 학습을 풀고 끝날 때까지 대기 후 스레드 정리
    async def asyncTearDown(self) -> None:
        self.gate.set()
        await self.drained()
        self.p._executor.shutdown(wait=True)

    # 학습 스레드가 training에 들어갈 때까지 대기
    async def kickoff(self) -> None:
        for _ in range(200):
            if self.calls:
                return
            await asyncio.sleep(0.01)
        self.fail("학습이 시작되지 않음")

    # in-flight·백그라운드 학습이 모두 끝날 때까지 대기 (최대 2초)
    async def drained(self) -> None:
        for _ in range(200):
            if not self.p._inflight and not getattr(self.p, "_bg", None):
                return
            await asyncio.sleep(0.01)

# S-04 — 공유 학습 태스크: 대기자 하나가 취소돼도 학습과 나머지 대기자는 유지
class ShieldTest(GatedBase):
    # 두 대기자 중 하나를 취소해도 나머지는 결과를 받고 training은 1회
    async def testwaiter(self):
        w1 = asyncio.create_task(self.p.forecast("005930"))
        w2 = asyncio.create_task(self.p.forecast("005930"))
        await self.kickoff()
        w1.cancel()
        await asyncio.sleep(0.01)
        self.gate.set()
        try:
            result = await asyncio.wait_for(w2, 2)
        except asyncio.CancelledError:
            self.fail("남은 대기자가 CancelledError를 받음")
        self.assertEqual(result["metrics"]["mae"], 0)
        self.assertEqual(self.calls, ["005930"])
        self.assertTrue(w1.cancelled())

    # 대기자가 모두 취소된 뒤 학습이 실패해도 끝까지 돌고 "Task exception was never retrieved"가 남지 않음
    async def testorphan(self):
        loop = asyncio.get_running_loop()
        errors: list[str] = []
        handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, ctx: errors.append(ctx.get("message", "")))
        try:
            self.boom = True
            waiter = asyncio.create_task(self.p.forecast("005930"))
            await self.kickoff()
            inner = self.p._inflight["005930"]
            waiter.cancel()
            await asyncio.sleep(0.01)
            self.gate.set()
            for _ in range(200):
                if inner.done():
                    break
                await asyncio.sleep(0.01)
            self.assertFalse(inner.cancelled(), "대기자 취소가 학습까지 취소함")
            del inner, waiter
            await asyncio.sleep(0.01)
            gc.collect()
        finally:
            loop.set_exception_handler(handler)
        self.assertEqual(errors, [])

# M-03 — 백그라운드 학습: 기다리지 않음, 학습 중·최근 실패 종목은 건너뜀, 예외를 던지지 않음
class WarmTest(GatedBase):
    # 곧바로 돌아오고 학습은 뒤에서 1회 — 연달아 불러도 태스크 1개, 끝나면 캐시에 남고 참조 해제
    async def testinstant(self):
        self.p.warmup("5930")
        self.p.warmup("005930")
        await self.kickoff()
        self.assertEqual(len(self.p._bg), 1)
        self.assertIn("005930", self.p._inflight)
        self.p.warmup("005930")
        self.assertEqual(len(self.p._bg), 1)
        self.gate.set()
        await self.drained()
        self.assertEqual(self.calls, ["005930"])
        self.assertEqual(self.p._bg, set())
        self.assertIsNotNone(self.p.cached("005930"))

    # 화면 요청이 학습 중인 종목이면 warmup은 아무것도 하지 않음
    async def testinflight(self):
        waiter = asyncio.create_task(self.p.forecast("005930"))
        await self.kickoff()
        self.p.warmup("005930")
        self.assertEqual(self.p._bg, set())
        self.gate.set()
        await waiter
        self.assertEqual(self.calls, ["005930"])

    # 실패는 경고 로그 + 600초 동안 재시작 없음, 600초가 지나면 다시 시작
    async def testnoretry(self):
        self.boom = True
        self.gate.set()
        with self.assertLogs(predict_module.logger, level="WARNING") as logs:
            self.p.warmup("005930")
            await self.drained()
        self.assertTrue(any("005930" in m for m in logs.output), logs.output)
        self.p.warmup("005930")
        await self.drained()
        self.assertEqual(self.calls, ["005930"])
        later = time.time() + 601
        with mock.patch.object(ttl_cache_module, "time", types.SimpleNamespace(time=lambda: later)), \
                self.assertLogs(predict_module.logger, level="WARNING"):
            self.p.warmup("005930")
            await self.drained()
        self.assertEqual(self.calls, ["005930", "005930"])

    # 캐시 조회가 예외를 던져도 warmup은 경고 로그만 남기고 돌아옴 (학습 시작 안 함)
    async def testnoraise(self):
        def broken(_key):
            raise RuntimeError("cache down")

        self.p._cache.lookup = broken
        with self.assertLogs(predict_module.logger, level="WARNING"):
            self.p.warmup("005930")
        await asyncio.sleep(0.01)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.p._bg, set())

class FeatTest(unittest.TestCase):
    # 거래량 0 → 재개 구간에서도 피처가 유한 (2단계: 쓰이지 않던 Volume_Change 열을 없애고(M-10) 거래량 비율 피처로 같은 성질을 고정)
    def testinf(self):
        import numpy as np
        import pandas as pd

        df = pd.DataFrame({
            "Open":   [100.0] * 30,
            "High":   [101.0] * 30,
            "Low":    [99.0] * 30,
            "Close":  [100.0] * 30,
            "Volume": [0.0] * 15 + [1000.0] * 15,
        })
        out = predict_module.features(df)
        self.assertEqual(len(out), 10)
        self.assertNotIn("Volume_Change", out.columns)
        self.assertTrue(np.isfinite(out.to_numpy()).all())

    # 2-1 — 이동평균이 채워지지 않는 앞 20행은 버리고(0으로 메우지 않음) 7개 피처만, 남은 값은 모두 유한
    def testwarm(self):
        import numpy as np

        df = frame(60)
        out = predict_module.features(df)
        self.assertEqual(list(out.columns), list(predict_module.FEATURES))
        self.assertEqual(len(out), 40)
        self.assertEqual(out.index[0], df.index[20])
        self.assertTrue(np.isfinite(out.to_numpy()).all())

    # 2-1 — 가격 수준이 들어가지 않음: 시가·고가·저가·종가를 10배 해도 피처가 같음
    def testlevel(self):
        import numpy as np

        df = frame(60)
        big = df.copy()
        big[["Open", "High", "Low", "Close"]] *= 10
        np.testing.assert_allclose(predict_module.features(big).to_numpy(),
                                   predict_module.features(df).to_numpy(), rtol=0, atol=1e-9)

    # 2-1 — 거래정지일(거래량 0)이 이어져도 거래량 비율 log((V+1)/(MA20(V)+1))이 유한
    def testvolume(self):
        import numpy as np

        df = frame(60)
        df.iloc[25:35, df.columns.get_loc("Volume")] = 0.0
        out = predict_module.features(df)
        self.assertTrue(np.isfinite(out.to_numpy()).all())
        self.assertLess(out.loc[df.index[34], "vol"], 0)

    # 2-1 — 남은 입력에 NaN·inf가 있으면 조용히 0으로 바꾸지 않고 ValueError (종가 결측, 고가 0)
    def testnan(self):
        import numpy as np

        for col, value in (("Close", np.nan), ("High", 0.0)):
            with self.subTest(col=col):
                df = frame(60)
                df.iloc[40, df.columns.get_loc(col)] = value
                with self.assertRaises(ValueError):
                    predict_module.features(df)

class SettledTest(unittest.TestCase):
    # 고정 시각/개장 여부로 settled 실행
    def settledcase(self, dates, now, open_day):
        import pandas as pd

        class ClockStub:
            @classmethod
            def now(cls):
                return now

        df = pd.DataFrame(
            {"Close": [float(i) for i in range(len(dates))]},
            index=pd.to_datetime(dates),
        )
        p = predrig()
        with mock.patch.object(predict_module, "datetime", ClockStub), \
             mock.patch.object(predict_module, "mkt", lambda d=None: open_day):
            return p.settled(df, "005930")

    # 장중에는 형성 중인 당일봉 제거
    def testintraday(self):
        out = self.settledcase(
            ["2026-07-10", "2026-07-13", "2026-07-14"],
            datetime(2026, 7, 14, 10, 0), open_day=True,
        )
        self.assertEqual(len(out), 2)
        self.assertEqual(out.index[-1].date(), datetime(2026, 7, 13).date())

    # 장 마감 후에는 당일 완결봉 유지
    def testpostclose(self):
        out = self.settledcase(
            ["2026-07-13", "2026-07-14"],
            datetime(2026, 7, 14, 15, 31), open_day=True,
        )
        self.assertEqual(len(out), 2)

    # 휴장일 조회는 손대지 않음
    def testholiday(self):
        out = self.settledcase(
            ["2026-07-13", "2026-07-14"],
            datetime(2026, 7, 14, 10, 0), open_day=False,
        )
        self.assertEqual(len(out), 2)

    # 장중이라도 당일봉이 아직 없으면 그대로
    def testnodaybar(self):
        out = self.settledcase(
            ["2026-07-10", "2026-07-13"],
            datetime(2026, 7, 14, 10, 0), open_day=True,
        )
        self.assertEqual(len(out), 2)

    # raw()의 FDR 경로에 가드가 적용됨
    def testrawbar(self):
        import types
        import pandas as pd

        idx = pd.to_datetime(["2026-07-13", "2026-07-14"])
        frame = pd.DataFrame(
            {"Open": [1.0, 1.0], "High": [1.0, 1.0], "Low": [1.0, 1.0],
             "Close": [1.0, 1.0], "Volume": [10, 10]},
            index=idx,
        )
        fake_fdr = types.SimpleNamespace(DataReader=lambda *a, **k: frame)

        class ClockStub:
            @classmethod
            def now(cls):
                return datetime(2026, 7, 14, 10, 0)

        p = predrig()
        with mock.patch.dict(sys.modules, {"FinanceDataReader": fake_fdr}), \
             mock.patch.object(predict_module, "datetime", ClockStub), \
             mock.patch.object(predict_module, "mkt", lambda d=None: True):
            out = p.raw("005930")
        self.assertEqual(len(out), 1)
        self.assertEqual(out.index[-1].date(), datetime(2026, 7, 13).date())

# 합성 일봉 n행 (영업일 인덱스, 마지막 날 end(기본 2026-11-20), 고정 난수 걸음)
def frame(n: int, end: str = "2026-11-20"):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(7)
    close = 50_000 + np.cumsum(rng.normal(0, 500, n))
    return pd.DataFrame(
        {
            "Open":   close + rng.normal(0, 100, n),
            "High":   close + 300,
            "Low":    close - 300,
            "Close":  close,
            "Volume": rng.integers(1_000, 10_000, n).astype(float),
        },
        index=pd.bdate_range(end=end, periods=n),
    )

# 컷(첫 검증 윈도우의 기준일 = frame(220)의 189번째 행) 이후 행을 바꾼 사본 — 가격 1.5배, 거래량 7배
def mutant(df):
    out = df.copy()
    for col, k in (("Open", 1.5), ("High", 1.5), ("Low", 1.5), ("Close", 1.5), ("Volume", 7.0)):
        out.iloc[189:, out.columns.get_loc(col)] *= k
    return out

# 고정 시각을 돌려주는 시계 (predict_module.datetime 자리)
def clock(now: datetime):
    class ClockStub:
        @classmethod
        def now(cls):
            return now

    return ClockStub

# S-07 — 예측 캐시 유효기간: 다음 개장일 15:30까지(최소 60초), 기준일이 최근 확정 거래일과 다르면 600초
class TtlTest(unittest.TestCase):
    # (지금, base_date, 기대 초)
    def testtable(self):
        cases = [
            (datetime(2026, 11, 20, 16, 0), "2026-11-20", 257_400),     # 금 16:00·기준 금 → 월 15:30
            (datetime(2026, 11, 24, 10, 0), "2026-11-23", 19_800),      # 화 10:00·기준 월 → 화 15:30
            (datetime(2026, 11, 24, 16, 0), "2026-11-23", 600),         # 화 16:00·기준 월 → 공급자 지연
            (datetime(2026, 12, 24, 16, 0), "2026-12-24", 343_800),     # 목 16:00 → 성탄절·주말 건너 월 15:30
            (datetime(2026, 11, 24, 15, 29, 30), "2026-11-23", 60),     # 마감 30초 전 → 최소 60초
        ]
        for now, base, want in cases:
            with self.subTest(now=now, base=base):
                self.assertEqual(predict_module.predttl(base, now), want)

    # 기준일이 없거나 읽을 수 없으면 600초
    def testnobase(self):
        for base in (None, "", "2026/11/23", "garbage"):
            with self.subTest(base=base):
                self.assertEqual(predict_module.predttl(base, datetime(2026, 11, 24, 10, 0)), 600)

    # 기준일이 최근 확정 거래일보다 새로우면(장중 미완성봉 의심) 600초
    def testnewer(self):
        self.assertEqual(predict_module.predttl("2026-11-24", datetime(2026, 11, 24, 10, 0)), 600)

    # forecast()는 결과의 base_date와 같은 시계로 유효기간을 정해 v4 키에 저장 (2단계: 예측·지표 형식 v2 → v3, 3단계: source·validated·spec 추가 v3 → v4)
    def testmemo(self):
        cases = [
            (datetime(2026, 11, 24, 16, 0), 600),
            (datetime(2026, 11, 24, 10, 0), 19_800),
        ]
        for now, want in cases:
            with self.subTest(now=now):
                p = predrig()
                memos: list[tuple[str, float]] = []
                memo = p._cache.memo

                def recorder(key, value, ttl):
                    memos.append((key, ttl))
                    memo(key, value, ttl)

                p._cache.memo = recorder
                p.training = lambda symbol, *a, **k: {
                    "predictions": [], "base_close": 100, "base_date": "2026-11-23",
                }
                with mock.patch.object(predict_module, "datetime", clock(now)):
                    asyncio.run(p.forecast("005930"))
                p._executor.shutdown(wait=True)
                self.assertEqual(memos, [("pred:v4:005930", want)])

# S-07 — 캐시 키 버전: 옛 형식(pred:{code}, pred:v2:{code} = OHLC·accuracy_pct, pred:v3:{code} = source·validated 없음)은 읽지 않고, cached()와 forecast()가 같은 키를 씀
class KeyTest(unittest.TestCase):
    def testversion(self):
        p = predrig()
        p._cache.memo("pred:005930", {"predictions": [{"close": 1}]}, 3600)
        self.assertIsNone(p.cached("005930"))
        p._cache.memo("pred:v2:005930", {
            "predictions": [{"date": "2026-11-23", "open": 1, "high": 1, "low": 1, "close": 1}],
            "metrics": {"mae": 0.1, "accuracy_pct": 90.0}, "base_close": 1, "base_date": "2026-11-20",
        }, 3600)
        self.assertIsNone(p.cached("005930"))
        p._cache.memo("pred:v3:005930", {
            "predictions": [{"date": "2026-11-23", "close": 1, "change_pct": 0.0}],
            "metrics": {"mae": 0.1, "mae_zero": 0.1, "skill": 0.0, "n_val": 26}, "base_close": 1, "base_date": "2026-11-20",
        }, 3600)
        self.assertIsNone(p.cached("005930"))
        calls: list[str] = []

        def trainer(symbol, *args, **kwargs):
            calls.append(symbol)
            return {"predictions": [], "base_close": 100, "base_date": "2026-11-23"}

        p.training = trainer
        result = asyncio.run(p.forecast("5930"))
        p._executor.shutdown(wait=True)
        self.assertEqual(calls, ["005930"])
        self.assertEqual(p.cached("5930"), result)

# S-07·M-01 — 같은 입력이면 같은 예측(종목 코드 고정 시드), 결과에 기준가·기준일, 학습 스레드 1개
class SeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = predrig()
        self.data = frame(220)
        self.p.raw = lambda symbol: self.data.copy()

    def tearDown(self) -> None:
        self.p._executor.shutdown(wait=True)

    # 같은 합성 데이터로 두 번 학습 → 예측·지표가 같음 (에폭 1)
    def testseed(self):
        first = self.p.training("005930", epochs=1)
        second = self.p.training("005930", epochs=1)
        self.assertEqual(first["predictions"], second["predictions"])
        self.assertEqual(first["metrics"], second["metrics"])

    # 기준가 = 확정 일봉 마지막 종가(파이썬 int), 기준일 = 그 날짜
    def testbase(self):
        out = self.p.training("005930", epochs=1)
        self.assertEqual(out["base_close"], int(round(float(self.data["Close"].iloc[-1]))))
        self.assertIs(type(out["base_close"]), int)
        self.assertEqual(out["base_date"], "2026-11-20")

    # 학습 스레드 풀은 1개 — 동시 학습이면 시드를 걸어도 결과가 갈림
    def testworkers(self):
        self.assertEqual(self.p._executor._max_workers, 1)

# M-06 — 확정 일봉 200행 미만이면 학습을 시작하지 않고 ValueError("학습 데이터 부족")
class HistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = predrig()

    def tearDown(self) -> None:
        self.p._executor.shutdown(wait=True)

    # 모델 생성 자리의 덫 — 여기까지 오면 학습이 시작된 것
    @staticmethod
    def tripwire(*args, **kwargs):
        raise RuntimeError("학습 시작됨")

    # 199행 → ValueError, 모델을 만들지 않음
    def testshort(self):
        self.p.raw = lambda symbol: frame(199)
        with mock.patch.object(predict_module, "StockTransformer", self.tripwire):
            with self.assertRaises(ValueError) as ctx:
                self.p.training("005930", epochs=1)
        self.assertIn("학습 데이터 부족", str(ctx.exception))

    # 200행 → 학습 단계까지 감 (경계값)
    def testenough(self):
        self.p.raw = lambda symbol: frame(200)
        with mock.patch.object(predict_module, "StockTransformer", self.tripwire):
            with self.assertRaises(RuntimeError):
                self.p.training("005930", epochs=1)

# training() 출력 (frame(220), 005930, 에폭 2) — 모델·전처리를 바꾸는 단계에서 이유를 적고 다시 기록
# 2단계에서 다시 기록: 피처·타깃(누적 로그수익률)·모델·출력 형식이 바뀜 (1단계 값은 OHLC·accuracy_pct 형식)
_SNAP = {
    "base_close": 34859,
    "base_date": "2026-11-20",
    "metrics": {"mae": 3.2764, "mae_zero": 3.3151, "skill": 0.0117, "n_val": 26},
    "predictions": [
        {"date": "2026-11-23", "close": 35004, "change_pct": 0.42},
        {"date": "2026-11-24", "close": 34550, "change_pct": -0.89},
        {"date": "2026-11-25", "close": 34715, "change_pct": -0.41},
        {"date": "2026-11-26", "close": 34532, "change_pct": -0.94},
        {"date": "2026-11-27", "close": 34507, "change_pct": -1.01},
    ],
}

# 1-1 — training()을 core()(확정 일봉 → 결과, 네트워크·현재 시각 없음)와 training()(raw → core)으로 분리
class CoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = predrig()
        self.data = frame(220)
        self.p.raw = lambda symbol: self.data.copy()

    def tearDown(self) -> None:
        self.p._executor.shutdown(wait=True)

    # 기록한 출력과 같음 (가격 ±1원, 등락률 ±0.01%p, 지표 ±0.0001 — 반올림 경계만 허용)
    def testsnapshot(self):
        out = self.p.training("005930", epochs=2)
        self.assertEqual(out["base_close"], _SNAP["base_close"])
        self.assertEqual(out["base_date"], _SNAP["base_date"])
        self.assertEqual(set(out["metrics"]), set(_SNAP["metrics"]))
        self.assertEqual(out["metrics"]["n_val"], _SNAP["metrics"]["n_val"])
        for k in ("mae", "mae_zero", "skill"):
            self.assertAlmostEqual(out["metrics"][k], _SNAP["metrics"][k], delta=1e-4, msg=k)
        self.assertEqual([d["date"] for d in out["predictions"]], [d["date"] for d in _SNAP["predictions"]])
        for got, want in zip(out["predictions"], _SNAP["predictions"]):
            self.assertEqual(set(got), {"date", "close", "change_pct"})
            self.assertAlmostEqual(got["close"], want["close"], delta=1, msg=f"{got['date']} close")
            self.assertAlmostEqual(got["change_pct"], want["change_pct"], delta=0.01, msg=f"{got['date']} change_pct")

    # training(symbol) == core(raw(symbol), seed=codeseed(symbol))
    def testparity(self):
        out = self.p.training("005930", epochs=1)
        alt = self.p.core(self.data.copy(), seed=predict_module.codeseed("005930"), epochs=1)
        self.assertEqual(out, alt)
        self.assertEqual(predict_module.codeseed("5930"), predict_module.codeseed("005930"))

    # core()는 raw()·현재 시각·데이터 공급자를 쓰지 않고, 같은 df·seed면 같은 결과
    def testoffline(self):
        def tripwire(*_a, **_k):
            raise AssertionError("core()가 raw()를 부름")

        class NoClock:
            @classmethod
            def now(cls):
                raise AssertionError("core()가 현재 시각을 읽음")

        self.p.raw = tripwire
        # 공급자 임포트 차단 — 두 키만 바꾸고 되돌림 (patch.dict는 학습 중 새로 임포트된 torch 모듈까지 지워 재등록 오류)
        names = ("FinanceDataReader", "yfinance")
        saved = {k: sys.modules[k] for k in names if k in sys.modules}
        for k in names:
            sys.modules[k] = None
        try:
            with mock.patch.object(predict_module, "datetime", NoClock):
                first = self.p.core(self.data.copy(), seed=11, epochs=1)
                second = self.p.core(self.data.copy(), seed=11, epochs=1)
        finally:
            for k in names:
                sys.modules.pop(k, None)
            sys.modules.update(saved)
        self.assertEqual(first, second)
        self.assertEqual(first["base_date"], "2026-11-20")

    # core()도 200행 미만이면 학습하지 않음 (배포와 같은 규칙)
    def testshort(self):
        with mock.patch.object(predict_module, "StockTransformer", HistoryTest.tripwire):
            with self.assertRaises(ValueError) as ctx:
                self.p.core(frame(199), seed=0, epochs=1)
        self.assertIn("학습 데이터 부족", str(ctx.exception))

# 2-2·2-5 — 미래가 새지 않음: 컷 이후 행을 바꿔도 학습 구간의 피처·타깃·표준화 통계가 그대로
class LeakTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = predrig()

    def tearDown(self) -> None:
        self.p._executor.shutdown(wait=True)

    # core()가 학습에 넘기는 첫 데이터셋(학습 구간 X·y)은 같고, 검증 데이터셋 입력은 달라짐
    def testscaler(self):
        import numpy as np

        seen: list = []
        real = predict_module.StockDataset

        def recorder(X, y):
            seen.append((np.array(X, copy=True), np.array(y, copy=True)))
            return real(X, y)

        with mock.patch.object(predict_module, "StockDataset", recorder):
            self.p.core(frame(220), seed=5, epochs=1)
            half = len(seen)
            self.p.core(mutant(frame(220)), seed=5, epochs=1)
        self.assertEqual(len(seen), 2 * half)
        np.testing.assert_array_equal(seen[0][0], seen[half][0])
        np.testing.assert_array_equal(seen[0][1], seen[half][1])
        self.assertFalse(np.array_equal(seen[1][0], seen[half + 1][0]))

    # samples() — 학습 텐서·통계가 같고, 통계는 학습 윈도우의 입력 행·타깃으로만 계산, 컷 = 189번째 행 날짜
    def testcut(self):
        import numpy as np

        df = frame(220)
        a = predict_module.samples(df, 20, 5)
        b = predict_module.samples(mutant(df), 20, 5)
        for key in ("xt", "yt"):
            np.testing.assert_array_equal(a[key], b[key])
        for key in ("mu", "sd", "ysd"):
            np.testing.assert_array_equal(a["stats"][key], b["stats"][key])
        self.assertFalse(np.array_equal(a["xv"], b["xv"]))
        self.assertEqual(a["cut"], df.index[189])
        rows = a["feats"].to_numpy()[: a["ends"][a["ntr"] - 1] + 1]
        want = predict_module.moments(rows, a["y"][: a["ntr"]])
        for key in ("mu", "sd", "ysd"):
            np.testing.assert_allclose(a["stats"][key], want[key])

# 2-2 — purge: 학습 윈도우의 타깃 마지막 날이 컷(검증 첫 윈도우의 기준일)보다 앞섬, 경계 윈도우를 예측 기간(5개)만큼 버림
class PurgeTest(unittest.TestCase):
    def testpurge(self):
        s = predict_module.samples(frame(220), 20, 5)
        dates, ends = s["feats"].index, s["ends"]
        self.assertEqual((len(ends), s["ntr"], s["v0"]), (176, 145, 150))
        self.assertLess(dates[ends[s["ntr"] - 1] + 5], s["cut"])
        self.assertGreaterEqual(dates[ends[s["ntr"]] + 5], s["cut"])
        self.assertEqual(s["cut"], dates[ends[s["v0"]]])
        self.assertEqual((len(s["xt"]), len(s["yt"]), len(s["xv"]), len(s["yv"])), (145, 145, 26, 26))

    # 검증은 뒤쪽 15%(최소 20윈도우) — 학습 윈도우가 10개 미만이면 ValueError
    def testholdout(self):
        self.assertEqual(predict_module.holdout(176, 5), (145, 150))
        self.assertEqual(predict_module.holdout(100, 5), (75, 80))
        self.assertEqual(predict_module.holdout(35, 5), (10, 15))
        with self.assertRaises(ValueError):
            predict_module.holdout(34, 5)

# 2-2 — 타깃: 윈도우 마지막 종가 대비 1~5일 뒤 종가의 누적 로그수익률(5차원), 입력은 끝 행까지 20행
class WindowTest(unittest.TestCase):
    def testtarget(self):
        import numpy as np

        df = frame(60)
        feats = predict_module.features(df)
        X, y, ends = predict_module.windows(feats, df["Close"], 20, 5)
        self.assertEqual((X.shape, y.shape, len(ends)), ((16, 20, 7), (16, 5), 16))
        c = df["Close"].to_numpy()[20:]
        for i in (0, 7, 15):
            e = ends[i]
            np.testing.assert_allclose(X[i], feats.to_numpy()[e - 19: e + 1])
            np.testing.assert_allclose(y[i], np.log(c[e + 1: e + 6] / c[e]))

# 2-2 — 검증 손실은 샘플 수로 가중한 평균 (배치 평균의 평균이 아님, M-09)
class LossTest(unittest.TestCase):
    def testweighted(self):
        import numpy as np
        import torch

        rng = np.random.default_rng(3)
        X = rng.normal(size=(33, 1, 5))
        y = rng.normal(size=(33, 5))
        loader = predict_module.DataLoader(predict_module.StockDataset(X, y), batch_size=32)
        criterion = torch.nn.HuberLoss()
        got = predict_module.valloss(torch.nn.Flatten(), loader, criterion)
        want = criterion(torch.tensor(X, dtype=torch.float32).flatten(1), torch.tensor(y, dtype=torch.float32)).item()
        self.assertAlmostEqual(got, want, places=5)

# 2-2 — 에폭 수는 검증으로 고르고, 전체 윈도우(학습+검증)로 그 에폭 수만큼 같은 시드로 다시 학습해 예측
class RefitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = predrig()

    def tearDown(self) -> None:
        self.p._executor.shutdown(wait=True)

    # 데이터셋 크기 — 학습 145 · 검증 26 · 재학습 전체 176 (purge한 5개도 재학습에는 들어감)
    def testsizes(self):
        sizes: list[int] = []
        real = predict_module.StockDataset

        def recorder(X, y):
            sizes.append(len(X))
            return real(X, y)

        with mock.patch.object(predict_module, "StockDataset", recorder):
            self.p.core(frame(220), seed=5, epochs=1)
        self.assertEqual(sizes, [145, 26, 176])

    # 재학습 에폭 수 = 검증 손실이 가장 낮았던 에폭 (최대 3에폭, patience 8이라 조기 종료 없음)
    def testrounds(self):
        passes: list[int] = []
        losses: list[float] = []
        epoch, valloss = predict_module.epoch, predict_module.valloss

        def counter(model, loader, *args):
            passes.append(len(loader.dataset))
            return epoch(model, loader, *args)

        def meter(*args):
            value = valloss(*args)
            losses.append(value)
            return value

        with mock.patch.object(predict_module, "epoch", counter), \
                mock.patch.object(predict_module, "valloss", meter):
            self.p.core(frame(220), seed=5, epochs=3)
        self.assertEqual(len(losses), 3)
        self.assertEqual(passes.count(145), 3)
        self.assertEqual(passes.count(176), losses.index(min(losses)) + 1)

    # 지표는 재학습 전 모델, 예측은 재학습 모델 — 재학습 모델이 0을 내면 지표는 그대로, 종가 = 기준가
    def testbefore(self):
        import torch

        class Zero(torch.nn.Module):
            def forward(self, x):
                return torch.zeros(len(x), 5)

        plain = self.p.core(frame(220), seed=5, epochs=1)
        with mock.patch.object(predict_module, "fullmodel", lambda *a, **k: Zero()):
            flat = self.p.core(frame(220), seed=5, epochs=1)
        self.assertEqual(flat["metrics"], plain["metrics"])
        self.assertEqual([d["close"] for d in flat["predictions"]], [flat["base_close"]] * 5)
        self.assertEqual([d["change_pct"] for d in flat["predictions"]], [0.0] * 5)

# 2-3 — 설정 고정 (지시서 표): 작은 Transformer·시간축 평균 풀링·Huber·AdamW·배치 32
class ConfigTest(unittest.TestCase):
    # 표의 값 그대로 (일봉 조회 365일은 raw() 그대로)
    def testspec(self):
        self.assertEqual(predict_module.SPEC, {
            "seq": 20, "horizon": 5, "d_model": 32, "nhead": 4, "layers": 2, "ff": 128, "dropout": 0.1,
            "lr": 1e-3, "decay": 1e-2, "batch": 32, "epochs": 60, "patience": 8, "val": 0.15, "valmin": 20,
        })

    # d_model 32 · 헤드 4 · 2층 · FFN 128 · dropout 0.1, Flatten 없이 시간축 평균 → Linear(32→5)
    def testmodel(self):
        import torch

        m = predict_module.StockTransformer(7, 5, 20)
        layers = m.transformer_encoder.layers
        self.assertEqual(m.embedding.out_features, 32)
        self.assertEqual(len(layers), 2)
        self.assertEqual(layers[0].self_attn.num_heads, 4)
        self.assertEqual(layers[0].linear1.out_features, 128)
        self.assertAlmostEqual(layers[0].dropout.p, 0.1)
        self.assertFalse(any(isinstance(x, torch.nn.Flatten) for x in m.modules()))
        self.assertIsInstance(m.fc, torch.nn.Linear)
        self.assertEqual((m.fc.in_features, m.fc.out_features), (32, 5))
        m.eval()
        x = torch.randn(3, 20, 7)
        with torch.no_grad():
            h = m.positional_encoding(m.embedding(x).permute(1, 0, 2)).permute(1, 0, 2)
            torch.testing.assert_close(m(x), m.fc(m.transformer_encoder(h).mean(dim=1)))

    # core()는 AdamW(lr 1e-3, weight_decay 1e-2)·HuberLoss·배치 32로 학습
    def testoptim(self):
        import torch

        seen: dict = {"adamw": [], "huber": 0, "batch": []}
        adamw, huber, loader = torch.optim.AdamW, torch.nn.HuberLoss, predict_module.DataLoader

        def optim(params, **kwargs):
            seen["adamw"].append(kwargs)
            return adamw(params, **kwargs)

        def loss(*args, **kwargs):
            seen["huber"] += 1
            return huber(*args, **kwargs)

        def batches(ds, **kwargs):
            seen["batch"].append(kwargs.get("batch_size"))
            return loader(ds, **kwargs)

        p = predrig()
        with mock.patch.object(torch.optim, "AdamW", optim), \
                mock.patch.object(torch.nn, "HuberLoss", loss), \
                mock.patch.object(predict_module, "DataLoader", batches):
            p.core(frame(220), seed=5, epochs=1)
        p._executor.shutdown(wait=True)
        self.assertTrue(seen["adamw"])
        self.assertTrue(all(k == {"lr": 1e-3, "weight_decay": 1e-2} for k in seen["adamw"]), seen["adamw"])
        self.assertGreater(seen["huber"], 0)
        self.assertEqual(set(seen["batch"]), {32})

# 2-4 — 예측 날짜는 mkt()로 센 다음 5개 개장일 (주말·휴장일 건너뜀, bdate_range 아님)
class DateTest(unittest.TestCase):
    def testholiday(self):
        cases = [
            ("2026-12-22", ["2026-12-23", "2026-12-24", "2026-12-28", "2026-12-29", "2026-12-30"]),  # 성탄절·주말
            ("2026-06-01", ["2026-06-02", "2026-06-04", "2026-06-05", "2026-06-08", "2026-06-09"]),  # 지방선거
        ]
        p = predrig()
        for end, want in cases:
            with self.subTest(end=end):
                out = p.core(frame(220, end=end), seed=1, epochs=1)
                self.assertEqual(out["base_date"], end)
                self.assertEqual([d["date"] for d in out["predictions"]], want)
        p._executor.shutdown(wait=True)

    # sessions() — 추석 연휴(9/24·25)와 주말을 건너뛴 다음 5개 개장일
    def testsessions(self):
        from datetime import date

        self.assertEqual(predict_module.sessions(date(2026, 9, 23), 5),
                         [date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)])

# 2-4 — 지표: 검증 구간 5일 누적 수익률(%) 기준 모델 MAE·0% 예측 MAE·skill·표본 수 (accuracy_pct 없음)
class MetricTest(unittest.TestCase):
    # 손 계산 — |1−2|·|−1−0|·|2+2| → MAE 2, 0% 예측 MAE (2+0+2)/3, skill 1 − 2/(4/3) = −0.5
    def testhand(self):
        self.assertEqual(predict_module.scorecard([1.0, -1.0, 2.0], [2.0, 0.0, -2.0]),
                         {"mae": 2.0, "mae_zero": 1.3333, "skill": -0.5, "n_val": 3})

    # 실제 수익률이 모두 0이면 skill을 계산하지 않음 (None)
    def testflat(self):
        self.assertEqual(predict_module.scorecard([0.5, -0.5], [0.0, 0.0]),
                         {"mae": 0.5, "mae_zero": 0.0, "skill": None, "n_val": 2})

# 2-4 — 출력: 5개 {date, close, change_pct}, close = round(base_close × exp(누적 로그수익률)), OHLC·accuracy_pct 없음
class OutputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        p = predrig()
        cls.out = p.core(frame(220), seed=3, epochs=1)
        p._executor.shutdown(wait=True)

    # 5개 항목, 키는 date·close·change_pct뿐, change_pct = 기준가 대비 % (소수 둘째 자리, brief()와 같은 계산)
    def testkeys(self):
        preds, base = self.out["predictions"], self.out["base_close"]
        self.assertEqual(len(preds), 5)
        for item in preds:
            self.assertEqual(set(item), {"date", "close", "change_pct"})
            self.assertIs(type(item["close"]), int)
            self.assertEqual(item["change_pct"], round((item["close"] - base) / base * 100, 2))

    # 지표 키는 mae·mae_zero·skill·n_val, n_val = 검증 윈도우 26개, skill = 1 − mae/mae_zero
    def testmetrics(self):
        m = self.out["metrics"]
        self.assertEqual(set(m), {"mae", "mae_zero", "skill", "n_val"})
        self.assertEqual(m["n_val"], 26)
        self.assertAlmostEqual(m["skill"], 1 - m["mae"] / m["mae_zero"], delta=1e-3)

    # curve() — close = round(base × exp(r)), 날짜는 기준일 다음 개장일부터
    def testcurve(self):
        import math
        from datetime import date

        rets = [0.01, -0.02, 0.0, 0.03, -0.005]
        out = predict_module.curve(50_000, date(2026, 11, 20), rets)
        self.assertEqual([d["date"] for d in out],
                         ["2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"])
        for item, r in zip(out, rets):
            self.assertEqual(item["close"], round(50_000 * math.exp(r)))
            self.assertEqual(item["change_pct"], round((item["close"] - 50_000) / 50_000 * 100, 2))

# 3-7 — FDR 일봉 조회 한도: 별도 데몬 스레드에서 돌리고 한도를 넘기면 버림(예측 실패), 버린 스레드가 나중에 끝나도 무해
class TimeboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.p = predrig()
        self.gate = threading.Event()
        self.finished = threading.Event()
        self.workers: list[threading.Thread] = []
        self.yahoo: list[str] = []
        self.hooks: list = []
        # 어떤 결과로 끝나도 막힌 가짜 조회를 풀어 줌 (버린 스레드가 테스트 뒤에 남지 않게)
        self.addCleanup(self.gate.set)

    # 막히는 가짜 FDR — blocked 종목은 gate가 열릴 때까지 막힘(안전 한도 5초: 구현이 그냥 기다리면 테스트 시간 초과로 실패)
    # 풀린 뒤 fault가 있으면 예외, 없으면 일봉. 조회를 돌린 스레드를 기록
    def stall(self, blocked=("005930",), fault=None):
        def reader(symbol, *a, **k):
            self.workers.append(threading.current_thread())
            if symbol in blocked:
                self.gate.wait(5)
                self.finished.set()
                if fault is not None:
                    raise fault
            return frame(220)
        return reader

    # 가짜 공급자 모듈 — FDR은 reader, yfinance는 부른 티커만 기록하고 yahoo 종목(.KS 등)에만 일봉
    def suppliers(self, reader, yahoo=()):
        import pandas as pd
        record = self.yahoo

        class Ticker:
            def __init__(self, ticker):
                record.append(ticker)
                self.ticker = ticker

            def history(self, **k):
                return frame(220) if self.ticker in yahoo else pd.DataFrame()

        return {"FinanceDataReader": types.SimpleNamespace(DataReader=reader),
                "yfinance": types.SimpleNamespace(Ticker=Ticker)}

    # 한도를 넘기면 제시간에 ValueError(데이터 수집 실패) — yfinance로 넘어가지 않음(예측 실패로 처리), 조회는 별도 데몬 스레드
    def testoverrun(self):
        with mock.patch.dict(sys.modules, self.suppliers(self.stall())):
            t0 = time.monotonic()
            with self.assertRaises(ValueError) as ctx:
                self.p.raw("005930", wait=0.2)
            spent = time.monotonic() - t0
        self.assertGreaterEqual(spent, 0.2)
        self.assertLess(spent, 2.0)
        self.assertIn("데이터 수집 실패", str(ctx.exception))
        self.assertEqual(self.yahoo, [])
        self.assertFalse(self.finished.is_set())
        self.assertEqual(len(self.workers), 1)
        self.assertIsNot(self.workers[0], threading.current_thread())
        self.assertTrue(self.workers[0].daemon)

    # 버린 스레드가 나중에 끝나도 무해 — 공급자 패치가 풀린 뒤 늦은 값·늦은 예외가 나와도 밖으로 새지 않고(스레드 예외 훅 호출 없음), 다음 조회는 제 값
    def testlatecomer(self):
        for fault in (None, RuntimeError("늦은 실패")):
            with self.subTest(fault=fault):
                self.gate.clear()
                self.finished.clear()
                self.workers.clear()
                with mock.patch.object(threading, "excepthook", self.hooks.append):
                    with mock.patch.dict(sys.modules, self.suppliers(self.stall(fault=fault))):
                        with self.assertRaises(ValueError):
                            self.p.raw("005930", wait=0.1)
                    self.gate.set()
                    self.assertTrue(self.finished.wait(2))
                    self.workers[0].join(2)
                    self.assertFalse(self.workers[0].is_alive())
                    with mock.patch.dict(sys.modules, self.suppliers(self.stall(blocked=()))):
                        out = self.p.raw("005930", wait=2)
                self.assertEqual(self.hooks, [])
                self.assertEqual(len(out), 220)

    # 한도 안에 끝난 조회는 그대로 — FDR 예외는 기존대로 yfinance 폴백(.KS), 한도 안의 FDR 값은 그대로 반환
    def testfallback(self):
        def broken(*a, **k):
            raise RuntimeError("FDR 오류")

        with mock.patch.dict(sys.modules, self.suppliers(broken, yahoo=("005930.KS",))):
            out = self.p.raw("005930", wait=2)
        self.assertEqual((len(out), self.yahoo), (220, ["005930.KS"]))
        self.yahoo.clear()
        with mock.patch.dict(sys.modules, self.suppliers(self.stall(blocked=()))):
            out = self.p.raw("005930", wait=2)
        self.assertEqual((len(out), self.yahoo), (220, []))

    # 하나뿐인 학습 스레드가 묶이지 않음 — 막힌 조회로 첫 예측이 한도(_FDR_WAIT) 뒤 실패하면 같은 executor의 다음 예측이 바로 진행
    def testexecutor(self):
        self.p.bundle = lambda: None
        self.p.core = lambda df, **k: {"predictions": [], "metrics": {}, "base_close": len(df),
                                       "base_date": "2026-11-20", "source": "per_symbol", "validated": False,
                                       "spec": predict_module.VERSION}

        async def pair():
            with self.assertRaises(ValueError):
                await asyncio.wait_for(self.p.forecast("005930"), 3)
            return await asyncio.wait_for(self.p.forecast("000660"), 3)

        with mock.patch.dict(sys.modules, self.suppliers(self.stall())), \
                mock.patch.object(predict_module, "_FDR_WAIT", 0.2):
            out = asyncio.run(pair())
        self.assertEqual(out["base_close"], 220)
        self.assertFalse(self.finished.is_set())

if __name__ == "__main__":
    unittest.main()
