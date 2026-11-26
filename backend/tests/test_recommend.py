import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.stock as stock_api
from config import settings

CODES = ["005930", "000660", "035420"]

# 캐시에 들어 있던 예측 — 5일 모두 100,000원 (2단계 형식 — 예측 {date, close, change_pct}, 지표 {mae, mae_zero, skill, n_val})
PRED = {
    "predictions": [
        {"date": f"2026-11-{d}", "close": 100_000, "change_pct": 0.0}
        for d in (23, 24, 25, 26, 27)
    ],
    "metrics": {"mae": 0.05, "mae_zero": 0.05, "skill": 0.0, "n_val": 20},
    "base_close": 100_000,
    "base_date": "2026-11-20",
}


# 추천 모듈 전역 초기화 — 테스트 사이 캐시·세대·작업 누수 방지
def blank() -> None:
    stock_api._stage1_cache = None
    stock_api._stage2_cache = None
    stock_api._generation = 0
    stock_api._stage1_job = None
    stock_api._stage2_job = None
    stock_api._stage2_gen = 0


# 추천 공통 스텁 — verdict·예측·학습 호출 기록 (네트워크 없음)
class RecommendBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        blank()
        self._flag = settings.use_prediction
        settings.use_prediction = False
        self.evals: list[tuple[str, dict | None, bool]] = []
        self.preds: list[str] = []
        self.fits: list[str] = []
        self.cache: dict[str, dict] = {}
        self._patches = [
            mock.patch.object(stock_api, "_SCAN_CODES", CODES),
            mock.patch.object(stock_api, "verdict", self.verdict),
            mock.patch.object(stock_api, "forecast", self.forecast),
            mock.patch.object(stock_api.predictor, "cached", self.cached),
            mock.patch.object(stock_api.predictor, "forecast", self.forecast),
            mock.patch.object(stock_api.predictor, "training", self.training),
        ]
        for p in self._patches:
            p.start()

    # 남은 1·2단계 작업 정리
    async def asyncTearDown(self) -> None:
        for job in (stock_api._stage1_job, stock_api._stage2_job):
            if job is not None and not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        settings.use_prediction = self._flag
        blank()

    # 가짜 verdict — 0이 아닌 점수 (1단계 필터 통과)
    async def verdict(self, code: str, prediction: dict | None = None, fast: bool = False) -> dict:
        self.evals.append((code, prediction, fast))
        return {
            "signal": "hold",
            "score": 10.0 + CODES.index(code),
            "price": 105_000,
            "summary": "관망",
            "factors": [],
        }

    async def forecast(self, code: str) -> dict:
        self.preds.append(code)
        return PRED

    def training(self, symbol: str, *_args, **_kw) -> dict:
        self.fits.append(symbol)
        return PRED

    def cached(self, symbol: str) -> dict | None:
        return self.cache.get(symbol.zfill(6))

    # 1단계 응답 후 2단계 작업 완료까지 대기
    async def settle(self) -> dict:
        body = await stock_api.recommend()
        job = stock_api._stage2_job
        if job is not None:
            await asyncio.wait_for(job, 3)
        return body

    def stage(self, fast: bool) -> list[tuple[str, dict | None, bool]]:
        return [e for e in self.evals if e[2] is fast]


# M-04·S-10 — 검증 전까지 추천 경로의 Direction 끄기
class RecommendDirectionTest(RecommendBase):
    # 플래그 False: 2단계 완료까지 예측·학습 호출 0회, 2단계는 prediction=None 전체 평가로만 보강
    async def testflagoff(self):
        await self.settle()
        body = await stock_api.recommend()
        self.assertEqual(body["stage"], "enhanced")
        self.assertEqual(self.preds, [])
        self.assertEqual(self.fits, [])
        full = self.stage(False)
        self.assertEqual(sorted(c for c, _, _ in full), sorted(CODES))
        self.assertTrue(all(p is None for _, p, _ in full), full)

    # 캐시에 예측이 있어도 1단계 점수는 prediction=None — 배지는 캐시로 표시 (표시 전용)
    async def teststage1(self):
        self.cache = {"005930": PRED}
        body = await self.settle()
        fast = self.stage(True)
        self.assertEqual(len(fast), len(CODES))
        self.assertTrue(all(p is None for _, p, _ in fast), fast)
        badge = next(i for i in body["items"] if i["code"] == "005930")
        self.assertIsNotNone(badge["prediction"])
        enhanced = await stock_api.recommend()
        self.assertEqual(enhanced["stage"], "enhanced")
        badge = next(i for i in enhanced["items"] if i["code"] == "005930")
        self.assertIsNotNone(badge["prediction"])
        self.assertTrue(all(p is None for _, p, _ in self.stage(False)))

    # 플래그 True: 1단계는 여전히 None, 2단계는 지금처럼 학습 결과로 평가
    async def testflagon(self):
        settings.use_prediction = True
        self.cache = {"005930": PRED}
        await self.settle()
        self.assertTrue(all(p is None for _, p, _ in self.stage(True)))
        self.assertEqual(sorted(self.preds), sorted(CODES))
        self.assertTrue(all(p is PRED for _, p, _ in self.stage(False)))


