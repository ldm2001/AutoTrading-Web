import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

import api.ai as ai_module
import api.predict as predict_module
from api.limiter import limiter


# 라우터만 얹은 테스트 앱
def apprig() -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(ai_module.router)
    app.include_router(predict_module.router)
    return TestClient(app)


class CodeValidationTest(unittest.TestCase):
    # 종목코드 형식이 아니면 핸들러 진입 전 422
    def testinvalid(self):
        client = apprig()
        for path in (
            "/api/ai/signal/AAPL",
            "/api/ai/news/0059301",
            "/api/ai/indicators/00593",
            "/api/predict/AAPL",
        ):
            self.assertEqual(client.get(path).status_code, 422, path)

    # 유효 코드는 통과하고 predict는 zfill 유지
    def testvalid(self):
        client = apprig()

        async def stub(code):
            return {"predictions": [], "metrics": {"mae": 0}}

        with mock.patch.object(predict_module, "forecast", stub):
            resp = client.get("/api/predict/5930")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["code"], "005930")

    # M-01 — 응답에 예측 기준가·기준일 추가 (기존 필드 유지)
    def testbase(self):
        client = apprig()

        async def stub(code):
            return {
                "predictions": [], "metrics": {"mae": 0},
                "base_close": 100_000, "base_date": "2026-11-20",
            }

        with mock.patch.object(predict_module, "forecast", stub):
            body = client.get("/api/predict/005930").json()
        self.assertEqual((body["base_close"], body["base_date"]), (100_000, "2026-11-20"))
        self.assertEqual(body["status"], "success")

    # 3-5 — 응답에 예측 출처(source)와 검증 여부(validated) 추가, 없으면 per_symbol·false (validated는 정확히 true일 때만 true)
    def testsource(self):
        client = apprig()
        cases = (({"source": "pooled", "validated": True}, ("pooled", True)),
                 ({"source": "pooled", "validated": False}, ("pooled", False)),
                 ({"source": "pooled", "validated": "true"}, ("pooled", False)),
                 ({"source": "per_symbol", "validated": False}, ("per_symbol", False)),
                 ({}, ("per_symbol", False)))
        for extra, want in cases:
            with self.subTest(extra=extra):
                # 같은 limiter 인스턴스(6/minute)를 다른 테스트와 같이 씀 — 경우마다 비워 429를 피함
                limiter.reset()

                async def stub(code, extra=extra):
                    return {"predictions": [], "metrics": {"mae": 0}, "base_close": 1, "base_date": "2026-11-20", **extra}

                with mock.patch.object(predict_module, "forecast", stub):
                    body = client.get("/api/predict/005930").json()
                self.assertEqual((body["source"], body["validated"]), want)
                self.assertIs(type(body["validated"]), bool)


if __name__ == "__main__":
    unittest.main()
