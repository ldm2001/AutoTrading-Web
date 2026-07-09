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
def build() -> TestClient:
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(ai_module.router)
    app.include_router(predict_module.router)
    return TestClient(app)


class CodeValidationTest(unittest.TestCase):
    # 종목코드 형식이 아니면 핸들러 진입 전 422
    def test_invalid_rejected(self):
        client = build()
        for path in (
            "/api/ai/signal/AAPL",
            "/api/ai/news/0059301",
            "/api/ai/indicators/00593",
            "/api/predict/AAPL",
        ):
            self.assertEqual(client.get(path).status_code, 422, path)

    # 유효 코드는 통과하고 predict는 zfill 유지
    def test_valid_passes(self):
        client = build()

        async def fake(code):
            return {"predictions": [], "metrics": {"mae": 0}}

        with mock.patch.object(predict_module, "predict_stock", fake):
            resp = client.get("/api/predict/5930")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["code"], "005930")


if __name__ == "__main__":
    unittest.main()
