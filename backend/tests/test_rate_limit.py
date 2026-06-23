import asyncio
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from config import settings
from api.auth import guard
from api.security import csrfok
import api.trade as trade_module
from service.trading.bot import bot


# guard 직접 호출용 요청 스텁
class _Req:
    client = None


# bot.start 대체 no-op (실제 태스크 기동 방지)
async def _noop_start() -> None:
    return None


# FR-04/05/06 — limiter 배선 + 조건부 매매 라우터 + csrf 게이트
class RateLimitAuthTest(unittest.TestCase):
    # api_key/bot 상태 보존 후 start를 no-op으로 교체
    def setUp(self) -> None:
        self._api_key = settings.api_key
        self._bot_start = bot.start
        self._bot_running = bot.running
        bot.start = _noop_start
        bot.running = False

    # 교체했던 전역/인스턴스 상태 복원
    def tearDown(self) -> None:
        settings.api_key = self._api_key
        bot.start = self._bot_start
        bot.running = self._bot_running

    # T6 — 레이트리밋 초과 시 500이 아닌 429 (app.state.limiter 배선 검증)
    def test_t6(self):
        settings.api_key = "testkey"
        app = main.appfactory()
        client = TestClient(app, raise_server_exceptions=False)
        headers = {"X-API-Key": "testkey", "Origin": "http://127.0.0.1:5173"}

        statuses = [
            client.post("/api/trading/bot/start", headers=headers).status_code
            for _ in range(5)
        ]

        self.assertIn(429, statuses)
        self.assertNotIn(500, statuses)

    # T7 — api_key 미설정 시 매매 라우터 미등록(404) + health 플래그 + guard 503
    def test_t7(self):
        settings.api_key = ""
        app = main.appfactory()
        client = TestClient(app, raise_server_exceptions=False)

        self.assertEqual(client.get("/api/trading/bot/status").status_code, 404)
        self.assertFalse(client.get("/api/health").json()["trading_api"])

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(guard(_Req(), None))
        self.assertEqual(raised.exception.status_code, 503)

    # T8 — csrfok: Origin 부재 시 X-API-Key 헤더 필수 (값 검증은 guard 담당)
    def test_t8(self):
        self.assertFalse(csrfok(None, None))
        self.assertTrue(csrfok(None, "any-key"))
        self.assertTrue(csrfok("http://127.0.0.1:5173", None))
        self.assertFalse(csrfok("http://evil.example", None))

        settings.api_key = "testkey"
        app = main.appfactory()
        client = TestClient(app, raise_server_exceptions=False)

        # Origin 無 + 키 헤더 無 → 미들웨어 단계에서 403
        blocked = client.put("/api/trading/watchlist", json={"codes": []})
        self.assertEqual(blocked.status_code, 403)

        # 키 헤더 有 → 미들웨어 통과, 라우트 도달 (200)
        saved = trade_module.save_watchlist
        trade_module.save_watchlist = lambda codes: None
        try:
            reached = client.put(
                "/api/trading/watchlist",
                json={"codes": ["005930"]},
                headers={"X-API-Key": "testkey"},
            )
        finally:
            trade_module.save_watchlist = saved
        self.assertNotEqual(reached.status_code, 403)
        self.assertEqual(reached.status_code, 200)


if __name__ == "__main__":
    unittest.main()
