import asyncio
import datetime
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import HTTPException

import api.backtest as backtest_api
from api.backtest import BacktestRequest, btapi
from service.trading.backtest import BacktestResult


# 15분봉 N개 — store.span 대체
def bars(count: int = 60) -> list[dict]:
    base = datetime.datetime(2026, 3, 2, 9, 0)
    return [
        {
            "time": base + datetime.timedelta(minutes=15 * i),
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000,
        }
        for i in range(count)
    ]


# FDR 일봉 프레임 N행 — DataReader 대체
def frame(count: int = 45) -> pd.DataFrame:
    idx = pd.bdate_range("2026-01-01", periods=count)
    return pd.DataFrame(
        {
            "Open": [100] * count, "High": [102] * count, "Low": [98] * count,
            "Close": [100] * count, "Volume": [1_000_000] * count,
        },
        index=idx,
    )


# 조건이 참이 될 때까지 루프를 양보하며 대기 (시간 초과 시 False)
async def until(pred, timeout: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not pred():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


# 요청 본문 — 검증 생략(가짜 bt 1회만)
def req(code: str = "005930", **kw) -> BacktestRequest:
    kw.setdefault("include_validation", False)
    return BacktestRequest(code=code, **kw)


# E-01 — 백테스트 계산은 전용 스레드, 동시 1건, 요청이 끊겨도 작업 유지
class BacktestApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        backtest_api._job = None
        self.gate = threading.Event()      # 가짜 bt를 막는 문
        self.entered = threading.Event()   # 가짜 bt 진입
        self.finished = threading.Event()  # 가짜 bt 종료
        self.hold = 2.0                    # 문이 안 열릴 때 최대 대기 (버그 시 멈춤 방지)
        self.beats = 0
        self.gap: int | None = None
        self.threads: dict[str, int] = {}
        self.memos: dict[str, object] = {}
        self.rows = frame(45)
        self.span_rows = 60
        self.reader_error: Exception | None = None
        self._fdr = sys.modules.get("FinanceDataReader")
        sys.modules["FinanceDataReader"] = types.SimpleNamespace(DataReader=self.reader)
        self._patches = [
            mock.patch.object(backtest_api.store, "span", self.span),
            mock.patch.object(backtest_api, "bt", self.bt),
            mock.patch.object(backtest_api, "grid", self.grid),
            mock.patch.object(backtest_api, "wf", self.wf),
            mock.patch.object(backtest_api, "wftab", self.wftab),
        ]
        for p in self._patches:
            p.start()

    # 남은 작업을 풀어 주고 끝날 때까지 대기 (다음 테스트로 새지 않게)
    async def asyncTearDown(self) -> None:
        self.gate.set()
        job = getattr(backtest_api, "_job", None)
        if job is not None:
            await until(job.done)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        if self._fdr is None:
            sys.modules.pop("FinanceDataReader", None)
        else:
            sys.modules["FinanceDataReader"] = self._fdr
        backtest_api._job = None

    # 가짜 FDR DataReader
    def reader(self, code, start, end):
        if self.reader_error is not None:
            raise self.reader_error
        return self.rows

    # 가짜 store.span — 호출 스레드 기록
    def span(self, code, interval=15, days=365):
        self.threads["span"] = threading.get_ident()
        return bars(self.span_rows)

    # 가짜 bt — 문이 열릴 때까지 막고, 막힌 동안 하트비트 증가량 기록 (받은 점수 표도 기록)
    def bt(self, code, candles_15m, daily, cfg=None, scores=None):
        self.threads["bt"] = threading.get_ident()
        self.memos["bt"] = scores
        self.entered.set()
        before = self.beats
        self.gate.wait(timeout=self.hold)
        self.gap = self.beats - before
        self.finished.set()
        return BacktestResult(code=code, total_bars=len(candles_15m))

    def grid(self, code, candles_15m, daily, cfg, **_kw):
        self.threads["grid"] = threading.get_ident()
        self.memos["grid"] = _kw.get("scores")
        return [{"buy_threshold": cfg.buy_threshold}]

    def wf(self, code, candles_15m, daily, cfg, **_kw):
        self.threads["wf"] = threading.get_ident()
        self.memos["wf"] = _kw.get("scores")
        return [{"window": 1}]

    def wftab(self, code, data, root=None):
        self.threads["wftab"] = threading.get_ident()
        return 1

    # 계산 중에도 루프가 돈다 — 가짜 bt가 0.5초 막힌 동안 하트비트가 5 이상 증가
    async def testliveloop(self):
        self.hold = 0.5

        async def beat():
            while True:
                await asyncio.sleep(0.01)
                self.beats += 1

        hb = asyncio.create_task(beat())
        await asyncio.sleep(0.03)
        try:
            body = await btapi(req())
        finally:
            hb.cancel()
        self.assertGreaterEqual(self.gap, 5)
        self.assertNotEqual(self.threads["bt"], threading.get_ident())
        self.assertEqual(body["code"], "005930")

    # 실행 중 두 번째 요청은 429 — 첫 요청은 정상 완료
    async def testbusy(self):
        first = asyncio.create_task(btapi(req("005930")))
        self.assertTrue(await until(self.entered.is_set))
        with self.assertRaises(HTTPException) as cm:
            await btapi(req("000660"))
        self.assertEqual(cm.exception.status_code, 429)
        self.assertEqual(cm.exception.detail, "백테스트가 이미 실행 중입니다")
        self.gate.set()
        body = await asyncio.wait_for(first, 3)
        self.assertEqual(body["code"], "005930")

    # 요청 태스크를 취소해도 작업은 끝까지 돌고 그동안 429, 끝난 뒤에는 새 요청 수락
    async def testcancellation(self):
        first = asyncio.create_task(btapi(req("005930")))
        self.assertTrue(await until(self.entered.is_set))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.finished.is_set())
        with self.assertRaises(HTTPException) as cm:
            await btapi(req("000660"))
        self.assertEqual(cm.exception.status_code, 429)
        self.gate.set()
        self.assertTrue(await until(lambda: backtest_api._job.done()))
        self.assertTrue(self.finished.is_set())
        body = await asyncio.wait_for(btapi(req("000660")), 3)
        self.assertEqual(body["code"], "000660")

    # 일봉 수집 실패는 502, 실패한 작업은 슬롯을 놓는다
    async def testdayfault(self):
        self.gate.set()
        self.reader_error = RuntimeError("naver down")
        with self.assertRaises(HTTPException) as cm:
            await btapi(req())
        self.assertEqual(cm.exception.status_code, 502)
        self.assertEqual(cm.exception.detail, "일봉 데이터 수집 실패")
        self.reader_error = None
        body = await asyncio.wait_for(btapi(req()), 3)
        self.assertEqual(body["code"], "005930")

    # 일봉 35개 미만은 400
    async def testdayshort(self):
        self.gate.set()
        self.rows = frame(34)
        with self.assertRaises(HTTPException) as cm:
            await btapi(req())
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(cm.exception.detail, "일봉 데이터 부족 (34개, 최소 35개 필요)")

    # 15분봉 50개 미만은 400 — 작업을 등록하지 않는다
    async def test15mshort(self):
        self.span_rows = 49
        with self.assertRaises(HTTPException) as cm:
            await btapi(req())
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("15분봉 데이터 부족 (49개)", cm.exception.detail)
        self.assertNotIn("bt", self.threads)
        self.assertIsNone(backtest_api._job)

    # 15분봉 읽기·research 기록은 루프 스레드, 일봉·bt·grid·wf는 백테스트 스레드. 응답 형식 유지
    async def testthread(self):
        self.gate.set()
        self.assertTrue(BacktestRequest(code="005930").include_validation)
        body = await asyncio.wait_for(btapi(BacktestRequest(code="005930")), 3)
        main = threading.get_ident()
        self.assertEqual(self.threads["span"], main)
        self.assertEqual(self.threads["wftab"], main)
        for name in ("bt", "grid", "wf"):
            self.assertNotEqual(self.threads[name], main, name)
        self.assertEqual(body["validation_log_count"], 1)
        self.assertEqual(body["validation"]["walk_forward"], [{"window": 1}])
        self.assertEqual(set(body), {
            "code", "total_bars", "total_trades", "cum_return_pct", "annualized_pct",
            "mdd_pct", "win_rate_pct", "risk_reward", "buy_hold_return_pct",
            "excess_return_pct", "avg_trade_cost_bps", "validation_warnings",
            "validation", "validation_log_count", "cost_assumptions", "trades",
        })

    # 1-6 — 요청 하나의 bt·grid·wf가 같은 점수 표(dict 하나)를 같이 씀
    async def testmemo(self):
        self.gate.set()
        await asyncio.wait_for(btapi(BacktestRequest(code="005930")), 3)
        memo = self.memos.get("bt")
        self.assertIsInstance(memo, dict)
        self.assertIs(self.memos.get("grid"), memo)
        self.assertIs(self.memos.get("wf"), memo)


if __name__ == "__main__":
    unittest.main()