# M-01 — 추천 배지 등락률은 예측이 출발한 기준 종가 대비, 기준가·기준일 필드 추가 (기존 필드 유지)
class BriefTest(unittest.TestCase):
    # 평평한 예측 + 현재가 105,000 → 0% 보합
    def testflat(self):
        self.assertEqual(stock_api.brief(105_000, PRED), {
            "current_price": 105_000,
            "predicted_5d": 100_000,
            "change_pct": 0.0,
            "trend": "보합",
            "base_close": 100_000,
            "base_date": "2026-11-20",
        })

    # 기준가 대비 +4% 예측은 현재가와 무관하게 +4% 상승
    def testrise(self):
        pred = dict(PRED, predictions=[dict(c, close=104_000, change_pct=4.0) for c in PRED["predictions"]])
        for price in (100_000, 104_000, 110_000):
            with self.subTest(price=price):
                out = stock_api.brief(price, pred)
                self.assertEqual((out["change_pct"], out["trend"]), (4.0, "상승"))

    # 기준가 없는 옛 형식 예측은 배지 없음
    def testnobase(self):
        old = {k: v for k, v in PRED.items() if k not in ("base_close", "base_date")}
        self.assertIsNone(stock_api.brief(105_000, old))


# S-02 — 추천 응답의 갱신 필드(loading·refreshing), 2단계가 통째로 실패한 세대는 다시 시작하지 않음
class RefreshTest(RecommendBase):
    # 2단계 평가 점수 None → 정렬 TypeError로 2단계가 통째로 실패 (2단계 캐시 미기록)
    async def glitch(self, code: str, prediction: dict | None = None, fast: bool = False) -> dict:
        out = await self.verdict(code, prediction, fast)
        if not fast and code == CODES[0]:
            out["score"] = None
        return out

    # 실패한 세대: 이후 호출(5초 재조회 포함)이 2단계를 재시작하지 않고 enhancing·refreshing False
    async def testfailure(self):
        with mock.patch.object(stock_api, "verdict", self.glitch):
            with self.assertLogs(stock_api.logger, "ERROR"):
                await self.settle()
            for _ in range(3):
                body = await self.settle()
        self.assertEqual(len(self.stage(False)), len(CODES))
        self.assertIsNone(stock_api._stage2_job)
        self.assertEqual(body["stage"], "screened")
        self.assertFalse(body["enhancing"])
        self.assertFalse(body["refreshing"])
        self.assertFalse(body["loading"])

    # 새 세대(1단계 만료 뒤 재스크리닝)는 2단계를 다시 시작 — 막는 건 실패한 그 세대뿐
    async def testgeneration(self):
        with mock.patch.object(stock_api, "verdict", self.glitch):
            with self.assertLogs(stock_api.logger, "ERROR"):
                await self.settle()
            stock_api._stage1_cache = (0, stock_api._stage1_cache[1])
            with self.assertLogs(stock_api.logger, "ERROR"):
                body = await self.settle()
        self.assertEqual(stock_api._generation, 2)
        self.assertEqual(len(self.stage(False)), 2 * len(CODES))
        self.assertTrue(body["enhancing"])

    # 2단계 진행 중 refreshing True(= enhancing), 완료 뒤 enhanced에서 False — loading은 항상 False
    async def testfields(self):
        body = await stock_api.recommend()
        self.assertEqual((body["stage"], body["loading"], body["refreshing"]), ("screened", False, True))
        self.assertEqual(body["refreshing"], body["enhancing"])
        await asyncio.wait_for(stock_api._stage2_job, 3)
        body = await stock_api.recommend()
        self.assertEqual(
            (body["stage"], body["loading"], body["refreshing"], body["enhancing"]),
            ("enhanced", False, False, False),
        )
        self.assertEqual(len(body["items"]), len(CODES))


if __name__ == "__main__":
    unittest.main()
